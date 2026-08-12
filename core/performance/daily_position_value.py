from __future__ import annotations

"""Exact daily simulated position values from immutable close/action evidence."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.corporate_action import CorporateActionLedger
from core.performance.daily_market_observation import DailyMarketObservationLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import (
    _canonical_json,
    _decimal_string,
    _fraction,
    _fraction_material,
    _record_hash,
    _write_all,
)


DAILY_POSITION_VALUE_SCHEMA_VERSION = "1.0"
DAILY_POSITION_VALUE_CALCULATION_VERSION = "split-dividend-complete-position-value-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
FORMULA = {
    "split_adjusted_quantity": "initial_quantity * product(split_numerator / split_denominator)",
    "position_market_value": "split_adjusted_quantity * official_unadjusted_close",
    "cumulative_gross_dividend_cash": (
        "sum(quantity_immediately_before_ex_time * paid_usd_amount_per_share)"
    ),
    "gross_holding_value": "position_market_value + cumulative_gross_dividend_cash",
    "dividend_policy": "GROSS_USD_CASH_NO_REINVESTMENT_BEFORE_TAX",
    "payment_policy": "INCLUDED_ONLY_IF_PAID_BY_DAILY_CLOSE",
    "fractional_share_policy": "RETAIN_EXACT_SIMULATED_FRACTIONAL_QUANTITY",
    "cash_in_lieu_policy": "NOT_INCLUDED",
    "arithmetic_policy": "EXACT_RATIONAL_WITH_34_DIGIT_DECIMAL_PRESENTATION",
}


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _decimal_fraction(value: Any, name: str, *, allow_zero: bool = False) -> Fraction:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite() or resolved < 0 or (resolved == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be a {qualifier} finite decimal")
    return Fraction(resolved)


def _event_time(event: Mapping[str, Any]) -> datetime:
    field = "ex_at" if event.get("event_type") == "CASH_DIVIDEND" else "effective_at"
    return _as_datetime(event.get(field))


def _result_id(observation_id: str) -> str:
    material = [observation_id, DAILY_POSITION_VALUE_CALCULATION_VERSION]
    return "DPVAL-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(
    fill: Mapping[str, Any],
    observation: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    fill_at = _as_datetime(fill["filled_at"])
    close_at = _as_datetime(observation["price_effective_at"])
    if fill.get("side") != "BUY":
        raise ValueError("Daily position value currently supports simulated long BUY fills only")
    if observation.get("price_basis") != "UNADJUSTED_CLOSE":
        raise ValueError("Daily position value requires an unadjusted official close")
    if evidence.get("completeness_status") != "COMPLETE":
        raise ValueError("Corporate-action evidence must be COMPLETE")
    if _as_datetime(evidence["covers_from_at"]) > fill_at:
        raise ValueError("Corporate-action evidence does not cover the fill time")
    if _as_datetime(evidence["through_at"]) < close_at:
        raise ValueError("Corporate-action evidence does not cover the daily close")

    events = [
        dict(item)
        for item in evidence.get("events") or []
        if fill_at <= _event_time(item) <= close_at
    ]
    events.sort(key=lambda item: (_event_time(item), item["event_type"], item["source_event_id"]))
    if any(item.get("event_type") == "OTHER" for item in events):
        raise ValueError("Relevant unsupported corporate actions block daily position value")
    if any(_event_time(item) == fill_at for item in events):
        raise ValueError("A corporate action at the exact fill time is ambiguous")
    by_time: dict[datetime, list[dict[str, Any]]] = {}
    for item in events:
        by_time.setdefault(_event_time(item), []).append(item)
    if any(
        any(item["event_type"] == "STOCK_SPLIT" for item in same_time)
        and len(same_time) > 1
        for same_time in by_time.values()
    ):
        raise ValueError("Simultaneous split and distribution ordering is ambiguous")

    quantity = _decimal_fraction(fill["filled_quantity"], "filled_quantity")
    initial_quantity = quantity
    dividend_cash = Fraction(0)
    applied = []
    for item in events:
        if item["event_type"] == "STOCK_SPLIT":
            numerator = _decimal_fraction(item["numerator"], "split numerator")
            denominator = _decimal_fraction(item["denominator"], "split denominator")
            before = quantity
            quantity = quantity * numerator / denominator
            applied.append(
                {
                    "event_type": "STOCK_SPLIT",
                    "source_event_id": item["source_event_id"],
                    "effective_at": item["effective_at"],
                    "numerator": item["numerator"],
                    "denominator": item["denominator"],
                    "exact_quantity_before": _fraction_material(before),
                    "exact_quantity_after": _fraction_material(quantity),
                }
            )
        elif item["event_type"] == "CASH_DIVIDEND":
            if fill_at >= _as_datetime(item["ex_at"]):
                raise ValueError("The fill is not clearly entitled to a dividend")
            if item.get("payment_at") is None:
                raise ValueError("Dividend payment_at is required")
            if _as_datetime(item["payment_at"]) > close_at:
                raise ValueError("An entitled dividend was not paid by the daily close")
            if item.get("currency") != "USD":
                raise ValueError("Only USD dividends are supported without FX evidence")
            amount = _decimal_fraction(item["amount_per_share"], "amount_per_share")
            cash = quantity * amount
            dividend_cash += cash
            applied.append(
                {
                    "event_type": "CASH_DIVIDEND",
                    "source_event_id": item["source_event_id"],
                    "ex_at": item["ex_at"],
                    "payment_at": item["payment_at"],
                    "amount_per_share": item["amount_per_share"],
                    "currency": "USD",
                    "exact_entitled_quantity": _fraction_material(quantity),
                    "exact_gross_cash": _fraction_material(cash),
                }
            )
    close_price = _fraction(observation["exact_close_price"], "official close")
    position_value = quantity * close_price
    gross_value = position_value + dividend_cash
    exact = {
        "initial_quantity": initial_quantity,
        "split_adjusted_quantity": quantity,
        "official_unadjusted_close": close_price,
        "position_market_value": position_value,
        "cumulative_gross_dividend_cash": dividend_cash,
        "gross_holding_value": gross_value,
    }
    return {
        **{key: _decimal_string(value) for key, value in exact.items()},
        "exact_fractions": {key: _fraction_material(value) for key, value in exact.items()},
        "events_applied": applied,
    }


class DailyPositionValueLedger:
    """Append-only position values; portfolio aggregation remains separate."""

    def __init__(
        self,
        path: str | Path,
        observation_ledger: DailyMarketObservationLedger,
        corporate_action_ledger: CorporateActionLedger,
    ) -> None:
        self.path = Path(path)
        self.observation_ledger = observation_ledger
        self.corporate_action_ledger = corporate_action_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Daily-position-value ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank daily-position-value line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at daily-position-value line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Daily-position-value line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_calculable(observation_id: str, reasons: Sequence[str]) -> dict[str, Any]:
        return {
            "status": "NOT_CALCULABLE",
            "observation_id": str(observation_id),
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "daily_position_value_calculated": False,
            "daily_portfolio_valuation_calculated": False,
            "performance_metric_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
        }

    def calculate(
        self,
        *,
        observation_id: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        observations = self.observation_ledger.verify()
        observation = next(
            (item for item in observations if item.get("observation_id") == observation_id), None
        )
        if observation is None:
            return self.not_calculable(observation_id, ["Verified daily close observation is missing."])
        fill = next(
            (
                item
                for item in self.observation_ledger.execution_ledger.verify()
                if item.get("fill_id") == observation.get("fill_id")
            ),
            None,
        )
        if fill is None:
            return self.not_calculable(observation_id, ["Verified simulated fill is missing."])
        evidence = self.corporate_action_ledger.evidence_for(
            fill_id=fill["fill_id"], through_at=observation["price_effective_at"]
        )
        if evidence is None:
            return self.not_calculable(
                observation_id, ["Verified corporate-action coverage through the daily close is missing."]
            )
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest = max(_as_datetime(observation["recorded_at"]), _as_datetime(evidence["retrieved_at"]))
        if calculated < latest:
            return self.not_calculable(observation_id, ["calculated_at cannot predate supporting evidence."])
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(observation_id, ["calculated_at cannot be in the future."])
        try:
            economics = _economics(fill, observation, evidence)
        except (TypeError, ValueError) as error:
            return self.not_calculable(observation_id, [str(error)])
        result = {
            "schema_version": DAILY_POSITION_VALUE_SCHEMA_VERSION,
            "calculation_version": DAILY_POSITION_VALUE_CALCULATION_VERSION,
            "result_id": _result_id(observation["observation_id"]),
            "status": "CALCULATED",
            "scope": "SIMULATED_CORPORATE_ACTION_COMPLETE_DAILY_POSITION_VALUE",
            "simulation_only": True,
            "currency": "USD",
            "calculated_at": calculated.isoformat(),
            "market_session_date": observation["market_session_date"],
            "effective_at": observation["price_effective_at"],
            "portfolio_version": fill["portfolio_version"],
            "ticker": fill["ticker"],
            "fill_id": fill["fill_id"],
            "execution_record_hash": fill["record_hash"],
            "observation_id": observation["observation_id"],
            "observation_record_hash": observation["record_hash"],
            "corporate_action_evidence_id": evidence["evidence_id"],
            "corporate_action_record_hash": evidence["record_hash"],
            "contains_backfilled_close_evidence": observation["availability"] != "CONTEMPORANEOUS",
            "contains_post_close_corporate_action_evidence": (
                _as_datetime(evidence["retrieved_at"]) > _as_datetime(observation["price_effective_at"])
            ),
            "daily_position_value_calculated": True,
            "daily_portfolio_valuation_calculated": False,
            "performance_metric_calculated": False,
            "recommendation_provided": False,
            "risk_adjusted": False,
            "annualized": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "strategy_version": fill["strategy_version"],
            "model_versions": fill["model_versions"],
            "git_revision": fill["git_revision"],
            **economics,
            "formula": dict(FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def _pinned_support(
        self, record: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None, Mapping[str, Any] | None, list[str]]:
        observations, observation_reasons = resolve_pinned_records(
            self.observation_ledger.verify(),
            [record.get("observation_id")],
            [record.get("observation_record_hash")],
            id_field="observation_id",
            label="daily-close",
        )
        evidence, evidence_reasons = resolve_pinned_records(
            self.corporate_action_ledger.verify(),
            [record.get("corporate_action_evidence_id")],
            [record.get("corporate_action_record_hash")],
            id_field="evidence_id",
            label="corporate-action",
        )
        observation = observations[0] if observations else None
        action = evidence[0] if evidence else None
        fill = next(
            (
                item
                for item in self.observation_ledger.execution_ledger.verify()
                if item.get("fill_id") == record.get("fill_id")
            ),
            None,
        )
        reasons = [*observation_reasons, *evidence_reasons]
        if fill is None:
            reasons.append("Pinned simulated fill is missing.")
        if observation is not None and observation.get("fill_id") != record.get("fill_id"):
            reasons.append("Pinned close does not belong to the selected fill.")
        if action is not None and action.get("fill_id") != record.get("fill_id"):
            reasons.append("Pinned corporate-action evidence belongs to another fill.")
        return fill, observation, action, reasons

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(f"Daily-position-value chain is broken at record {index}.")
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Daily-position-value record {index} has been modified.")
            fill, observation, evidence, reasons = self._pinned_support(record)
            if reasons or fill is None or observation is None or evidence is None:
                raise LedgerIntegrityError(
                    f"Daily-position-value record {index} lost supporting evidence."
                )
            try:
                economics = _economics(fill, observation, evidence)
                calculated = _as_datetime(record.get("calculated_at"))
                latest = max(
                    _as_datetime(observation["recorded_at"]),
                    _as_datetime(evidence["retrieved_at"]),
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Daily-position-value record {index} has invalid values."
                ) from error
            expected_id = _result_id(observation["observation_id"])
            boundary = (
                record.get("schema_version") == DAILY_POSITION_VALUE_SCHEMA_VERSION
                and record.get("calculation_version") == DAILY_POSITION_VALUE_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope") == "SIMULATED_CORPORATE_ACTION_COMPLETE_DAILY_POSITION_VALUE"
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and record.get("market_session_date") == observation["market_session_date"]
                and record.get("effective_at") == observation["price_effective_at"]
                and record.get("portfolio_version") == fill["portfolio_version"]
                and record.get("ticker") == fill["ticker"]
                and record.get("execution_record_hash") == fill["record_hash"]
                and record.get("contains_backfilled_close_evidence")
                == (observation["availability"] != "CONTEMPORANEOUS")
                and record.get("contains_post_close_corporate_action_evidence")
                == (_as_datetime(evidence["retrieved_at"]) > _as_datetime(observation["price_effective_at"]))
                and record.get("daily_position_value_calculated") is True
                and record.get("daily_portfolio_valuation_calculated") is False
                and record.get("performance_metric_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("risk_adjusted") is False
                and record.get("annualized") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("strategy_version") == fill["strategy_version"]
                and record.get("model_versions") == fill["model_versions"]
                and record.get("git_revision") == fill["git_revision"]
                and record.get("formula") == FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and calculated >= latest
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Daily-position-value record {index} violates its boundary."
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
            existing = next((item for item in records if item["result_id"] == result["result_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash", "calculated_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Daily position value {result['result_id']} already exists."
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
