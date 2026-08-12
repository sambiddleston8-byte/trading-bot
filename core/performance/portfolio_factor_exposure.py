from __future__ import annotations

"""Exact simulated portfolio factor exposure from pinned security evidence."""

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
from core.performance.factor_exposure_evidence import FactorExposureEvidenceLedger
from core.performance.pinned_support import resolve_pinned_records
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


PORTFOLIO_FACTOR_EXPOSURE_SCHEMA_VERSION = "1.0"
PORTFOLIO_FACTOR_EXPOSURE_CALCULATION_VERSION = (
    "post-flow-point-in-time-portfolio-factor-exposure-v1"
)
MAX_CLOCK_SKEW = timedelta(minutes=5)
FORMULA = {
    "post_flow_cash": "base_remaining_cash + cumulative_external_cash_flow",
    "post_flow_total_equity": "base_total_equity + cumulative_external_cash_flow",
    "invested_weighted_exposure": (
        "sum(position_value * security_factor_exposure) / invested_position_value"
    ),
    "contribution_scaled_to_total_equity": (
        "sum(position_value * security_factor_exposure) / post_flow_total_equity"
    ),
    "cash_policy": "CASH_REPORTED_SEPARATELY_WITHOUT_ASSUMED_FACTOR_EXPOSURE",
    "evidence_policy": (
        "EXACTLY_ONE_COMPLETE_RECORD_PER_POSITION_WITH_IDENTICAL_MODEL_"
        "METHODOLOGY_EFFECTIVE_TIME_DEFINITIONS_AND_UNITS"
    ),
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
    factor_model_name: str,
    factor_model_version: str,
) -> str:
    material = [
        portfolio_version,
        horizon,
        provider,
        factor_model_name,
        factor_model_version,
        PORTFOLIO_FACTOR_EXPOSURE_CALCULATION_VERSION,
    ]
    return "PFEXP-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _definitions(evidence: Mapping[str, Any]) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            _required(item.get("factor_code"), "factor_code"),
            _required(item.get("factor_name"), "factor_name"),
            _required(item.get("unit"), "unit"),
        )
        for item in evidence.get("factors") or []
    )


def _economics(
    valuation: Mapping[str, Any],
    flows: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
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
    if cash < 0 or equity <= 0:
        raise ValueError("Post-flow cash and total equity must remain valid")

    positions = list(valuation.get("positions") or [])
    tickers = [str(item.get("ticker") or "").upper() for item in positions]
    if not positions or any(not item for item in tickers) or len(set(tickers)) != len(tickers):
        raise ValueError("Verified factor-exposure positions require unique tickers")
    by_ticker = {str(item.get("ticker") or "").upper(): item for item in evidence}
    if set(by_ticker) != set(tickers) or len(by_ticker) != len(evidence):
        raise ValueError("Factor evidence must match every position exactly once")

    first = evidence[0]
    expected_methodology = (
        first.get("methodology_uri"),
        first.get("methodology_sha256"),
    )
    expected_effective_at = first.get("factor_effective_at")
    expected_definitions = _definitions(first)
    if not expected_definitions:
        raise ValueError("Complete factor evidence must contain factors")
    for item in evidence:
        if (item.get("methodology_uri"), item.get("methodology_sha256")) != expected_methodology:
            raise ValueError("All positions require the same factor methodology and hash")
        if item.get("factor_effective_at") != expected_effective_at:
            raise ValueError("All positions require the same factor effective timestamp")
        if _definitions(item) != expected_definitions:
            raise ValueError("All positions require identical factor definitions and units")

    weighted_sums = {code: Fraction(0) for code, _, _ in expected_definitions}
    invested_value = Fraction(0)
    position_evidence = []
    for position, ticker in zip(positions, tickers):
        value = _fraction(
            position.get("exact_fractions", {}).get("outcome_position_value", {}),
            f"{ticker} position value",
        )
        if value < 0:
            raise ValueError("Position values cannot be negative")
        item = by_ticker[ticker]
        factor_rows = []
        for factor in item["factors"]:
            exposure = _fraction(factor.get("exact_exposure", {}), "factor exposure")
            weighted = value * exposure
            weighted_sums[factor["factor_code"]] += weighted
            factor_rows.append(
                {
                    **factor,
                    "position_value_weighted_exposure": _decimal_string(weighted),
                    "exact_position_value_weighted_exposure": _fraction_material(weighted),
                }
            )
        invested_value += value
        position_evidence.append(
            {
                "ticker": ticker,
                "position_value": _decimal_string(value),
                "exact_position_value": _fraction_material(value),
                "factor_evidence_id": item["evidence_id"],
                "factor_evidence_record_hash": item["record_hash"],
                "availability_at_valuation": item["availability_at_valuation"],
                "factors": factor_rows,
            }
        )
    if invested_value <= 0 or invested_value != equity - cash:
        raise ValueError("Position, cash and total-equity values must reconcile exactly")

    factors = []
    for code, name, unit in expected_definitions:
        weighted_sum = weighted_sums[code]
        invested_exposure = weighted_sum / invested_value
        equity_contribution = weighted_sum / equity
        factors.append(
            {
                "factor_code": code,
                "factor_name": name,
                "unit": unit,
                "invested_position_weighted_exposure": _decimal_string(invested_exposure),
                "position_contribution_scaled_to_total_equity": _decimal_string(equity_contribution),
                "exact_fractions": {
                    "position_value_weighted_exposure_sum": _fraction_material(weighted_sum),
                    "invested_position_weighted_exposure": _fraction_material(invested_exposure),
                    "position_contribution_scaled_to_total_equity": _fraction_material(equity_contribution),
                },
            }
        )
    cash_weight = cash / equity
    invested_weight = invested_value / equity
    if cash_weight + invested_weight != 1:
        raise ValueError("Cash and invested weights must reconcile exactly")
    exact = {
        "base_remaining_cash": base_cash,
        "base_total_equity": base_equity,
        "cumulative_external_cash_flow": cumulative_flow,
        "post_flow_cash": cash,
        "post_flow_total_equity": equity,
        "invested_position_value": invested_value,
        "cash_weight": cash_weight,
        "invested_position_weight": invested_weight,
    }
    return {
        "position_count": len(positions),
        "factor_count": len(factors),
        "factor_effective_at": expected_effective_at,
        "methodology_uri": expected_methodology[0],
        "methodology_sha256": expected_methodology[1],
        "contains_backfilled_factor_evidence": any(
            item["availability_at_valuation"] == "BACKFILLED_AFTER_BOUNDARY"
            for item in evidence
        ),
        "positions": sorted(position_evidence, key=lambda item: item["ticker"]),
        "factors": factors,
        **{key: _decimal_string(value) for key, value in exact.items()},
        "exact_fractions": {key: _fraction_material(value) for key, value in exact.items()},
    }


class PortfolioFactorExposureLedger:
    """Append-only exact factor aggregation with cash explicitly unmodelled."""

    def __init__(
        self,
        path: str | Path,
        valuation_ledger: SimulatedPortfolioValuationLedger,
        cash_flow_ledger: PortfolioCashFlowLedger,
        factor_evidence_ledger: FactorExposureEvidenceLedger,
    ) -> None:
        self.path = Path(path)
        self.valuation_ledger = valuation_ledger
        self.cash_flow_ledger = cash_flow_ledger
        self.factor_evidence_ledger = factor_evidence_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Portfolio-factor-exposure ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank portfolio-factor-exposure line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at portfolio-factor-exposure line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Portfolio-factor-exposure line {line_number} is not an object."
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
            "portfolio_factor_exposure_calculated": False,
            "cash_factor_exposure_modelled": False,
            "recommendation_provided": False,
            "performance_claim": False,
            "alpha_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
        }

    def _support(
        self,
        portfolio_version: str,
        horizon: str,
        provider: str,
        factor_model_name: str,
        factor_model_version: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        valuations = self.valuation_ledger.verify()
        valuation = next(
            (
                item for item in valuations
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
                item for item in self.cash_flow_ledger.verify()
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
            reasons.append("Factor exposure must share strategy, model and Git identity.")

        all_evidence = self.factor_evidence_ledger.verify()
        selected = []
        for position in valuation.get("positions") or []:
            ticker = str(position.get("ticker") or "").upper()
            matches = [
                item for item in all_evidence
                if item.get("valuation_id") == valuation["valuation_id"]
                and item.get("ticker") == ticker
                and item.get("provider") == provider
                and item.get("factor_model_name") == factor_model_name
                and item.get("factor_model_version") == factor_model_version
                and item.get("completeness_status") == "COMPLETE"
            ]
            if len(matches) != 1:
                reasons.append(
                    f"{ticker} requires exactly one complete observation under the selected factor model."
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
        factor_model_name: str,
        factor_model_version: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        resolved_horizon = str(horizon or "").upper()
        try:
            resolved_provider = _required(provider, "provider")
            resolved_model = _required(factor_model_name, "factor_model_name")
            resolved_model_version = _required(
                factor_model_version, "factor_model_version"
            )
        except ValueError as error:
            return self.not_calculable(version, resolved_horizon, [str(error)])
        valuation, flows, evidence, reasons = self._support(
            version,
            resolved_horizon,
            resolved_provider,
            resolved_model,
            resolved_model_version,
        )
        if reasons:
            return self.not_calculable(version, resolved_horizon, reasons)
        assert valuation is not None
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest_support = max(
            [_as_datetime(valuation["calculated_at"])]
            + [_as_datetime(item["recorded_at"]) for item in flows]
            + [_as_datetime(item["recorded_at"]) for item in evidence]
        )
        if calculated < latest_support:
            return self.not_calculable(
                version,
                resolved_horizon,
                ["calculated_at cannot predate supporting factor evidence."],
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                version, resolved_horizon, ["calculated_at cannot be in the future."]
            )
        try:
            economics = _economics(valuation, flows, evidence)
        except ValueError as error:
            return self.not_calculable(version, resolved_horizon, [str(error)])
        result = {
            "schema_version": PORTFOLIO_FACTOR_EXPOSURE_SCHEMA_VERSION,
            "calculation_version": PORTFOLIO_FACTOR_EXPOSURE_CALCULATION_VERSION,
            "result_id": _result_id(
                version,
                resolved_horizon,
                resolved_provider,
                resolved_model,
                resolved_model_version,
            ),
            "status": "CALCULATED",
            "scope": "SIMULATED_POST_FLOW_INVESTED_POSITION_FACTOR_EXPOSURE",
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
            "factor_evidence_ids": [item["evidence_id"] for item in evidence],
            "factor_evidence_record_hashes": [item["record_hash"] for item in evidence],
            "provider": resolved_provider,
            "factor_model_name": resolved_model,
            "factor_model_version": resolved_model_version,
            "portfolio_factor_exposure_calculated": True,
            "cash_factor_exposure_modelled": False,
            "recommendation_provided": False,
            "performance_claim": False,
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
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        valuations = self.valuation_ledger.verify()
        valuation = next(
            (item for item in valuations if item.get("valuation_id") == record.get("valuation_id")),
            None,
        )
        if valuation is None:
            return None, [], [], ["Pinned portfolio valuation is missing."]
        flows, flow_reasons = resolve_pinned_records(
            self.cash_flow_ledger.verify(),
            record.get("supporting_cash_flow_ids"),
            record.get("supporting_cash_flow_hashes"),
            id_field="flow_id",
            label="cash-flow",
        )
        evidence, evidence_reasons = resolve_pinned_records(
            self.factor_evidence_ledger.verify(),
            record.get("factor_evidence_ids"),
            record.get("factor_evidence_record_hashes"),
            id_field="evidence_id",
            label="factor-evidence",
        )
        reasons = [*flow_reasons, *evidence_reasons]
        target_at = _as_datetime(valuation["outcome_asset_price_effective_at"])
        included_valuations = {
            item["valuation_id"]
            for item in valuations
            if item.get("portfolio_version") == valuation.get("portfolio_version")
            and _as_datetime(item["outcome_asset_price_effective_at"]) <= target_at
        }
        identity_fields = ("strategy_version", "model_versions", "git_revision")
        if any(
            item.get("valuation_id") not in included_valuations
            or _as_datetime(item["effective_at"]) > target_at
            or any(item.get(field) != valuation.get(field) for field in identity_fields)
            for item in flows
        ):
            reasons.append("Pinned cash flow violates its valuation boundary or identity.")
        expected_tickers = {
            str(item.get("ticker") or "").upper()
            for item in valuation.get("positions") or []
        }
        actual_tickers = {str(item.get("ticker") or "").upper() for item in evidence}
        if len(evidence) != len(expected_tickers) or actual_tickers != expected_tickers:
            reasons.append("Pinned factor evidence must match every position exactly.")
        if any(
            item.get("valuation_id") != valuation.get("valuation_id")
            or item.get("completeness_status") != "COMPLETE"
            or item.get("provider") != record.get("provider")
            or item.get("factor_model_name") != record.get("factor_model_name")
            or item.get("factor_model_version") != record.get("factor_model_version")
            for item in evidence
        ):
            reasons.append("Pinned factor evidence violates the selected model.")
        return valuation, list(flows), list(evidence), reasons

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Portfolio-factor-exposure chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Portfolio-factor-exposure record {index} has been modified."
                )
            valuation, flows, evidence, reasons = self._pinned_support(record)
            if reasons or valuation is None:
                raise LedgerIntegrityError(
                    f"Portfolio-factor-exposure record {index} lost supporting evidence."
                )
            try:
                economics = _economics(valuation, flows, evidence)
                calculated = _as_datetime(record.get("calculated_at"))
                latest_support = max(
                    [_as_datetime(valuation["calculated_at"])]
                    + [_as_datetime(item["recorded_at"]) for item in flows]
                    + [_as_datetime(item["recorded_at"]) for item in evidence]
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Portfolio-factor-exposure record {index} has invalid values."
                ) from error
            expected_id = _result_id(
                record.get("portfolio_version", ""),
                record.get("horizon", ""),
                record.get("provider", ""),
                record.get("factor_model_name", ""),
                record.get("factor_model_version", ""),
            )
            boundary = (
                record.get("schema_version") == PORTFOLIO_FACTOR_EXPOSURE_SCHEMA_VERSION
                and record.get("calculation_version") == PORTFOLIO_FACTOR_EXPOSURE_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope") == "SIMULATED_POST_FLOW_INVESTED_POSITION_FACTOR_EXPOSURE"
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and record.get("horizon_label") == valuation["horizon_label"]
                and record.get("effective_at") == valuation["outcome_asset_price_effective_at"]
                and record.get("valuation_id") == valuation["valuation_id"]
                and record.get("valuation_record_hash") == valuation["record_hash"]
                and record.get("supporting_cash_flow_ids") == [item["flow_id"] for item in flows]
                and record.get("supporting_cash_flow_hashes") == [item["record_hash"] for item in flows]
                and record.get("factor_evidence_ids") == [item["evidence_id"] for item in evidence]
                and record.get("factor_evidence_record_hashes") == [item["record_hash"] for item in evidence]
                and record.get("portfolio_factor_exposure_calculated") is True
                and record.get("cash_factor_exposure_modelled") is False
                and record.get("recommendation_provided") is False
                and record.get("performance_claim") is False
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
                    f"Portfolio-factor-exposure record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
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
                comparable = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and comparable == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Portfolio factor exposure result {result['result_id']} already exists."
                )
            material = {
                **result,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
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
                backup_descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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
