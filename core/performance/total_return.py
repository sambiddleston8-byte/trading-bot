from __future__ import annotations

"""Deterministic simulated long total returns from immutable evidence."""

from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
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
from core.performance.outcome_observation import OutcomeObservationLedger


TOTAL_RETURN_SCHEMA_VERSION = "1.0"
TOTAL_RETURN_CALCULATION_VERSION = "simulated-total-return-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
CALCULATION_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
TOTAL_RETURN_FORMULA = {
    "position_value": "split_adjusted_quantity * outcome_unadjusted_price",
    "gross_dividend_cash": (
        "sum(quantity_immediately_before_ex_time * gross_amount_per_share)"
    ),
    "gross_outcome_value": "position_value + gross_dividend_cash",
    "recorded_entry_cost": (
        "initial_quantity * simulated_fill_price + recorded_entry_fee"
    ),
    "gross_total_return_after_entry_fee_excl_exit": (
        "(gross_outcome_value - recorded_entry_cost) / recorded_entry_cost"
    ),
    "dividend_policy": "GROSS_USD_CASH_NO_REINVESTMENT_BEFORE_TAX",
    "entitlement_policy": "FILL_TIME_STRICTLY_BEFORE_EX_TIME",
    "payment_policy": "FACE_VALUE_INCLUDED_ONLY_IF_PAID_BY_OUTCOME_TIME",
    "fractional_share_policy": "RETAIN_EXACT_SIMULATED_FRACTIONAL_QUANTITY",
    "cash_in_lieu_policy": "NOT_INCLUDED",
    "exit_fee_policy": "NOT_INCLUDED_NO_EXIT_EXECUTION_RECORDED",
    "slippage_policy": "ENTRY_SLIPPAGE_ALREADY_EMBEDDED_IN_FILL_PRICE",
    "benchmark_policy": "PRICE_RETURN_CONTEXT_ONLY_NOT_LIKE_FOR_LIKE",
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
            raise OSError("Unable to complete append-only total-return write")
        written += count


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _decimal(value: Any, name: str, *, allow_zero: bool = False) -> Decimal:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite() or resolved < 0 or (resolved == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be a {qualifier} finite decimal")
    return resolved


def _decimal_string(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _fraction(value: Decimal) -> Fraction:
    return Fraction(value)


def _fraction_material(value: Fraction) -> dict[str, str]:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _fraction_decimal_string(value: Fraction) -> str:
    with localcontext(CALCULATION_CONTEXT):
        resolved = Decimal(value.numerator) / Decimal(value.denominator)
    return _decimal_string(resolved)


def _event_time(event: Mapping[str, Any]) -> datetime:
    field = "ex_at" if event.get("event_type") == "CASH_DIVIDEND" else "effective_at"
    return _as_datetime(event.get(field))


def _result_id(fill_id: str, horizon: str) -> str:
    key = _canonical_json([fill_id, horizon, TOTAL_RETURN_CALCULATION_VERSION])
    return "TRET-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32].upper()


def _relevant_events(
    evidence: Mapping[str, Any], *, fill_at: datetime, outcome_at: datetime
) -> list[dict[str, Any]]:
    events = [
        dict(event)
        for event in evidence.get("events", [])
        if fill_at <= _event_time(event) <= outcome_at
    ]
    events.sort(
        key=lambda item: (
            _event_time(item), item["event_type"], item["source_event_id"]
        )
    )
    return events


def _economics(
    *,
    fill: Mapping[str, Any],
    entry: Mapping[str, Any],
    outcome: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    fill_at = _as_datetime(fill["filled_at"])
    outcome_at = _as_datetime(outcome["asset_price_effective_at"])
    if evidence.get("completeness_status") != "COMPLETE":
        raise ValueError("Corporate-action evidence must be COMPLETE.")
    if _as_datetime(evidence["covers_from_at"]) > fill_at:
        raise ValueError("Corporate-action evidence does not cover the fill time.")
    if _as_datetime(evidence["through_at"]) < outcome_at:
        raise ValueError("Corporate-action evidence does not cover the outcome time.")

    events = _relevant_events(evidence, fill_at=fill_at, outcome_at=outcome_at)
    unsupported = [event for event in events if event["event_type"] == "OTHER"]
    if unsupported:
        raise ValueError("Relevant unsupported corporate actions block total return.")
    if any(_event_time(event) == fill_at for event in events):
        raise ValueError("A corporate action at the exact fill time is ambiguous.")

    by_time: dict[datetime, list[dict[str, Any]]] = {}
    for event in events:
        by_time.setdefault(_event_time(event), []).append(event)
    for same_time in by_time.values():
        split_count = sum(
            event["event_type"] == "STOCK_SPLIT" for event in same_time
        )
        if split_count and len(same_time) > 1:
            raise ValueError(
                "Simultaneous split and distribution ordering is ambiguous."
            )

    with localcontext(CALCULATION_CONTEXT):
        initial_quantity = _fraction(
            _decimal(fill["filled_quantity"], "filled_quantity")
        )
        entry_price = _fraction(_decimal(entry["asset_price"], "entry_price"))
        outcome_price = _fraction(_decimal(outcome["asset_price"], "outcome_price"))
        entry_fee = _fraction(
            _decimal(fill["fees"], "entry_fee", allow_zero=True)
        )
        entry_benchmark = _fraction(
            _decimal(entry["benchmark_price"], "entry_benchmark_price")
        )
        outcome_benchmark = _fraction(
            _decimal(outcome["benchmark_price"], "outcome_benchmark_price")
        )
        quantity = initial_quantity
        dividend_cash = Fraction(0)
        applied: list[dict[str, Any]] = []
        for event in events:
            if event["event_type"] == "STOCK_SPLIT":
                numerator = _fraction(
                    _decimal(event["numerator"], "split numerator")
                )
                denominator = _fraction(
                    _decimal(event["denominator"], "split denominator")
                )
                before = quantity
                quantity = quantity * numerator / denominator
                applied.append(
                    {
                        "event_type": "STOCK_SPLIT",
                        "source_event_id": event["source_event_id"],
                        "effective_at": event["effective_at"],
                        "numerator": event["numerator"],
                        "denominator": event["denominator"],
                        "quantity_before": _fraction_decimal_string(before),
                        "quantity_after": _fraction_decimal_string(quantity),
                        "quantity_before_fraction": _fraction_material(before),
                        "quantity_after_fraction": _fraction_material(quantity),
                    }
                )
            elif event["event_type"] == "CASH_DIVIDEND":
                ex_at = _as_datetime(event["ex_at"])
                if fill_at >= ex_at:
                    raise ValueError("The fill is not clearly entitled to a dividend.")
                if event.get("payment_at") is None:
                    raise ValueError("Dividend payment_at is required for total return.")
                payment_at = _as_datetime(event["payment_at"])
                if payment_at > outcome_at:
                    raise ValueError(
                        "An entitled dividend was not paid by the outcome time."
                    )
                if event.get("currency") != "USD":
                    raise ValueError("Only USD dividends are supported without FX evidence.")
                amount = _fraction(
                    _decimal(event["amount_per_share"], "amount_per_share")
                )
                cash = quantity * amount
                dividend_cash += cash
                applied.append(
                    {
                        "event_type": "CASH_DIVIDEND",
                        "source_event_id": event["source_event_id"],
                        "ex_at": event["ex_at"],
                        "payment_at": event["payment_at"],
                        "amount_per_share": event["amount_per_share"],
                        "currency": "USD",
                        "entitled_quantity": _fraction_decimal_string(quantity),
                        "entitled_quantity_fraction": _fraction_material(quantity),
                        "gross_cash": _fraction_decimal_string(cash),
                        "gross_cash_fraction": _fraction_material(cash),
                    }
                )

        gross_entry_value = initial_quantity * entry_price
        recorded_entry_cost = gross_entry_value + entry_fee
        outcome_position_value = quantity * outcome_price
        gross_outcome_value = outcome_position_value + dividend_cash
        total_return = (gross_outcome_value - recorded_entry_cost) / recorded_entry_cost
        benchmark_price_return = outcome_benchmark / entry_benchmark - Fraction(1)
        exact_fractions = {
            "initial_quantity": _fraction_material(initial_quantity),
            "split_adjusted_quantity": _fraction_material(quantity),
            "entry_price": _fraction_material(entry_price),
            "outcome_price": _fraction_material(outcome_price),
            "recorded_entry_fee": _fraction_material(entry_fee),
            "gross_entry_value": _fraction_material(gross_entry_value),
            "recorded_entry_cost": _fraction_material(recorded_entry_cost),
            "outcome_position_value": _fraction_material(outcome_position_value),
            "gross_dividend_cash": _fraction_material(dividend_cash),
            "gross_outcome_value": _fraction_material(gross_outcome_value),
            "gross_total_return_after_entry_fee_excl_exit": _fraction_material(
                total_return
            ),
            "entry_benchmark_price": _fraction_material(entry_benchmark),
            "outcome_benchmark_price": _fraction_material(outcome_benchmark),
            "benchmark_price_return_context": _fraction_material(
                benchmark_price_return
            ),
        }
        return {
            "initial_quantity": _fraction_decimal_string(initial_quantity),
            "split_adjusted_quantity": _fraction_decimal_string(quantity),
            "entry_price": _fraction_decimal_string(entry_price),
            "outcome_price": _fraction_decimal_string(outcome_price),
            "recorded_entry_fee": _fraction_decimal_string(entry_fee),
            "gross_entry_value": _fraction_decimal_string(gross_entry_value),
            "recorded_entry_cost": _fraction_decimal_string(recorded_entry_cost),
            "outcome_position_value": _fraction_decimal_string(outcome_position_value),
            "gross_dividend_cash": _fraction_decimal_string(dividend_cash),
            "gross_outcome_value": _fraction_decimal_string(gross_outcome_value),
            "gross_total_return_after_entry_fee_excl_exit": _fraction_decimal_string(
                total_return
            ),
            "entry_benchmark_price": _fraction_decimal_string(entry_benchmark),
            "outcome_benchmark_price": _fraction_decimal_string(outcome_benchmark),
            "benchmark_price_return_context": _fraction_decimal_string(
                benchmark_price_return
            ),
            "exact_fractions": exact_fractions,
            "events_applied": applied,
        }


class TotalReturnLedger:
    """Append-only simulated holding-period total returns; never a track record."""

    def __init__(
        self,
        path: str | Path,
        observation_ledger: OutcomeObservationLedger,
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
                "Total-return ledger has an incomplete final line; run explicit tail repair."
            )
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank total-return line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at total-return line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Total-return line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_calculable(fill_id: str, horizon: str, reasons: Sequence[str]) -> dict[str, Any]:
        return {
            "status": "NOT_CALCULABLE",
            "fill_id": str(fill_id),
            "horizon": str(horizon).upper(),
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "learning_eligible": False,
            "portfolio_performance_claim": False,
            "track_record_claim": False,
        }

    def calculate(
        self,
        *,
        fill_id: str,
        horizon: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        observations = self.observation_ledger.verify()
        fills = {
            item["fill_id"]: item
            for item in self.observation_ledger.execution_ledger.verify()
        }
        resolved_horizon = str(horizon or "").upper()
        reasons: list[str] = []
        if resolved_horizon == "ENTRY":
            reasons.append("ENTRY is a baseline, not a total-return horizon.")
        entry = next(
            (
                item
                for item in observations
                if item["fill_id"] == fill_id and item["horizon"] == "ENTRY"
            ),
            None,
        )
        outcome = next(
            (
                item
                for item in observations
                if item["fill_id"] == fill_id
                and item["horizon"] == resolved_horizon
            ),
            None,
        )
        fill = fills.get(str(fill_id))
        if fill is None:
            reasons.append("Verified local simulated fill is missing.")
        elif fill.get("side") != "BUY":
            reasons.append("Total return currently supports simulated long BUY fills only.")
        if entry is None:
            reasons.append("Verified ENTRY observation is missing.")
        if outcome is None and resolved_horizon != "ENTRY":
            reasons.append("Verified due-horizon observation is missing.")
        if entry is not None and entry.get("benchmark_price_basis") != "UNADJUSTED_CLOSE":
            reasons.append("ENTRY benchmark basis must be UNADJUSTED_CLOSE.")
        if outcome is not None and (
            outcome.get("asset_price_basis") != "UNADJUSTED_CLOSE"
            or outcome.get("benchmark_price_basis") != "UNADJUSTED_CLOSE"
        ):
            reasons.append("Outcome prices must use UNADJUSTED_CLOSE.")
        if reasons:
            return self.not_calculable(fill_id, resolved_horizon, reasons)

        assert fill is not None and entry is not None and outcome is not None
        outcome_at = _as_datetime(outcome["asset_price_effective_at"])
        evidence = self.corporate_action_ledger.evidence_for(
            fill_id=fill_id, through_at=outcome_at
        )
        if evidence is None:
            return self.not_calculable(
                fill_id,
                resolved_horizon,
                ["Complete corporate-action evidence is missing."],
            )
        resolved_calculated_at = _as_datetime(
            calculated_at or datetime.now(timezone.utc)
        )
        latest_evidence_at = max(
            _as_datetime(entry["retrieved_at"]),
            _as_datetime(outcome["retrieved_at"]),
            _as_datetime(evidence["retrieved_at"]),
        )
        if resolved_calculated_at < latest_evidence_at:
            return self.not_calculable(
                fill_id,
                resolved_horizon,
                ["calculated_at cannot predate supporting evidence."],
            )
        if resolved_calculated_at > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                fill_id, resolved_horizon, ["calculated_at cannot be in the future."]
            )
        try:
            economics = _economics(
                fill=fill,
                entry=entry,
                outcome=outcome,
                evidence=evidence,
            )
        except ValueError as error:
            return self.not_calculable(fill_id, resolved_horizon, [str(error)])
        result = {
            "schema_version": TOTAL_RETURN_SCHEMA_VERSION,
            "calculation_version": TOTAL_RETURN_CALCULATION_VERSION,
            "result_id": _result_id(fill_id, resolved_horizon),
            "status": "CALCULATED",
            "scope": "SIMULATED_LONG_HOLDING_PERIOD_TOTAL_RETURN",
            "return_unit": "DECIMAL_STRING",
            "currency": "USD",
            "simulation_only": True,
            "portfolio_performance_claim": False,
            "track_record_claim": False,
            "learning_eligible": False,
            "relative_total_return_calculated": False,
            "alpha_calculated": False,
            "calculated_at": resolved_calculated_at.isoformat(),
            "fill_id": fill_id,
            "execution_record_hash": fill["record_hash"],
            "entry_observation_id": entry["observation_id"],
            "entry_observation_hash": entry["record_hash"],
            "outcome_observation_id": outcome["observation_id"],
            "outcome_observation_hash": outcome["record_hash"],
            "corporate_action_evidence_id": evidence["evidence_id"],
            "corporate_action_evidence_hash": evidence["record_hash"],
            "order_id": fill["order_id"],
            "decision_id": fill["decision_id"],
            "portfolio_version": fill["portfolio_version"],
            "ticker": fill["ticker"],
            "horizon": resolved_horizon,
            "horizon_label": outcome["horizon_label"],
            "benchmark_ticker": entry["benchmark_ticker"],
            "benchmark_comparison_status": "PRICE_RETURN_ONLY_NOT_LIKE_FOR_LIKE",
            "recorded_entry_slippage_bps": fill["slippage_bps"],
            **economics,
            "formula": TOTAL_RETURN_FORMULA,
            "strategy_version": fill["strategy_version"],
            "model_versions": fill["model_versions"],
            "git_revision": fill["git_revision"],
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        observations = {
            item["observation_id"]: item for item in self.observation_ledger.verify()
        }
        fills = {
            item["fill_id"]: item
            for item in self.observation_ledger.execution_ledger.verify()
        }
        evidence_records = {
            item["evidence_id"]: item for item in self.corporate_action_ledger.verify()
        }
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Total-return chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Total-return record {index} has been modified."
                )
            result_id = str(record.get("result_id") or "")
            fill_id = str(record.get("fill_id") or "")
            horizon = str(record.get("horizon") or "")
            fill = fills.get(fill_id)
            entry = observations.get(record.get("entry_observation_id"))
            outcome = observations.get(record.get("outcome_observation_id"))
            evidence = evidence_records.get(record.get("corporate_action_evidence_id"))
            if fill is None or entry is None or outcome is None or evidence is None:
                raise LedgerIntegrityError(
                    f"Total-return record {index} lost linked evidence."
                )
            expected_id = _result_id(fill_id, horizon)
            linked = (
                record.get("execution_record_hash") == fill.get("record_hash")
                and record.get("entry_observation_hash") == entry.get("record_hash")
                and record.get("outcome_observation_hash") == outcome.get("record_hash")
                and record.get("corporate_action_evidence_hash")
                == evidence.get("record_hash")
                and entry.get("fill_id") == fill_id
                and entry.get("horizon") == "ENTRY"
                and outcome.get("fill_id") == fill_id
                and outcome.get("horizon") == horizon
                and record.get("horizon_label") == outcome.get("horizon_label")
                and evidence.get("fill_id") == fill_id
                and record.get("order_id") == fill.get("order_id")
                and record.get("decision_id") == fill.get("decision_id")
                and record.get("portfolio_version") == fill.get("portfolio_version")
                and record.get("ticker") == fill.get("ticker")
                and record.get("strategy_version") == fill.get("strategy_version")
                and record.get("model_versions") == fill.get("model_versions")
                and record.get("git_revision") == fill.get("git_revision")
                and record.get("benchmark_ticker") == entry.get("benchmark_ticker")
                and record.get("benchmark_ticker") == outcome.get("benchmark_ticker")
            )
            boundary = (
                record.get("schema_version") == TOTAL_RETURN_SCHEMA_VERSION
                and record.get("calculation_version")
                == TOTAL_RETURN_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope")
                == "SIMULATED_LONG_HOLDING_PERIOD_TOTAL_RETURN"
                and record.get("return_unit") == "DECIMAL_STRING"
                and record.get("currency") == "USD"
                and record.get("simulation_only") is True
                and record.get("portfolio_performance_claim") is False
                and record.get("track_record_claim") is False
                and record.get("learning_eligible") is False
                and record.get("relative_total_return_calculated") is False
                and record.get("alpha_calculated") is False
                and record.get("benchmark_comparison_status")
                == "PRICE_RETURN_ONLY_NOT_LIKE_FOR_LIKE"
                and record.get("formula") == TOTAL_RETURN_FORMULA
                and fill.get("side") == "BUY"
                and entry.get("benchmark_price_basis") == "UNADJUSTED_CLOSE"
                and outcome.get("asset_price_basis") == "UNADJUSTED_CLOSE"
                and outcome.get("benchmark_price_basis") == "UNADJUSTED_CLOSE"
            )
            try:
                calculated_at = _as_datetime(record.get("calculated_at"))
                economics = _economics(
                    fill=fill,
                    entry=entry,
                    outcome=outcome,
                    evidence=evidence,
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Total-return record {index} has invalid evidence or values."
                ) from error
            latest_evidence_at = max(
                _as_datetime(entry["retrieved_at"]),
                _as_datetime(outcome["retrieved_at"]),
                _as_datetime(evidence["retrieved_at"]),
            )
            calculations = (
                calculated_at >= latest_evidence_at
                and calculated_at <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and all(record.get(key) == value for key, value in economics.items())
                and record.get("recorded_entry_slippage_bps")
                == fill.get("slippage_bps")
            )
            if not linked or not boundary or not calculations:
                raise LedgerIntegrityError(
                    f"Total-return record {index} violates its boundary."
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
                    f"Total-return result {result['result_id']} already exists."
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
