from __future__ import annotations

"""Immutable raw prediction/outcome pairs; no cohort score is calculated."""

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

from core.decision_ledger import (
    GENESIS_HASH,
    InvestmentDecisionLedger,
    LedgerIntegrityError,
    canonical_timestamp,
)
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import (
    _canonical_json,
    _decimal_string,
    _fraction,
    _fraction_material,
    _record_hash,
    _write_all,
)
from core.performance.total_return import TotalReturnLedger


PREDICTION_OUTCOME_SCHEMA_VERSION = "1.0"
PREDICTION_OUTCOME_PAIRING_VERSION = "decision-fixed-horizon-total-return-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
CANONICAL_HORIZON_DAYS = {
    "1_DAY": 1,
    "1_WEEK": 7,
    "1_MONTH": 30,
    "3_MONTHS": 91,
    "6_MONTHS": 183,
    "12_MONTHS": 365,
    "24_MONTHS": 730,
}
FORMULA = {
    "prediction_error": "actual_total_return - predicted_expected_return",
    "confidence_unit": "SCORE_0_TO_100",
    "predicted_return_unit": "DECIMAL_RETURN",
    "actual_return_unit": "DECIMAL_RETURN",
    "horizon_matching": "DECLARED_EXPECTED_RETURN_HORIZON_DAYS_TO_CANONICAL_OUTCOME_HORIZON",
    "actual_return_basis": "GROSS_TOTAL_RETURN_AFTER_ENTRY_FEE_EXCLUDING_EXIT_EXECUTION_COST",
}


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _decimal_fraction(value: Any, name: str) -> Fraction:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite():
        raise ValueError(f"{name} must be a finite decimal")
    return Fraction(resolved)


def _confidence(value: Any) -> Fraction:
    resolved = _decimal_fraction(value, "confidence")
    if resolved < 0 or resolved > 100:
        raise ValueError("confidence must use the declared 0-to-100 score")
    return resolved


def _expected_return(value: Any) -> Fraction:
    resolved = _decimal_fraction(value, "expected_return")
    if resolved < -1:
        raise ValueError("expected_return cannot be below a total loss")
    return resolved


def _horizon_days(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("expected_return_horizon_days must be a positive integer")
    try:
        resolved = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("expected_return_horizon_days must be a positive integer") from error
    if resolved <= 0 or str(resolved) != str(value).strip().removesuffix(".0"):
        raise ValueError("expected_return_horizon_days must be a positive integer")
    return resolved


def _pair_id(decision_id: str, horizon: str) -> str:
    material = [decision_id, horizon, PREDICTION_OUTCOME_PAIRING_VERSION]
    return "POPAIR-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(
    decision: Mapping[str, Any], total_return: Mapping[str, Any]
) -> dict[str, Any]:
    payload = decision.get("decision_payload") or {}
    predicted = _expected_return(payload.get("expected_return"))
    confidence = _confidence(payload.get("confidence"))
    horizon_days = _horizon_days(payload.get("expected_return_horizon_days"))
    horizon = str(total_return.get("horizon") or "").upper()
    expected_days = CANONICAL_HORIZON_DAYS.get(horizon)
    if expected_days is None or horizon_days != expected_days:
        raise ValueError(
            "declared expected-return horizon does not match the fixed outcome horizon"
        )
    actual = _fraction(
        total_return["exact_fractions"][
            "gross_total_return_after_entry_fee_excl_exit"
        ],
        "actual total return",
    )
    error = actual - predicted
    exact = {
        "predicted_expected_return": predicted,
        "predicted_confidence_score": confidence,
        "actual_total_return": actual,
        "prediction_error": error,
    }
    return {
        "predicted_expected_return": _decimal_string(predicted),
        "predicted_confidence_score": _decimal_string(confidence),
        "predicted_horizon_days": horizon_days,
        "actual_total_return": _decimal_string(actual),
        "prediction_error": _decimal_string(error),
        "exact_fractions": {
            key: _fraction_material(value) for key, value in exact.items()
        },
    }


class PredictionOutcomePairLedger:
    """Append-only raw cohort units linked to decisions and total-return evidence."""

    def __init__(
        self,
        path: str | Path,
        decision_ledger: InvestmentDecisionLedger,
        total_return_ledger: TotalReturnLedger,
    ) -> None:
        self.path = Path(path)
        self.decision_ledger = decision_ledger
        self.total_return_ledger = total_return_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Prediction-outcome ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank prediction-outcome line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at prediction-outcome line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Prediction-outcome line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_pairable(decision_id: str, result_id: str, reasons: Sequence[str]):
        return {
            "status": "NOT_PAIRABLE",
            "decision_id": decision_id,
            "total_return_result_id": result_id,
            "reasons": list(reasons),
            "record_appended": False,
            "hit_rate_calculated": False,
            "calibration_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
        }

    def _support(self, decision_id: str, result_id: str):
        decision = next(
            (
                item
                for item in self.decision_ledger.verify()
                if item.get("decision_id") == decision_id
            ),
            None,
        )
        total_return = next(
            (
                item
                for item in self.total_return_ledger.verify()
                if item.get("result_id") == result_id
            ),
            None,
        )
        reasons = []
        fill = None
        if decision is None:
            reasons.append("Verified timestamped investment decision is missing.")
        if total_return is None:
            reasons.append("Verified fixed-horizon total return is missing.")
        if decision is not None and total_return is not None:
            if total_return.get("decision_id") != decision_id:
                reasons.append("Total return is linked to a different decision.")
            fills = {
                item["fill_id"]: item
                for item in self.total_return_ledger.observation_ledger.execution_ledger.verify()
            }
            fill = fills.get(total_return.get("fill_id"))
            if fill is None:
                reasons.append("Verified simulated fill is missing.")
            identity_fields = (
                "portfolio_version",
                "ticker",
                "model_versions",
                "git_revision",
            )
            if any(
                decision.get(field) != total_return.get(field)
                for field in identity_fields
            ):
                reasons.append(
                    "Decision and outcome must share portfolio, ticker, model and Git identity."
                )
            try:
                decided = _as_datetime(decision["decided_at"])
                data_as_of = _as_datetime(decision["data_as_of"])
                if data_as_of > decided:
                    reasons.append("Decision data_as_of cannot postdate the decision.")
                if fill is not None and decided > _as_datetime(fill["filled_at"]):
                    reasons.append("Decision must predate its simulated fill.")
                _economics(decision, total_return)
            except (KeyError, TypeError, ValueError) as error:
                reasons.append(str(error))
        return decision, total_return, fill, sorted(set(reasons))

    def pair(
        self,
        *,
        decision_id: str,
        total_return_result_id: str,
        paired_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        decision_key = _required(decision_id, "decision_id")
        result_key = _required(total_return_result_id, "total_return_result_id")
        decision, total_return, fill, reasons = self._support(decision_key, result_key)
        if reasons or decision is None or total_return is None or fill is None:
            return self.not_pairable(decision_key, result_key, reasons)
        paired = _as_datetime(paired_at or datetime.now(timezone.utc))
        latest = max(
            _as_datetime(total_return["calculated_at"]), _as_datetime(fill["filled_at"])
        )
        if paired < latest:
            return self.not_pairable(
                decision_key, result_key, ["paired_at cannot predate supporting evidence."]
            )
        if paired > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_pairable(
                decision_key, result_key, ["paired_at cannot be in the future."]
            )
        economics = _economics(decision, total_return)
        horizon = total_return["horizon"]
        result = {
            "schema_version": PREDICTION_OUTCOME_SCHEMA_VERSION,
            "pairing_version": PREDICTION_OUTCOME_PAIRING_VERSION,
            "pair_id": _pair_id(decision_key, horizon),
            "status": "PAIRED",
            "record_type": "RAW_FIXED_HORIZON_PREDICTION_OUTCOME_EVIDENCE",
            "simulation_only": True,
            "paired_at": paired.isoformat(),
            "decision_id": decision_key,
            "decision_record_hash": decision["record_hash"],
            "decision": decision["decision"],
            "decided_at": decision["decided_at"],
            "data_as_of": decision["data_as_of"],
            "total_return_result_id": result_key,
            "total_return_record_hash": total_return["record_hash"],
            "fill_id": fill["fill_id"],
            "fill_record_hash": fill["record_hash"],
            "portfolio_version": decision["portfolio_version"],
            "ticker": decision["ticker"],
            "horizon": horizon,
            "horizon_label": total_return["horizon_label"],
            "actual_return_basis": (
                "GROSS_TOTAL_RETURN_AFTER_ENTRY_FEE_EXCLUDING_EXIT_EXECUTION_COST"
            ),
            "complete_round_trip": False,
            "exit_execution_cost_included": False,
            "success_rule_applied": False,
            "confidence_bucket_applied": False,
            "expected_return_bucket_applied": False,
            "hit_rate_calculated": False,
            "calibration_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
            "strategy_version": total_return["strategy_version"],
            "model_versions": total_return["model_versions"],
            "git_revision": total_return["git_revision"],
            **economics,
            "formula": dict(FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        seen_units = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(
                    f"Prediction-outcome record {index} has been modified."
                )
            decision, total_return, fill, reasons = self._support(
                str(record.get("decision_id") or ""),
                str(record.get("total_return_result_id") or ""),
            )
            decisions, decision_reasons = resolve_pinned_records(
                self.decision_ledger.verify(),
                [record.get("decision_id")],
                [record.get("decision_record_hash")],
                id_field="decision_id",
                label="investment decision",
            )
            total_returns, return_reasons = resolve_pinned_records(
                self.total_return_ledger.verify(),
                [record.get("total_return_result_id")],
                [record.get("total_return_record_hash")],
                id_field="result_id",
                label="total return",
            )
            if (
                reasons
                or decision_reasons
                or return_reasons
                or decision is None
                or total_return is None
                or fill is None
                or decisions != [decision]
                or total_returns != [total_return]
            ):
                raise LedgerIntegrityError(
                    f"Prediction-outcome record {index} lost supporting evidence."
                )
            try:
                economics = _economics(decision, total_return)
                paired = _as_datetime(record["paired_at"])
                latest = max(
                    _as_datetime(total_return["calculated_at"]),
                    _as_datetime(fill["filled_at"]),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Prediction-outcome record {index} has invalid values."
                ) from error
            expected_id = _pair_id(decision["decision_id"], total_return["horizon"])
            unit = (decision["decision_id"], total_return["horizon"])
            boundary = (
                record.get("schema_version") == PREDICTION_OUTCOME_SCHEMA_VERSION
                and record.get("pairing_version") == PREDICTION_OUTCOME_PAIRING_VERSION
                and record.get("pair_id") == expected_id
                and expected_id not in seen_ids
                and unit not in seen_units
                and record.get("status") == "PAIRED"
                and record.get("record_type")
                == "RAW_FIXED_HORIZON_PREDICTION_OUTCOME_EVIDENCE"
                and record.get("simulation_only") is True
                and record.get("decision_record_hash") == decision["record_hash"]
                and record.get("decision") == decision["decision"]
                and record.get("decided_at") == decision["decided_at"]
                and record.get("data_as_of") == decision["data_as_of"]
                and record.get("total_return_record_hash") == total_return["record_hash"]
                and record.get("fill_id") == fill["fill_id"]
                and record.get("fill_record_hash") == fill["record_hash"]
                and record.get("portfolio_version") == decision["portfolio_version"]
                and record.get("ticker") == decision["ticker"]
                and record.get("horizon") == total_return["horizon"]
                and record.get("horizon_label") == total_return["horizon_label"]
                and record.get("actual_return_basis")
                == "GROSS_TOTAL_RETURN_AFTER_ENTRY_FEE_EXCLUDING_EXIT_EXECUTION_COST"
                and record.get("complete_round_trip") is False
                and record.get("exit_execution_cost_included") is False
                and record.get("success_rule_applied") is False
                and record.get("confidence_bucket_applied") is False
                and record.get("expected_return_bucket_applied") is False
                and record.get("hit_rate_calculated") is False
                and record.get("calibration_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("live_trading_enabled") is False
                and record.get("strategy_version") == total_return["strategy_version"]
                and record.get("model_versions") == total_return["model_versions"]
                and record.get("git_revision") == total_return["git_revision"]
                and record.get("formula") == FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and paired >= latest
                and paired <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Prediction-outcome record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            seen_units.add(unit)
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["pair_id"] == result["pair_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "paired_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Prediction/outcome pair {result['pair_id']} already exists."
                )
            if any(
                item["decision_id"] == result["decision_id"]
                and item["horizon"] == result["horizon"]
                for item in records
            ):
                raise LedgerIntegrityError(
                    "A different outcome is already paired to this decision and horizon."
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
