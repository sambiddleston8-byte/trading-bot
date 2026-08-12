from __future__ import annotations

"""Deterministic price-return outcomes from verified raw observations."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.outcome_observation import OutcomeObservationLedger


OUTCOME_RESULT_SCHEMA_VERSION = "1.0"
CALCULATION_VERSION = "price-return-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
RESULT_FORMULA = {
    "asset_price_return": "outcome_price / entry_price - 1",
    "benchmark_price_return": (
        "outcome_benchmark_price / entry_benchmark_price - 1"
    ),
    "entry_fee_adjusted_long_return_excl_exit": (
        "(quantity * outcome_price - (quantity * entry_price + "
        "recorded_entry_fee)) / (quantity * entry_price + recorded_entry_fee)"
    ),
    "exit_fee_policy": "NOT_INCLUDED_NO_EXIT_EXECUTION_RECORDED",
    "slippage_policy": "ENTRY_SLIPPAGE_ALREADY_EMBEDDED_IN_FILL_PRICE",
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
            raise OSError("Unable to complete append-only outcome-result write")
        written += count


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _positive(value: Any, name: str) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a positive finite number") from error
    if not math.isfinite(resolved) or resolved <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return resolved


def _nonnegative(value: Any, name: str) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a non-negative finite number") from error
    if not math.isfinite(resolved) or resolved < 0:
        raise ValueError(f"{name} must be a non-negative finite number")
    return resolved


def _finite(value: Any, name: str) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(resolved):
        raise ValueError(f"{name} must be a finite number")
    return resolved


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _result_id(fill_id: str, horizon: str) -> str:
    key = _canonical_json([fill_id, horizon, CALCULATION_VERSION])
    return "OUT-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32].upper()


def _corporate_action_check(
    value: Mapping[str, Any], *, entry_at: datetime, horizon_at: datetime
) -> dict[str, Any]:
    check = dict(value)
    if str(check.get("status") or "").upper() != "NO_EVENTS":
        raise ValueError("corporate-action status must be NO_EVENTS")
    source = _required(check.get("data_source"), "corporate-action data_source")
    source_version = _required(
        check.get("source_version"), "corporate-action source_version"
    )
    checked_at = _as_datetime(check.get("checked_at"))
    covers_from_at = _as_datetime(check.get("covers_from_at"))
    through_at = _as_datetime(check.get("through_at"))
    now = datetime.now(timezone.utc)
    if checked_at > now + MAX_CLOCK_SKEW:
        raise ValueError("corporate-action checked_at cannot be in the future")
    if covers_from_at > entry_at:
        raise ValueError("corporate-action check does not cover the entry date")
    if through_at < horizon_at:
        raise ValueError("corporate-action check does not cover the outcome horizon")
    if checked_at < through_at:
        raise ValueError("corporate-action checked_at cannot predate through_at")
    material = {
        "status": "NO_EVENTS",
        "data_source": source,
        "source_version": source_version,
        "checked_at": checked_at.isoformat(),
        "covers_from_at": covers_from_at.isoformat(),
        "through_at": through_at.isoformat(),
    }
    return {
        **material,
        "evidence_sha256": hashlib.sha256(
            _canonical_json(material).encode("utf-8")
        ).hexdigest(),
    }


class OutcomeResultLedger:
    """Append-only price-return results; never a total-return track record."""

    def __init__(
        self,
        path: str | Path,
        observation_ledger: OutcomeObservationLedger,
    ) -> None:
        self.path = Path(path)
        self.observation_ledger = observation_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Outcome result ledger has an incomplete final line; run explicit tail repair."
            )
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank outcome result line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at outcome result line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Outcome result line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_calculable(fill_id: str, horizon: str, reasons: list[str]) -> dict[str, Any]:
        return {
            "status": "NOT_CALCULABLE",
            "fill_id": str(fill_id),
            "horizon": str(horizon).upper(),
            "reasons": reasons,
            "record_appended": False,
            "learning_eligible": False,
            "portfolio_performance_claim": False,
        }

    def calculate(
        self,
        *,
        fill_id: str,
        horizon: str,
        corporate_action_check: Mapping[str, Any] | None,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        observations = self.observation_ledger.verify()
        resolved_horizon = str(horizon or "").upper()
        reasons: list[str] = []
        if resolved_horizon == "ENTRY":
            reasons.append("ENTRY is a baseline, not an outcome horizon.")
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
                if item["fill_id"] == fill_id and item["horizon"] == resolved_horizon
            ),
            None,
        )
        if entry is None:
            reasons.append("Verified ENTRY observation is missing.")
        if outcome is None and resolved_horizon != "ENTRY":
            reasons.append("Verified due-horizon observation is missing.")
        fills = {
            item["fill_id"]: item
            for item in self.observation_ledger.execution_ledger.verify()
        }
        fill = fills.get(str(fill_id))
        if fill is None:
            reasons.append("Verified local simulated fill is missing.")
        elif fill.get("side") != "BUY":
            reasons.append("This first outcome calculator supports long BUY fills only.")
        if entry is not None and entry.get("benchmark_price_basis") != "UNADJUSTED_CLOSE":
            reasons.append("ENTRY benchmark basis must be UNADJUSTED_CLOSE.")
        if outcome is not None and (
            outcome.get("asset_price_basis") != "UNADJUSTED_CLOSE"
            or outcome.get("benchmark_price_basis") != "UNADJUSTED_CLOSE"
        ):
            reasons.append("Outcome prices must use UNADJUSTED_CLOSE.")
        if reasons:
            return self.not_calculable(fill_id, resolved_horizon, reasons)

        assert entry is not None and outcome is not None and fill is not None
        try:
            action_check = _corporate_action_check(
                corporate_action_check or {},
                entry_at=_as_datetime(entry["asset_price_effective_at"]),
                horizon_at=_as_datetime(outcome["asset_price_effective_at"]),
            )
        except ValueError as error:
            return self.not_calculable(fill_id, resolved_horizon, [str(error)])

        quantity = _positive(fill["filled_quantity"], "filled_quantity")
        entry_price = _positive(entry["asset_price"], "entry asset_price")
        outcome_price = _positive(outcome["asset_price"], "outcome asset_price")
        entry_benchmark = _positive(entry["benchmark_price"], "entry benchmark_price")
        outcome_benchmark = _positive(
            outcome["benchmark_price"], "outcome benchmark_price"
        )
        entry_fees = _nonnegative(fill["fees"], "entry fees")
        gross_entry_value = quantity * entry_price
        recorded_entry_cost = gross_entry_value + entry_fees
        gross_outcome_value = quantity * outcome_price
        asset_price_return = outcome_price / entry_price - 1.0
        benchmark_price_return = outcome_benchmark / entry_benchmark - 1.0
        entry_fee_adjusted_long_return_excl_exit = (
            gross_outcome_value - recorded_entry_cost
        ) / recorded_entry_cost
        resolved_calculated_at = _as_datetime(
            calculated_at or datetime.now(timezone.utc)
        )
        latest_evidence_at = max(
            _as_datetime(entry["retrieved_at"]),
            _as_datetime(outcome["retrieved_at"]),
            _as_datetime(action_check["checked_at"]),
        )
        if resolved_calculated_at < latest_evidence_at:
            return self.not_calculable(
                fill_id,
                resolved_horizon,
                ["calculated_at cannot predate the latest supporting evidence."],
            )
        if resolved_calculated_at > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                fill_id,
                resolved_horizon,
                ["calculated_at cannot be in the future."],
            )
        result = {
            "schema_version": OUTCOME_RESULT_SCHEMA_VERSION,
            "calculation_version": CALCULATION_VERSION,
            "result_id": _result_id(fill_id, resolved_horizon),
            "status": "CALCULATED",
            "scope": "PRICE_RETURN_ONLY",
            "return_unit": "DECIMAL",
            "portfolio_performance_claim": False,
            "total_return_claim": False,
            "learning_eligible": False,
            "calculated_at": resolved_calculated_at.isoformat(),
            "fill_id": fill_id,
            "execution_record_hash": fill["record_hash"],
            "entry_observation_id": entry["observation_id"],
            "entry_observation_hash": entry["record_hash"],
            "outcome_observation_id": outcome["observation_id"],
            "outcome_observation_hash": outcome["record_hash"],
            "order_id": fill["order_id"],
            "decision_id": fill["decision_id"],
            "portfolio_version": fill["portfolio_version"],
            "ticker": fill["ticker"],
            "horizon": resolved_horizon,
            "horizon_label": outcome["horizon_label"],
            "quantity": quantity,
            "entry_price": entry_price,
            "outcome_price": outcome_price,
            "entry_benchmark_price": entry_benchmark,
            "outcome_benchmark_price": outcome_benchmark,
            "benchmark_ticker": entry["benchmark_ticker"],
            "recorded_entry_fee": entry_fees,
            "recorded_entry_slippage_bps": fill["slippage_bps"],
            "gross_entry_value": gross_entry_value,
            "recorded_entry_cost": recorded_entry_cost,
            "gross_outcome_value": gross_outcome_value,
            "asset_price_return": asset_price_return,
            "benchmark_price_return": benchmark_price_return,
            "benchmark_relative_price_return": (
                asset_price_return - benchmark_price_return
            ),
            "entry_fee_adjusted_long_return_excl_exit": (
                entry_fee_adjusted_long_return_excl_exit
            ),
            "entry_fee_adjusted_relative_return_excl_exit": (
                entry_fee_adjusted_long_return_excl_exit - benchmark_price_return
            ),
            "corporate_action_check": action_check,
            "price_basis": "UNADJUSTED_CLOSE",
            "formula": RESULT_FORMULA,
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
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(f"Outcome result chain broke at record {index}.")
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Outcome result {index} has been modified.")
            result_id = str(record.get("result_id") or "")
            fill_id = str(record.get("fill_id") or "")
            horizon = str(record.get("horizon") or "")
            if result_id != _result_id(fill_id, horizon) or result_id in seen_ids:
                raise LedgerIntegrityError(f"Invalid outcome result identity at {index}.")
            fill = fills.get(fill_id)
            entry = observations.get(record.get("entry_observation_id"))
            outcome = observations.get(record.get("outcome_observation_id"))
            if fill is None or entry is None or outcome is None:
                raise LedgerIntegrityError(f"Outcome result {index} lost linked evidence.")
            linked = (
                record.get("execution_record_hash") == fill.get("record_hash")
                and record.get("entry_observation_hash") == entry.get("record_hash")
                and record.get("outcome_observation_hash") == outcome.get("record_hash")
                and entry.get("fill_id") == fill_id
                and entry.get("horizon") == "ENTRY"
                and outcome.get("fill_id") == fill_id
                and outcome.get("horizon") == horizon
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
                record.get("schema_version") == OUTCOME_RESULT_SCHEMA_VERSION
                and record.get("calculation_version") == CALCULATION_VERSION
                and record.get("status") == "CALCULATED"
                and record.get("scope") == "PRICE_RETURN_ONLY"
                and record.get("return_unit") == "DECIMAL"
                and record.get("portfolio_performance_claim") is False
                and record.get("total_return_claim") is False
                and record.get("learning_eligible") is False
                and record.get("price_basis") == "UNADJUSTED_CLOSE"
                and fill.get("side") == "BUY"
            )
            try:
                action_check = _corporate_action_check(
                    record.get("corporate_action_check") or {},
                    entry_at=_as_datetime(entry["asset_price_effective_at"]),
                    horizon_at=_as_datetime(outcome["asset_price_effective_at"]),
                )
                quantity = _positive(record.get("quantity"), "quantity")
                entry_price = _positive(record.get("entry_price"), "entry_price")
                outcome_price = _positive(record.get("outcome_price"), "outcome_price")
                entry_benchmark = _positive(
                    record.get("entry_benchmark_price"), "entry_benchmark_price"
                )
                outcome_benchmark = _positive(
                    record.get("outcome_benchmark_price"), "outcome_benchmark_price"
                )
                fee = _nonnegative(record.get("recorded_entry_fee"), "fee")
                calculated_at = _as_datetime(record.get("calculated_at"))
                recorded_entry_value = _finite(
                    record.get("gross_entry_value"), "gross_entry_value"
                )
                recorded_entry_cost = _finite(
                    record.get("recorded_entry_cost"), "recorded_entry_cost"
                )
                recorded_outcome_value = _finite(
                    record.get("gross_outcome_value"), "gross_outcome_value"
                )
                recorded_asset_return = _finite(
                    record.get("asset_price_return"), "asset_price_return"
                )
                recorded_benchmark_return = _finite(
                    record.get("benchmark_price_return"), "benchmark_price_return"
                )
                recorded_relative_return = _finite(
                    record.get("benchmark_relative_price_return"),
                    "benchmark_relative_price_return",
                )
                recorded_fee_return = _finite(
                    record.get("entry_fee_adjusted_long_return_excl_exit"),
                    "entry_fee_adjusted_long_return_excl_exit",
                )
                recorded_fee_relative = _finite(
                    record.get("entry_fee_adjusted_relative_return_excl_exit"),
                    "entry_fee_adjusted_relative_return_excl_exit",
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Invalid outcome values at {index}.") from error
            expected_entry_value = quantity * entry_price
            expected_entry_cost = expected_entry_value + fee
            expected_outcome_value = quantity * outcome_price
            expected_asset_return = outcome_price / entry_price - 1.0
            expected_benchmark_return = outcome_benchmark / entry_benchmark - 1.0
            expected_fee_return = (
                expected_outcome_value - expected_entry_cost
            ) / expected_entry_cost
            source_values = (
                quantity == float(fill["filled_quantity"])
                and entry_price == float(entry["asset_price"])
                and outcome_price == float(outcome["asset_price"])
                and entry_benchmark == float(entry["benchmark_price"])
                and outcome_benchmark == float(outcome["benchmark_price"])
                and fee == float(fill["fees"])
                and record.get("recorded_entry_slippage_bps")
                == fill.get("slippage_bps")
                and record.get("formula") == RESULT_FORMULA
                and calculated_at
                >= max(
                    _as_datetime(entry["retrieved_at"]),
                    _as_datetime(outcome["retrieved_at"]),
                    _as_datetime(action_check["checked_at"]),
                )
                and calculated_at <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            calculations = (
                source_values
                and math.isclose(recorded_entry_value, expected_entry_value)
                and math.isclose(recorded_entry_cost, expected_entry_cost)
                and math.isclose(recorded_outcome_value, expected_outcome_value)
                and math.isclose(recorded_asset_return, expected_asset_return)
                and math.isclose(
                    recorded_benchmark_return, expected_benchmark_return
                )
                and math.isclose(
                    recorded_relative_return,
                    expected_asset_return - expected_benchmark_return,
                )
                and math.isclose(
                    recorded_fee_return,
                    expected_fee_return,
                )
                and math.isclose(
                    recorded_fee_relative,
                    expected_fee_return - expected_benchmark_return,
                )
                and record.get("corporate_action_check") == action_check
            )
            if not linked or not boundary or not calculations:
                raise LedgerIntegrityError(f"Outcome result {index} violates its boundary.")
            seen_ids.add(result_id)
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
                raise LedgerIntegrityError(f"Outcome result {result['result_id']} exists.")
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
