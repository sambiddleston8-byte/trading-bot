from __future__ import annotations

"""Exact sector exposure from verified point-in-time classification evidence."""

from datetime import datetime, timedelta, timezone
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance.portfolio_cash_flow import PortfolioCashFlowLedger
from core.performance.portfolio_valuation import (
    SimulatedPortfolioValuationLedger,
    _as_datetime,
    _canonical_json,
    _decimal_string,
    _fraction,
    _fraction_material,
    _record_hash,
    _write_all,
)
from core.performance.sector_classification import (
    SectorClassificationEvidenceLedger,
)


SECTOR_EXPOSURE_SCHEMA_VERSION = "1.0"
SECTOR_EXPOSURE_CALCULATION_VERSION = "post-flow-point-in-time-sector-exposure-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
FORMULA = {
    "post_flow_cash": "base_remaining_cash + cumulative_external_cash_flow",
    "post_flow_total_equity": "base_total_equity + cumulative_external_cash_flow",
    "sector_portfolio_weight": "sector_position_value / post_flow_total_equity",
    "sector_invested_weight": "sector_position_value / invested_position_value",
    "cash_policy": "CASH_REPORTED_SEPARATELY_NOT_AS_A_SECTOR",
    "classification_policy": (
        "EXACTLY_ONE_COMPLETE_RECORD_PER_POSITION_SAME_PROVIDER_TAXONOMY_VERSION"
    ),
    "label_policy": "PROVIDER_CODES_AND_LABELS_PRESERVED_WITHOUT_TRANSLATION",
    "arithmetic_policy": "EXACT_RATIONAL_WITH_34_DIGIT_DECIMAL_PRESENTATION",
}


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _result_id(
    portfolio_version: str,
    horizon: str,
    provider: str,
    taxonomy_name: str,
    taxonomy_version: str,
) -> str:
    material = [
        portfolio_version,
        horizon,
        provider,
        taxonomy_name,
        taxonomy_version,
        SECTOR_EXPOSURE_CALCULATION_VERSION,
    ]
    return "SEXP-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(
    valuation: Mapping[str, Any],
    flows: Sequence[Mapping[str, Any]],
    classifications: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    base_cash = _fraction(
        valuation.get("exact_fractions", {}).get("remaining_cash", {}),
        "base remaining cash",
    )
    base_equity = _fraction(
        valuation.get("exact_fractions", {}).get("total_equity", {}),
        "base total equity",
    )
    cumulative_flow = sum(
        (
            _fraction(item.get("exact_signed_amount", {}), "external cash flow")
            for item in flows
        ),
        Fraction(0),
    )
    cash = base_cash + cumulative_flow
    equity = base_equity + cumulative_flow
    if cash < 0:
        raise ValueError("Post-flow cash cannot be negative")
    if equity <= 0:
        raise ValueError("Post-flow total equity must be positive")

    positions = list(valuation.get("positions") or [])
    if not positions:
        raise ValueError("At least one verified invested position is required")
    tickers = [str(item.get("ticker") or "").upper() for item in positions]
    if any(not ticker for ticker in tickers) or len(set(tickers)) != len(tickers):
        raise ValueError("Verified sector-exposure positions require unique tickers")
    by_ticker = {str(item.get("ticker") or "").upper(): item for item in classifications}
    if set(by_ticker) != set(tickers) or len(by_ticker) != len(classifications):
        raise ValueError("Classification evidence must match every position exactly once")

    sector_code_to_name: dict[str, str] = {}
    sector_name_to_code: dict[str, str] = {}
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    invested_value = Fraction(0)
    position_evidence = []
    for position, ticker in zip(positions, tickers):
        value = _fraction(
            position.get("exact_fractions", {}).get("outcome_position_value", {}),
            f"{ticker} position value",
        )
        if value < 0:
            raise ValueError("Position values cannot be negative")
        classification = by_ticker[ticker]
        code = _required(classification.get("sector_code"), "sector_code")
        name = _required(classification.get("sector_name"), "sector_name")
        if code in sector_code_to_name and sector_code_to_name[code] != name:
            raise ValueError("One provider sector code cannot have conflicting names")
        if name in sector_name_to_code and sector_name_to_code[name] != code:
            raise ValueError("One provider sector name cannot have conflicting codes")
        sector_code_to_name[code] = name
        sector_name_to_code[name] = code
        bucket = grouped.setdefault(
            (code, name), {"value": Fraction(0), "tickers": []}
        )
        bucket["value"] += value
        bucket["tickers"].append(ticker)
        invested_value += value
        position_evidence.append(
            {
                "ticker": ticker,
                "position_value": _decimal_string(value),
                "exact_position_value": _fraction_material(value),
                "sector_code": code,
                "sector_name": name,
                "classification_evidence_id": classification["evidence_id"],
                "classification_record_hash": classification["record_hash"],
                "availability_at_valuation": classification[
                    "availability_at_valuation"
                ],
            }
        )
    if invested_value <= 0:
        raise ValueError("Invested position value must be positive")
    if invested_value != equity - cash:
        raise ValueError("Position, cash and total-equity values must reconcile exactly")

    sectors = []
    for (code, name), bucket in grouped.items():
        value = bucket["value"]
        portfolio_weight = value / equity
        invested_weight = value / invested_value
        sectors.append(
            {
                "sector_code": code,
                "sector_name": name,
                "tickers": sorted(bucket["tickers"]),
                "position_value": _decimal_string(value),
                "portfolio_weight": _decimal_string(portfolio_weight),
                "invested_weight": _decimal_string(invested_weight),
                "exact_fractions": {
                    "position_value": _fraction_material(value),
                    "portfolio_weight": _fraction_material(portfolio_weight),
                    "invested_weight": _fraction_material(invested_weight),
                },
            }
        )
    sectors.sort(
        key=lambda item: (
            -_fraction(item["exact_fractions"]["portfolio_weight"], "sector weight"),
            item["sector_code"],
            item["sector_name"],
        )
    )
    position_evidence.sort(key=lambda item: item["ticker"])
    cash_weight = cash / equity
    invested_weight = invested_value / equity
    portfolio_total = cash_weight + sum(
        (
            _fraction(item["exact_fractions"]["portfolio_weight"], "sector weight")
            for item in sectors
        ),
        Fraction(0),
    )
    invested_total = sum(
        (
            _fraction(item["exact_fractions"]["invested_weight"], "invested weight")
            for item in sectors
        ),
        Fraction(0),
    )
    if portfolio_total != 1 or invested_total != 1:
        raise ValueError("Sector and cash exposure weights must reconcile exactly")
    exact = {
        "base_remaining_cash": base_cash,
        "base_total_equity": base_equity,
        "cumulative_external_cash_flow": cumulative_flow,
        "post_flow_cash": cash,
        "post_flow_total_equity": equity,
        "invested_position_value": invested_value,
        "cash_weight": cash_weight,
        "invested_position_weight": invested_weight,
        "portfolio_weight_total": portfolio_total,
        "invested_sector_weight_total": invested_total,
    }
    return {
        "position_count": len(positions),
        "sector_count": len(sectors),
        "contains_backfilled_classification_evidence": any(
            item["availability_at_valuation"] == "BACKFILLED_AFTER_BOUNDARY"
            for item in classifications
        ),
        "positions": position_evidence,
        "sectors": sectors,
        **{key: _decimal_string(value) for key, value in exact.items()},
        "exact_fractions": {
            key: _fraction_material(value) for key, value in exact.items()
        },
    }


class SectorExposureLedger:
    """Append-only exact sector exposure with cash kept separate."""

    def __init__(
        self,
        path: str | Path,
        valuation_ledger: SimulatedPortfolioValuationLedger,
        cash_flow_ledger: PortfolioCashFlowLedger,
        classification_ledger: SectorClassificationEvidenceLedger,
    ) -> None:
        self.path = Path(path)
        self.valuation_ledger = valuation_ledger
        self.cash_flow_ledger = cash_flow_ledger
        self.classification_ledger = classification_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Sector-exposure ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank sector-exposure line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at sector-exposure line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Sector-exposure line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_calculable(
        portfolio_version: str, horizon: str, reasons: Sequence[str]
    ) -> dict[str, Any]:
        return {
            "status": "NOT_CALCULABLE",
            "portfolio_version": str(portfolio_version),
            "horizon": str(horizon).upper(),
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "sector_exposure_calculated": False,
            "recommendation_provided": False,
            "portfolio_return_calculated": False,
            "alpha_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
        }

    def _support(
        self,
        portfolio_version: str,
        horizon: str,
        provider: str,
        taxonomy_name: str,
        taxonomy_version: str,
    ) -> tuple[
        dict[str, Any] | None,
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[str],
    ]:
        valuations = self.valuation_ledger.verify()
        valuation = next(
            (
                item
                for item in valuations
                if item.get("portfolio_version") == portfolio_version
                and item.get("horizon") == horizon
            ),
            None,
        )
        reasons = []
        if horizon == "ENTRY":
            reasons.append("ENTRY is the funding baseline, not a valuation horizon.")
        if valuation is None:
            reasons.append("Verified portfolio valuation is missing.")
            return None, [], [], reasons
        target_at = _as_datetime(valuation["outcome_asset_price_effective_at"])
        included_valuations = {
            item["valuation_id"]: item
            for item in valuations
            if item.get("portfolio_version") == portfolio_version
            and _as_datetime(item["outcome_asset_price_effective_at"]) <= target_at
        }
        flows = sorted(
            (
                item
                for item in self.cash_flow_ledger.verify()
                if item.get("portfolio_version") == portfolio_version
                and _as_datetime(item["effective_at"]) <= target_at
            ),
            key=lambda item: _as_datetime(item["effective_at"]),
        )
        if any(item.get("valuation_id") not in included_valuations for item in flows):
            reasons.append("Every cash flow must retain an included valuation boundary.")
        identity_fields = ("strategy_version", "model_versions", "git_revision")
        if any(
            any(item.get(field) != valuation.get(field) for field in identity_fields)
            for item in flows
        ):
            reasons.append("Sector exposure must share strategy, model and Git identity.")

        all_classifications = self.classification_ledger.verify()
        selected = []
        for position in valuation.get("positions") or []:
            ticker = str(position.get("ticker") or "").upper()
            matches = [
                item
                for item in all_classifications
                if item.get("valuation_id") == valuation["valuation_id"]
                and item.get("ticker") == ticker
                and item.get("provider") == provider
                and item.get("taxonomy_name") == taxonomy_name
                and item.get("taxonomy_version") == taxonomy_version
                and item.get("completeness_status") == "COMPLETE"
            ]
            if len(matches) != 1:
                reasons.append(
                    f"{ticker} requires exactly one complete classification under the selected taxonomy."
                )
            else:
                selected.append(matches[0])
        return valuation, flows, selected, reasons

    def calculate(
        self,
        *,
        portfolio_version: str,
        horizon: str,
        provider: str,
        taxonomy_name: str,
        taxonomy_version: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        resolved_horizon = str(horizon or "").upper()
        try:
            resolved_provider = _required(provider, "provider")
            resolved_taxonomy = _required(taxonomy_name, "taxonomy_name")
            resolved_taxonomy_version = _required(
                taxonomy_version, "taxonomy_version"
            )
        except ValueError as error:
            return self.not_calculable(version, resolved_horizon, [str(error)])
        valuation, flows, classifications, reasons = self._support(
            version,
            resolved_horizon,
            resolved_provider,
            resolved_taxonomy,
            resolved_taxonomy_version,
        )
        if reasons:
            return self.not_calculable(version, resolved_horizon, reasons)
        assert valuation is not None
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest_support = max(
            [_as_datetime(valuation["calculated_at"])]
            + [_as_datetime(item["recorded_at"]) for item in flows]
            + [_as_datetime(item["recorded_at"]) for item in classifications]
        )
        if calculated < latest_support:
            return self.not_calculable(
                version,
                resolved_horizon,
                ["calculated_at cannot predate supporting sector-exposure evidence."],
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                version, resolved_horizon, ["calculated_at cannot be in the future."]
            )
        try:
            economics = _economics(valuation, flows, classifications)
        except ValueError as error:
            return self.not_calculable(version, resolved_horizon, [str(error)])
        result = {
            "schema_version": SECTOR_EXPOSURE_SCHEMA_VERSION,
            "calculation_version": SECTOR_EXPOSURE_CALCULATION_VERSION,
            "result_id": _result_id(
                version,
                resolved_horizon,
                resolved_provider,
                resolved_taxonomy,
                resolved_taxonomy_version,
            ),
            "status": "CALCULATED",
            "scope": "SIMULATED_POST_FLOW_POINT_IN_TIME_SECTOR_EXPOSURE",
            "simulation_only": True,
            "currency": "USD",
            "calculated_at": calculated.isoformat(),
            "portfolio_version": version,
            "horizon": resolved_horizon,
            "horizon_label": valuation["horizon_label"],
            "effective_at": valuation["outcome_asset_price_effective_at"],
            "valuation_id": valuation["valuation_id"],
            "valuation_record_hash": valuation["record_hash"],
            "supporting_cash_flow_ids": [item["flow_id"] for item in flows],
            "supporting_cash_flow_hashes": [item["record_hash"] for item in flows],
            "classification_evidence_ids": [
                item["evidence_id"] for item in classifications
            ],
            "classification_record_hashes": [
                item["record_hash"] for item in classifications
            ],
            "provider": resolved_provider,
            "taxonomy_name": resolved_taxonomy,
            "taxonomy_version": resolved_taxonomy_version,
            "sector_exposure_calculated": True,
            "cash_classified_as_sector": False,
            "recommendation_provided": False,
            "portfolio_return_calculated": False,
            "alpha_calculated": False,
            "risk_adjusted": False,
            "annualized": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "strategy_version": valuation["strategy_version"],
            "model_versions": valuation["model_versions"],
            "git_revision": valuation["git_revision"],
            **economics,
            "formula": dict(FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def _pinned_support(
        self, record: Mapping[str, Any]
    ) -> tuple[
        dict[str, Any] | None,
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[str],
    ]:
        reasons = []
        valuations = self.valuation_ledger.verify()
        valuation = next(
            (
                item
                for item in valuations
                if item.get("valuation_id") == record.get("valuation_id")
            ),
            None,
        )
        if valuation is None:
            return None, [], [], ["Pinned portfolio valuation is missing."]
        target_at = _as_datetime(valuation["outcome_asset_price_effective_at"])
        included_valuations = {
            item["valuation_id"]: item
            for item in valuations
            if item.get("portfolio_version") == valuation.get("portfolio_version")
            and _as_datetime(item["outcome_asset_price_effective_at"]) <= target_at
        }

        flow_ids = record.get("supporting_cash_flow_ids")
        flow_hashes = record.get("supporting_cash_flow_hashes")
        if not isinstance(flow_ids, list) or not isinstance(flow_hashes, list):
            return valuation, [], [], ["Pinned cash-flow support is invalid."]
        all_flows = self.cash_flow_ledger.verify()
        flows_by_id = {item["flow_id"]: item for item in all_flows}
        flows = [flows_by_id.get(flow_id) for flow_id in flow_ids]
        if (
            len(flow_ids) != len(set(flow_ids))
            or len(flow_ids) != len(flow_hashes)
            or any(item is None for item in flows)
        ):
            reasons.append("Pinned cash-flow support is missing or duplicated.")
            resolved_flows: list[dict[str, Any]] = []
        else:
            resolved_flows = [item for item in flows if item is not None]
            if [item["record_hash"] for item in resolved_flows] != flow_hashes:
                reasons.append("Pinned cash-flow support hash has changed.")
            if any(
                item.get("valuation_id") not in included_valuations
                or _as_datetime(item["effective_at"]) > target_at
                for item in resolved_flows
            ):
                reasons.append("Pinned cash flow is outside the valuation boundary.")

        evidence_ids = record.get("classification_evidence_ids")
        evidence_hashes = record.get("classification_record_hashes")
        if not isinstance(evidence_ids, list) or not isinstance(evidence_hashes, list):
            return valuation, resolved_flows, [], [
                *reasons,
                "Pinned classification support is invalid.",
            ]
        all_classifications = self.classification_ledger.verify()
        classifications_by_id = {
            item["evidence_id"]: item for item in all_classifications
        }
        classifications = [
            classifications_by_id.get(evidence_id) for evidence_id in evidence_ids
        ]
        if (
            len(evidence_ids) != len(set(evidence_ids))
            or len(evidence_ids) != len(evidence_hashes)
            or any(item is None for item in classifications)
        ):
            reasons.append("Pinned classification support is missing or duplicated.")
            resolved_classifications: list[dict[str, Any]] = []
        else:
            resolved_classifications = [
                item for item in classifications if item is not None
            ]
            if [item["record_hash"] for item in resolved_classifications] != evidence_hashes:
                reasons.append("Pinned classification support hash has changed.")

        identity_fields = ("strategy_version", "model_versions", "git_revision")
        if any(
            any(item.get(field) != valuation.get(field) for field in identity_fields)
            for item in resolved_flows
        ):
            reasons.append("Pinned cash flow has incompatible identity.")
        expected_tickers = {
            str(item.get("ticker") or "").upper()
            for item in valuation.get("positions") or []
        }
        actual_tickers = {
            str(item.get("ticker") or "").upper()
            for item in resolved_classifications
        }
        if len(resolved_classifications) != len(expected_tickers) or (
            actual_tickers != expected_tickers
        ):
            reasons.append("Pinned classifications must match every position exactly.")
        if any(
            item.get("valuation_id") != valuation.get("valuation_id")
            or item.get("completeness_status") != "COMPLETE"
            or item.get("provider") != record.get("provider")
            or item.get("taxonomy_name") != record.get("taxonomy_name")
            or item.get("taxonomy_version") != record.get("taxonomy_version")
            for item in resolved_classifications
        ):
            reasons.append("Pinned classifications violate the selected taxonomy.")
        return valuation, resolved_flows, resolved_classifications, reasons

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {
                key: value for key, value in record.items() if key != "record_hash"
            }
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Sector-exposure chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Sector-exposure record {index} has been modified."
                )
            version = str(record.get("portfolio_version") or "")
            horizon = str(record.get("horizon") or "")
            provider = str(record.get("provider") or "")
            taxonomy = str(record.get("taxonomy_name") or "")
            taxonomy_version = str(record.get("taxonomy_version") or "")
            valuation, flows, classifications, reasons = self._pinned_support(record)
            if reasons or valuation is None:
                raise LedgerIntegrityError(
                    f"Sector-exposure record {index} lost supporting evidence."
                )
            try:
                economics = _economics(valuation, flows, classifications)
                calculated = _as_datetime(record.get("calculated_at"))
                latest_support = max(
                    [_as_datetime(valuation["calculated_at"])]
                    + [_as_datetime(item["recorded_at"]) for item in flows]
                    + [_as_datetime(item["recorded_at"]) for item in classifications]
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Sector-exposure record {index} has invalid values."
                ) from error
            expected_id = _result_id(
                version, horizon, provider, taxonomy, taxonomy_version
            )
            boundary = (
                record.get("schema_version") == SECTOR_EXPOSURE_SCHEMA_VERSION
                and record.get("calculation_version")
                == SECTOR_EXPOSURE_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope")
                == "SIMULATED_POST_FLOW_POINT_IN_TIME_SECTOR_EXPOSURE"
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and record.get("horizon_label") == valuation["horizon_label"]
                and record.get("effective_at")
                == valuation["outcome_asset_price_effective_at"]
                and record.get("valuation_id") == valuation["valuation_id"]
                and record.get("valuation_record_hash") == valuation["record_hash"]
                and record.get("supporting_cash_flow_ids")
                == [item["flow_id"] for item in flows]
                and record.get("supporting_cash_flow_hashes")
                == [item["record_hash"] for item in flows]
                and record.get("classification_evidence_ids")
                == [item["evidence_id"] for item in classifications]
                and record.get("classification_record_hashes")
                == [item["record_hash"] for item in classifications]
                and record.get("sector_exposure_calculated") is True
                and record.get("cash_classified_as_sector") is False
                and record.get("recommendation_provided") is False
                and record.get("portfolio_return_calculated") is False
                and record.get("alpha_calculated") is False
                and record.get("risk_adjusted") is False
                and record.get("annualized") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("strategy_version") == valuation["strategy_version"]
                and record.get("model_versions") == valuation["model_versions"]
                and record.get("git_revision") == valuation["git_revision"]
                and record.get("formula") == FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and calculated >= latest_support
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Sector-exposure record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            previous_hash = record["record_hash"]
        return records

    def _append(
        self, result: dict[str, Any], *, allow_existing: bool
    ) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["result_id"] == result["result_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "calculated_at"}
                comparable = {
                    key: value for key, value in existing.items() if key not in ignored
                }
                proposed = {
                    key: value for key, value in result.items() if key not in ignored
                }
                if allow_existing and comparable == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Sector exposure result {result['result_id']} already exists."
                )
            material = {
                **result,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            try:
                _write_all(target, (_canonical_json(record) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def repair_incomplete_tail(self) -> Path | None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if not self.path.exists():
                return None
            raw = self.path.read_bytes()
            if not raw or raw.endswith(b"\n"):
                return None
            complete_end = raw.rfind(b"\n") + 1
            prefix, tail = raw[:complete_end], raw[complete_end:]
            try:
                json.loads(tail.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                backup = self.path.with_suffix(
                    self.path.suffix + f".incomplete-tail-{uuid4().hex}"
                )
                backup_descriptor = os.open(
                    backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                try:
                    _write_all(backup_descriptor, tail)
                    os.fsync(backup_descriptor)
                finally:
                    os.close(backup_descriptor)
                target = os.open(self.path, os.O_WRONLY | os.O_TRUNC)
                try:
                    _write_all(target, prefix)
                    os.fsync(target)
                finally:
                    os.close(target)
                self.verify()
                return backup
            target = os.open(self.path, os.O_WRONLY | os.O_APPEND)
            try:
                _write_all(target, b"\n")
                os.fsync(target)
            finally:
                os.close(target)
            self.verify()
            return None
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
