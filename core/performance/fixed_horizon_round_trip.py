from __future__ import annotations

"""Complete fixed-horizon prediction outcomes from verified round trips."""

from datetime import datetime, timedelta, timezone
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import (
    _canonical_json,
    _decimal_string,
    _fraction,
    _fraction_material,
    _record_hash,
    _write_all,
)
from core.performance.prediction_outcome_pair import PredictionOutcomePairLedger
from core.performance.round_trip_execution import SimulatedRoundTripExecutionLedger


FIXED_HORIZON_ROUND_TRIP_SCHEMA_VERSION = "1.0"
FIXED_HORIZON_ROUND_TRIP_CALCULATION_VERSION = "prediction-fixed-horizon-net-round-trip-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
FORMULA = {
    "net_outcome_value": "recorded_exit_proceeds + gross_paid_dividend_cash",
    "net_total_return": "(net_outcome_value - recorded_entry_cost) / recorded_entry_cost",
    "prediction_error": "net_total_return - predicted_expected_return",
    "exit_timing": "EXIT_FILL_TIME_EXACTLY_EQUALS_FIXED_HORIZON_ASSET_PRICE_EFFECTIVE_TIME",
    "exit_price": "EXIT_FILL_PRICE_EXACTLY_EQUALS_FIXED_HORIZON_OBSERVED_ASSET_PRICE",
    "dividend_policy": "GROSS_USD_PAID_CASH_PRE_TAX_NO_REINVESTMENT",
    "arithmetic_policy": "EXACT_RATIONAL_WITH_34_DIGIT_DECIMAL_PRESENTATION",
}


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _result_id(pair_id: str, round_trip_id: str) -> str:
    material = [pair_id, round_trip_id, FIXED_HORIZON_ROUND_TRIP_CALCULATION_VERSION]
    return "FHRT-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(pair: Mapping[str, Any], total: Mapping[str, Any], trip: Mapping[str, Any]):
    predicted = _fraction(pair["exact_fractions"]["predicted_expected_return"], "predicted return")
    entry_cost = _fraction(trip["exact_fractions"]["recorded_entry_cost"], "round-trip entry cost")
    total_entry_cost = _fraction(total["exact_fractions"]["recorded_entry_cost"], "total-return entry cost")
    if entry_cost <= 0 or entry_cost != total_entry_cost:
        raise ValueError("Round-trip and fixed-horizon entry cost must reconcile exactly")
    trip_quantity = _fraction(trip["exact_fractions"]["filled_quantity"], "round-trip quantity")
    outcome_quantity = _fraction(total["exact_fractions"]["split_adjusted_quantity"], "outcome quantity")
    if trip_quantity != outcome_quantity:
        raise ValueError("Exit quantity must equal the fixed-horizon split-adjusted quantity")
    exit_proceeds = _fraction(trip["exact_fractions"]["recorded_exit_proceeds"], "exit proceeds")
    dividends = _fraction(total["exact_fractions"]["gross_dividend_cash"], "gross dividend cash")
    outcome_value = exit_proceeds + dividends
    profit = outcome_value - entry_cost
    net_return = profit / entry_cost
    error = net_return - predicted
    exact = {
        "predicted_expected_return": predicted,
        "recorded_entry_cost": entry_cost,
        "recorded_exit_proceeds": exit_proceeds,
        "gross_paid_dividend_cash": dividends,
        "net_outcome_value": outcome_value,
        "net_profit_or_loss": profit,
        "net_total_return_after_entry_and_exit_fees": net_return,
        "prediction_error": error,
    }
    return {
        **{key: _decimal_string(value) for key, value in exact.items()},
        "exact_fractions": {key: _fraction_material(value) for key, value in exact.items()},
    }


class FixedHorizonRoundTripOutcomeLedger:
    """Append-only complete prediction outcome; no cohort score is calculated."""

    def __init__(
        self,
        path: str | Path,
        prediction_pair_ledger: PredictionOutcomePairLedger,
        round_trip_ledger: SimulatedRoundTripExecutionLedger,
    ) -> None:
        self.path = Path(path)
        self.prediction_pair_ledger = prediction_pair_ledger
        self.round_trip_ledger = round_trip_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Fixed-horizon-round-trip ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank fixed-horizon round-trip line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at fixed-horizon round-trip line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Fixed-horizon round-trip line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_pairable(pair_id: str, round_trip_id: str, reasons: Sequence[str]):
        return {
            "status": "NOT_PAIRABLE",
            "prediction_pair_id": str(pair_id),
            "round_trip_result_id": str(round_trip_id),
            "reasons": list(reasons),
            "record_appended": False,
            "complete_round_trip": False,
            "hit_rate_calculated": False,
            "calibration_calculated": False,
            "recommendation_provided": False,
            "order_submission_enabled": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
        }

    def _support(self, pair_id: str, round_trip_id: str):
        pair = next(
            (item for item in self.prediction_pair_ledger.verify() if item.get("pair_id") == pair_id),
            None,
        )
        trip = next(
            (item for item in self.round_trip_ledger.verify() if item.get("result_id") == round_trip_id),
            None,
        )
        reasons = []
        total = None
        observation = None
        exit_fill = None
        if pair is None:
            reasons.append("Verified prediction/outcome pair is missing.")
        if trip is None:
            reasons.append("Verified complete simulated round trip is missing.")
        if pair is not None:
            total = next(
                (
                    item for item in self.prediction_pair_ledger.total_return_ledger.verify()
                    if item.get("result_id") == pair.get("total_return_result_id")
                ),
                None,
            )
            if total is None:
                reasons.append("Pinned fixed-horizon total return is missing.")
            else:
                observation = next(
                    (
                        item for item in self.prediction_pair_ledger.total_return_ledger.observation_ledger.verify()
                        if item.get("observation_id") == total.get("outcome_observation_id")
                    ),
                    None,
                )
                if observation is None:
                    reasons.append("Pinned fixed-horizon outcome observation is missing.")
        if trip is not None:
            exit_fill = next(
                (
                    item for item in self.round_trip_ledger.execution_ledger.verify()
                    if item.get("fill_id") == trip.get("exit_fill_id")
                ),
                None,
            )
            if exit_fill is None:
                reasons.append("Pinned simulated exit fill is missing.")
        if pair is not None and trip is not None and total is not None and observation is not None and exit_fill is not None:
            identity_fields = (
                "decision_id", "portfolio_version", "ticker", "strategy_version",
                "model_versions", "git_revision",
            )
            if any(pair.get(field) != trip.get(field) for field in identity_fields):
                reasons.append("Prediction and round trip must share complete identity.")
            if pair.get("fill_id") != trip.get("entry_fill_id"):
                reasons.append("Prediction entry fill must equal the round-trip entry fill.")
            if exit_fill.get("filled_at") != observation.get("asset_price_effective_at"):
                reasons.append("Exit fill time must exactly match the fixed-horizon observation time.")
            try:
                exit_gross = _fraction(trip["exact_fractions"]["exit_fill_gross_value"], "exit gross")
                quantity = _fraction(trip["exact_fractions"]["filled_quantity"], "exit quantity")
                price = Fraction(str(observation["asset_price"]))
                if exit_gross != quantity * price:
                    reasons.append("Exit fill price must exactly match the fixed-horizon observed price.")
                _economics(pair, total, trip)
            except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
                reasons.append(str(error))
        return pair, trip, total, observation, exit_fill, sorted(set(reasons))

    def pair(
        self,
        *,
        prediction_pair_id: str,
        round_trip_result_id: str,
        paired_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        pair_id = str(prediction_pair_id or "").strip()
        trip_id = str(round_trip_result_id or "").strip()
        pair, trip, total, observation, exit_fill, reasons = self._support(pair_id, trip_id)
        if reasons or any(item is None for item in (pair, trip, total, observation, exit_fill)):
            return self.not_pairable(pair_id, trip_id, reasons)
        resolved_at = _as_datetime(paired_at or datetime.now(timezone.utc))
        latest = max(_as_datetime(pair["paired_at"]), _as_datetime(trip["calculated_at"]))
        if resolved_at < latest:
            return self.not_pairable(pair_id, trip_id, ["paired_at cannot predate supporting evidence."])
        if resolved_at > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_pairable(pair_id, trip_id, ["paired_at cannot be in the future."])
        economics = _economics(pair, total, trip)
        result = {
            "schema_version": FIXED_HORIZON_ROUND_TRIP_SCHEMA_VERSION,
            "calculation_version": FIXED_HORIZON_ROUND_TRIP_CALCULATION_VERSION,
            "result_id": _result_id(pair_id, trip_id),
            "status": "PAIRED",
            "record_type": "COMPLETE_FIXED_HORIZON_PREDICTION_ROUND_TRIP_OUTCOME",
            "simulation_only": True,
            "paired_at": resolved_at.isoformat(),
            "prediction_pair_id": pair_id,
            "prediction_pair_record_hash": pair["record_hash"],
            "round_trip_result_id": trip_id,
            "round_trip_record_hash": trip["record_hash"],
            "total_return_result_id": total["result_id"],
            "total_return_record_hash": total["record_hash"],
            "outcome_observation_id": observation["observation_id"],
            "outcome_observation_record_hash": observation["record_hash"],
            "decision_id": pair["decision_id"],
            "portfolio_version": pair["portfolio_version"],
            "ticker": pair["ticker"],
            "horizon": pair["horizon"],
            "horizon_label": pair["horizon_label"],
            "entry_fill_id": trip["entry_fill_id"],
            "exit_fill_id": trip["exit_fill_id"],
            "exit_filled_at": trip["exit_filled_at"],
            "complete_round_trip": True,
            "entry_and_exit_fees_included": True,
            "gross_dividends_included_pre_tax": True,
            "success_rule_applied": False,
            "confidence_bucket_applied": False,
            "expected_return_bucket_applied": False,
            "hit_rate_calculated": False,
            "calibration_calculated": False,
            "recommendation_provided": False,
            "order_submission_enabled": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
            "strategy_version": pair["strategy_version"],
            "model_versions": pair["model_versions"],
            "git_revision": pair["git_revision"],
            **economics,
            "formula": dict(FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        seen_pairs = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Fixed-horizon round-trip record {index} has been modified.")
            pair, trip, total, observation, exit_fill, reasons = self._support(
                str(record.get("prediction_pair_id") or ""),
                str(record.get("round_trip_result_id") or ""),
            )
            if reasons or any(item is None for item in (pair, trip, total, observation, exit_fill)):
                raise LedgerIntegrityError(f"Fixed-horizon round-trip record {index} lost support.")
            try:
                economics = _economics(pair, total, trip)
                paired = _as_datetime(record["paired_at"])
                latest = max(_as_datetime(pair["paired_at"]), _as_datetime(trip["calculated_at"]))
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Fixed-horizon round-trip record {index} has invalid values.") from error
            expected_id = _result_id(pair["pair_id"], trip["result_id"])
            boundary = (
                record.get("schema_version") == FIXED_HORIZON_ROUND_TRIP_SCHEMA_VERSION
                and record.get("calculation_version") == FIXED_HORIZON_ROUND_TRIP_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and pair["pair_id"] not in seen_pairs
                and record.get("status") == "PAIRED"
                and record.get("record_type") == "COMPLETE_FIXED_HORIZON_PREDICTION_ROUND_TRIP_OUTCOME"
                and record.get("simulation_only") is True
                and record.get("prediction_pair_record_hash") == pair["record_hash"]
                and record.get("round_trip_record_hash") == trip["record_hash"]
                and record.get("total_return_result_id") == total["result_id"]
                and record.get("total_return_record_hash") == total["record_hash"]
                and record.get("outcome_observation_id") == observation["observation_id"]
                and record.get("outcome_observation_record_hash") == observation["record_hash"]
                and all(record.get(field) == pair.get(field) for field in (
                    "decision_id", "portfolio_version", "ticker", "horizon", "horizon_label",
                    "strategy_version", "model_versions", "git_revision",
                ))
                and record.get("entry_fill_id") == trip["entry_fill_id"]
                and record.get("exit_fill_id") == trip["exit_fill_id"]
                and record.get("exit_filled_at") == trip["exit_filled_at"]
                and record.get("complete_round_trip") is True
                and record.get("entry_and_exit_fees_included") is True
                and record.get("gross_dividends_included_pre_tax") is True
                and record.get("success_rule_applied") is False
                and record.get("confidence_bucket_applied") is False
                and record.get("expected_return_bucket_applied") is False
                and record.get("hit_rate_calculated") is False
                and record.get("calibration_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("order_submission_enabled") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("live_trading_enabled") is False
                and record.get("formula") == FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and paired >= latest
                and paired <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(f"Fixed-horizon round-trip record {index} violates its boundary.")
            seen_ids.add(expected_id)
            seen_pairs.add(pair["pair_id"])
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next((item for item in records if item["result_id"] == result["result_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash", "paired_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(f"Fixed-horizon round trip {result['result_id']} already exists.")
            if any(item["prediction_pair_id"] == result["prediction_pair_id"] for item in records):
                raise LedgerIntegrityError("A complete round trip is already paired to this prediction horizon.")
            material = {**result, "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH}
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
                backup = self.path.with_suffix(self.path.suffix + f".incomplete-tail-{uuid4().hex}")
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
