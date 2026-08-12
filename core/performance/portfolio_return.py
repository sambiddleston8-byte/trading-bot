from __future__ import annotations

"""Exact cash-flow-neutral return linked across simulated valuation periods."""

from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.portfolio_cash_flow import PortfolioCashFlowLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import SimulatedPortfolioValuationLedger


PORTFOLIO_RETURN_SCHEMA_VERSION = "1.0"
PORTFOLIO_RETURN_CALCULATION_VERSION = "boundary-cash-flow-time-weighted-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
CALCULATION_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
PORTFOLIO_RETURN_FORMULA = {
    "pre_flow_equity": "base_portfolio_total_equity + cumulative_prior_external_cash_flows",
    "subperiod_return": "pre_flow_equity / previous_post_flow_equity - 1",
    "post_flow_equity": "pre_flow_equity + boundary_external_cash_flow",
    "linked_return": "product(1 + subperiod_return) - 1",
    "cash_flow_timing": "AFTER_MARKET_VALUATION_END_OF_SUBPERIOD",
    "midperiod_cash_flow_policy": "NOT_SUPPORTED_REQUIRES_EXACT_BOUNDARY_VALUATION",
    "arithmetic_policy": "EXACT_RATIONAL_WITH_34_DIGIT_DECIMAL_PRESENTATION",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete append-only portfolio-return write")
        written += count


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _fraction(material: Mapping[str, Any], name: str) -> Fraction:
    try:
        denominator = int(material["denominator"])
        value = Fraction(int(material["numerator"]), denominator)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{name} exact fraction is invalid") from error
    if denominator <= 0:
        raise ValueError(f"{name} exact fraction denominator must be positive")
    return value


def _fraction_material(value: Fraction) -> dict[str, str]:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _decimal_string(value: Fraction) -> str:
    with localcontext(CALCULATION_CONTEXT):
        resolved = Decimal(value.numerator) / Decimal(value.denominator)
    if resolved == 0:
        return "0"
    return format(resolved.normalize(), "f")


def _result_id(portfolio_version: str, horizon: str) -> str:
    material = [portfolio_version, horizon, PORTFOLIO_RETURN_CALCULATION_VERSION]
    return "PRET-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(
    funding: Mapping[str, Any],
    valuations: Sequence[Mapping[str, Any]],
    flows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    initial_funding = _fraction(funding["exact_amount"], "initial funding")
    if initial_funding <= 0:
        raise ValueError("Initial funding must be positive")
    flow_by_valuation = {item["valuation_id"]: item for item in flows}
    prior_post_flow_equity = initial_funding
    cumulative_prior_flow = Fraction(0)
    linked_growth = Fraction(1)
    subperiods = []
    for valuation in valuations:
        base_equity = _fraction(
            valuation["exact_fractions"]["total_equity"], "base total equity"
        )
        pre_flow_equity = base_equity + cumulative_prior_flow
        if pre_flow_equity <= 0 or prior_post_flow_equity <= 0:
            raise ValueError("Subperiod equity must remain positive")
        subperiod_return = pre_flow_equity / prior_post_flow_equity - 1
        boundary_flow = flow_by_valuation.get(valuation["valuation_id"])
        signed_flow = (
            _fraction(boundary_flow["exact_signed_amount"], "boundary cash flow")
            if boundary_flow is not None
            else Fraction(0)
        )
        post_flow_equity = pre_flow_equity + signed_flow
        if post_flow_equity <= 0:
            raise ValueError("Post-flow equity must remain positive")
        linked_growth *= 1 + subperiod_return
        subperiods.append(
            {
                "horizon": valuation["horizon"],
                "horizon_label": valuation["horizon_label"],
                "valuation_id": valuation["valuation_id"],
                "valuation_record_hash": valuation["record_hash"],
                "effective_at": valuation["outcome_asset_price_effective_at"],
                "previous_post_flow_equity": _decimal_string(prior_post_flow_equity),
                "base_portfolio_total_equity": _decimal_string(base_equity),
                "cumulative_prior_external_cash_flow": _decimal_string(
                    cumulative_prior_flow
                ),
                "pre_flow_equity": _decimal_string(pre_flow_equity),
                "subperiod_return": _decimal_string(subperiod_return),
                "boundary_cash_flow_id": (
                    boundary_flow["flow_id"] if boundary_flow is not None else None
                ),
                "boundary_cash_flow_record_hash": (
                    boundary_flow["record_hash"] if boundary_flow is not None else None
                ),
                "boundary_signed_cash_flow": _decimal_string(signed_flow),
                "post_flow_equity": _decimal_string(post_flow_equity),
                "exact_fractions": {
                    "previous_post_flow_equity": _fraction_material(
                        prior_post_flow_equity
                    ),
                    "base_portfolio_total_equity": _fraction_material(base_equity),
                    "cumulative_prior_external_cash_flow": _fraction_material(
                        cumulative_prior_flow
                    ),
                    "pre_flow_equity": _fraction_material(pre_flow_equity),
                    "subperiod_return": _fraction_material(subperiod_return),
                    "boundary_signed_cash_flow": _fraction_material(signed_flow),
                    "post_flow_equity": _fraction_material(post_flow_equity),
                },
            }
        )
        cumulative_prior_flow += signed_flow
        prior_post_flow_equity = post_flow_equity
    linked_return = linked_growth - 1
    return {
        "subperiods": subperiods,
        "subperiod_count": len(subperiods),
        "initial_funding": _decimal_string(initial_funding),
        "cumulative_external_cash_flow": _decimal_string(cumulative_prior_flow),
        "ending_pre_flow_equity": subperiods[-1]["pre_flow_equity"],
        "ending_post_flow_equity": subperiods[-1]["post_flow_equity"],
        "time_weighted_portfolio_return": _decimal_string(linked_return),
        "exact_fractions": {
            "initial_funding": _fraction_material(initial_funding),
            "cumulative_external_cash_flow": _fraction_material(cumulative_prior_flow),
            "ending_pre_flow_equity": subperiods[-1]["exact_fractions"][
                "pre_flow_equity"
            ],
            "ending_post_flow_equity": subperiods[-1]["exact_fractions"][
                "post_flow_equity"
            ],
            "time_weighted_portfolio_return": _fraction_material(linked_return),
        },
    }


class TimeWeightedPortfolioReturnLedger:
    """Append-only simulated TWR; explicitly not alpha or a live track record."""

    def __init__(
        self,
        path: str | Path,
        valuation_ledger: SimulatedPortfolioValuationLedger,
        cash_flow_ledger: PortfolioCashFlowLedger,
    ) -> None:
        self.path = Path(path)
        self.valuation_ledger = valuation_ledger
        self.cash_flow_ledger = cash_flow_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Portfolio-return ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank portfolio-return line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at portfolio-return line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Portfolio-return line {line_number} is not an object."
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
            "through_horizon": str(horizon).upper(),
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "portfolio_return_calculated": False,
            "relative_portfolio_return_calculated": False,
            "alpha_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
        }

    def _support(
        self, portfolio_version: str, through_horizon: str
    ) -> tuple[
        dict[str, Any] | None,
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[str],
    ]:
        all_valuations = self.valuation_ledger.verify()
        candidates = [
            item
            for item in all_valuations
            if item.get("portfolio_version") == portfolio_version
        ]
        through = next(
            (item for item in candidates if item.get("horizon") == through_horizon), None
        )
        reasons = []
        if through_horizon == "ENTRY":
            reasons.append("ENTRY is the funding baseline, not a return horizon.")
        if through is None:
            reasons.append("Verified through-horizon portfolio valuation is missing.")
            return None, [], [], reasons
        through_at = _as_datetime(through["outcome_asset_price_effective_at"])
        valuations = sorted(
            (
                item
                for item in candidates
                if _as_datetime(item["outcome_asset_price_effective_at"]) <= through_at
            ),
            key=lambda item: _as_datetime(item["outcome_asset_price_effective_at"]),
        )
        if len(
            {item["outcome_asset_price_effective_at"] for item in valuations}
        ) != len(valuations):
            reasons.append("Portfolio valuations must have unique effective times.")
        funding = self.valuation_ledger.funding_ledger.funding_for(portfolio_version)
        if funding is None:
            reasons.append("Verified initial portfolio funding is missing.")
        flows = sorted(
            (
                item
                for item in self.cash_flow_ledger.verify()
                if item.get("portfolio_version") == portfolio_version
                and _as_datetime(item["effective_at"]) <= through_at
            ),
            key=lambda item: _as_datetime(item["effective_at"]),
        )
        valuation_ids = {item["valuation_id"] for item in valuations}
        if any(item["valuation_id"] not in valuation_ids for item in flows):
            reasons.append("Every included cash flow must match an included valuation boundary.")
        if valuations:
            identity_fields = ("strategy_version", "model_versions", "git_revision")
            reference = valuations[0]
            if any(
                any(item.get(field) != reference.get(field) for field in identity_fields)
                for item in [*valuations, *flows]
            ) or (
                funding is not None
                and any(funding.get(field) != reference.get(field) for field in identity_fields)
            ):
                reasons.append("Return evidence must share strategy, model and Git identity.")
        return funding, valuations, flows, reasons

    def calculate(
        self,
        *,
        portfolio_version: str,
        through_horizon: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        horizon = str(through_horizon or "").upper()
        funding, valuations, flows, reasons = self._support(version, horizon)
        if reasons:
            return self.not_calculable(version, horizon, reasons)
        assert funding is not None and valuations
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest_support = max(
            [_as_datetime(item["calculated_at"]) for item in valuations]
            + [_as_datetime(item["recorded_at"]) for item in flows]
        )
        if calculated < latest_support:
            return self.not_calculable(
                version, horizon, ["calculated_at cannot predate supporting evidence."]
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                version, horizon, ["calculated_at cannot be in the future."]
            )
        try:
            economics = _economics(funding, valuations, flows)
        except ValueError as error:
            return self.not_calculable(version, horizon, [str(error)])
        result = {
            "schema_version": PORTFOLIO_RETURN_SCHEMA_VERSION,
            "calculation_version": PORTFOLIO_RETURN_CALCULATION_VERSION,
            "result_id": _result_id(version, horizon),
            "status": "CALCULATED",
            "scope": "SIMULATED_CASH_FLOW_NEUTRAL_TIME_WEIGHTED_PORTFOLIO_RETURN",
            "simulation_only": True,
            "currency": "USD",
            "calculated_at": calculated.isoformat(),
            "portfolio_version": version,
            "through_horizon": horizon,
            "through_horizon_label": valuations[-1]["horizon_label"],
            "funding_id": funding["funding_id"],
            "funding_record_hash": funding["record_hash"],
            "supporting_valuation_ids": [item["valuation_id"] for item in valuations],
            "supporting_valuation_hashes": [item["record_hash"] for item in valuations],
            "supporting_cash_flow_ids": [item["flow_id"] for item in flows],
            "supporting_cash_flow_hashes": [item["record_hash"] for item in flows],
            "portfolio_return_calculated": True,
            "relative_portfolio_return_calculated": False,
            "alpha_calculated": False,
            "risk_adjusted": False,
            "annualized": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "strategy_version": valuations[0]["strategy_version"],
            "model_versions": valuations[0]["model_versions"],
            "git_revision": valuations[0]["git_revision"],
            **economics,
            "formula": dict(PORTFOLIO_RETURN_FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def _pinned_support(
        self, record: Mapping[str, Any]
    ) -> tuple[
        dict[str, Any] | None,
        list[Mapping[str, Any]],
        list[Mapping[str, Any]],
        list[str],
    ]:
        version = str(record.get("portfolio_version") or "")
        horizon = str(record.get("through_horizon") or "")
        funding = self.valuation_ledger.funding_ledger.funding_for(version)
        reasons = []
        if funding is None:
            reasons.append("Pinned initial portfolio funding is missing.")
        valuations, valuation_reasons = resolve_pinned_records(
            self.valuation_ledger.verify(),
            record.get("supporting_valuation_ids"),
            record.get("supporting_valuation_hashes"),
            id_field="valuation_id",
            label="portfolio valuation",
        )
        flows, flow_reasons = resolve_pinned_records(
            self.cash_flow_ledger.verify(),
            record.get("supporting_cash_flow_ids"),
            record.get("supporting_cash_flow_hashes"),
            id_field="flow_id",
            label="cash flow",
        )
        reasons.extend(valuation_reasons)
        reasons.extend(flow_reasons)
        if not valuations:
            reasons.append("Pinned portfolio valuations are missing.")
            return funding, valuations, flows, reasons
        effective_times = [
            _as_datetime(item["outcome_asset_price_effective_at"])
            for item in valuations
        ]
        if effective_times != sorted(effective_times) or len(set(effective_times)) != len(
            effective_times
        ):
            reasons.append("Pinned portfolio valuations must have ordered unique times.")
        if any(item.get("portfolio_version") != version for item in valuations):
            reasons.append("Pinned portfolio valuations have the wrong portfolio.")
        if valuations[-1].get("horizon") != horizon:
            reasons.append("Pinned through-horizon valuation does not match the result.")
        valuation_ids = {item["valuation_id"] for item in valuations}
        if any(
            item.get("portfolio_version") != version
            or item.get("valuation_id") not in valuation_ids
            for item in flows
        ):
            reasons.append("Pinned cash flows do not match pinned valuation boundaries.")
        identity_fields = ("strategy_version", "model_versions", "git_revision")
        reference = valuations[0]
        if any(
            any(item.get(field) != reference.get(field) for field in identity_fields)
            for item in [*valuations, *flows]
        ) or (
            funding is not None
            and any(funding.get(field) != reference.get(field) for field in identity_fields)
        ):
            reasons.append("Pinned return evidence has incompatible identity.")
        return funding, valuations, flows, reasons

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Portfolio-return chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Portfolio-return record {index} has been modified."
                )
            version = str(record.get("portfolio_version") or "")
            horizon = str(record.get("through_horizon") or "")
            funding, valuations, flows, reasons = self._pinned_support(record)
            if reasons or funding is None or not valuations:
                raise LedgerIntegrityError(
                    f"Portfolio-return record {index} violates its boundary: "
                    "pinned support is missing or incompatible."
                )
            try:
                economics = _economics(funding, valuations, flows)
                calculated = _as_datetime(record.get("calculated_at"))
                latest_support = max(
                    [_as_datetime(item["calculated_at"]) for item in valuations]
                    + [_as_datetime(item["recorded_at"]) for item in flows]
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Portfolio-return record {index} has invalid values."
                ) from error
            expected_id = _result_id(version, horizon)
            boundary = (
                record.get("schema_version") == PORTFOLIO_RETURN_SCHEMA_VERSION
                and record.get("calculation_version")
                == PORTFOLIO_RETURN_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope")
                == "SIMULATED_CASH_FLOW_NEUTRAL_TIME_WEIGHTED_PORTFOLIO_RETURN"
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and record.get("through_horizon_label") == valuations[-1]["horizon_label"]
                and record.get("funding_id") == funding["funding_id"]
                and record.get("funding_record_hash") == funding["record_hash"]
                and record.get("supporting_valuation_ids")
                == [item["valuation_id"] for item in valuations]
                and record.get("supporting_valuation_hashes")
                == [item["record_hash"] for item in valuations]
                and record.get("supporting_cash_flow_ids")
                == [item["flow_id"] for item in flows]
                and record.get("supporting_cash_flow_hashes")
                == [item["record_hash"] for item in flows]
                and record.get("portfolio_return_calculated") is True
                and record.get("relative_portfolio_return_calculated") is False
                and record.get("alpha_calculated") is False
                and record.get("risk_adjusted") is False
                and record.get("annualized") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("strategy_version") == valuations[0]["strategy_version"]
                and record.get("model_versions") == valuations[0]["model_versions"]
                and record.get("git_revision") == valuations[0]["git_revision"]
                and record.get("formula") == PORTFOLIO_RETURN_FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and calculated >= latest_support
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Portfolio-return record {index} violates its boundary."
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
                (item for item in records if item["result_id"] == result["result_id"]), None
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "calculated_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Portfolio return {result['result_id']} already exists."
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
