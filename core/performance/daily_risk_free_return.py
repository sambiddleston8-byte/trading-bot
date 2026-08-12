from __future__ import annotations

"""Exact SOFR Index return matched to one authoritative daily portfolio period."""

from datetime import date, datetime, timedelta, timezone
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.daily_portfolio_return import DailyPortfolioReturnLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import (
    _canonical_json,
    _decimal_string,
    _fraction,
    _fraction_material,
    _record_hash,
    _write_all,
)
from core.performance.risk_free_index_observation import RiskFreeIndexObservationLedger


DAILY_RISK_FREE_RETURN_SCHEMA_VERSION = "1.0"
DAILY_RISK_FREE_RETURN_CALCULATION_VERSION = "matched-sofr-index-ratio-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
FORMULA = {
    "risk_free_growth": "current_sofr_index / previous_sofr_index",
    "daily_risk_free_return": "risk_free_growth - 1",
    "period_matching": "EXACT_PREVIOUS_AND_CURRENT_PORTFOLIO_MARKET_SESSION_DATES",
    "arithmetic_policy": "EXACT_RATIONAL_WITH_34_DIGIT_DECIMAL_PRESENTATION",
}


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _result_id(portfolio_version: str, daily_return_id: str) -> str:
    material = [
        portfolio_version,
        daily_return_id,
        DAILY_RISK_FREE_RETURN_CALCULATION_VERSION,
    ]
    return "DRFRET-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(
    previous_observation: Mapping[str, Any],
    current_observation: Mapping[str, Any],
) -> dict[str, Any]:
    previous = _fraction(
        previous_observation["exact_index_value"], "previous SOFR Index"
    )
    current = _fraction(current_observation["exact_index_value"], "current SOFR Index")
    if previous <= 0 or current <= 0:
        raise ValueError("SOFR Index observations must remain positive")
    growth = current / previous
    period_return = growth - 1
    previous_date = date.fromisoformat(previous_observation["value_date"])
    current_date = date.fromisoformat(current_observation["value_date"])
    elapsed_days = (current_date - previous_date).days
    if elapsed_days <= 0:
        raise ValueError("SOFR Index observation dates must strictly increase")
    return {
        "elapsed_calendar_days": elapsed_days,
        "previous_sofr_index": _decimal_string(previous),
        "current_sofr_index": _decimal_string(current),
        "risk_free_growth": _decimal_string(growth),
        "daily_risk_free_return": _decimal_string(period_return),
        "exact_fractions": {
            "previous_sofr_index": _fraction_material(previous),
            "current_sofr_index": _fraction_material(current),
            "risk_free_growth": _fraction_material(growth),
            "daily_risk_free_return": _fraction_material(period_return),
        },
    }


class DailyRiskFreeReturnLedger:
    """Append-only risk-free period returns; no risk-adjusted metric is calculated."""

    def __init__(
        self,
        path: str | Path,
        daily_return_ledger: DailyPortfolioReturnLedger,
        index_observation_ledger: RiskFreeIndexObservationLedger,
    ) -> None:
        self.path = Path(path)
        self.daily_return_ledger = daily_return_ledger
        self.index_observation_ledger = index_observation_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Daily-risk-free-return ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank daily-risk-free-return line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at daily-risk-free-return line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Daily-risk-free-return line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_calculable(version: str, daily_return_id: str, reasons: Sequence[str]):
        return {
            "status": "NOT_CALCULABLE",
            "portfolio_version": version,
            "daily_portfolio_return_id": daily_return_id,
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "daily_risk_free_return_calculated": False,
            "excess_return_calculated": False,
            "sharpe_calculated": False,
            "sortino_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
        }

    def _support(self, version: str, daily_return_id: str):
        daily_return = next(
            (
                item
                for item in self.daily_return_ledger.verify()
                if item.get("portfolio_version") == version
                and item.get("result_id") == daily_return_id
                and item.get("daily_return_calculated") is True
            ),
            None,
        )
        reasons = []
        if daily_return is None:
            reasons.append("Verified daily portfolio return is missing.")
            return None, None, None, reasons
        observations = self.index_observation_ledger.verify()
        by_date = {item["value_date"]: item for item in observations}
        previous = by_date.get(daily_return["previous_market_session_date"])
        current = by_date.get(daily_return["current_market_session_date"])
        if previous is None:
            reasons.append("Final SOFR Index evidence is missing for the previous period date.")
        if current is None:
            reasons.append("Final SOFR Index evidence is missing for the current period date.")
        return daily_return, previous, current, reasons

    def calculate(
        self,
        *,
        portfolio_version: str,
        daily_portfolio_return_id: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        daily_id = str(daily_portfolio_return_id or "").strip()
        daily_return, previous, current, reasons = self._support(version, daily_id)
        if reasons or daily_return is None or previous is None or current is None:
            return self.not_calculable(version, daily_id, reasons)
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest = max(
            _as_datetime(daily_return["calculated_at"]),
            _as_datetime(previous["recorded_at"]),
            _as_datetime(current["recorded_at"]),
        )
        if calculated < latest:
            return self.not_calculable(
                version, daily_id, ["calculated_at cannot predate supporting evidence."]
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(
                version, daily_id, ["calculated_at cannot be in the future."]
            )
        try:
            economics = _economics(previous, current)
        except (KeyError, TypeError, ValueError) as error:
            return self.not_calculable(version, daily_id, [str(error)])
        result = {
            "schema_version": DAILY_RISK_FREE_RETURN_SCHEMA_VERSION,
            "calculation_version": DAILY_RISK_FREE_RETURN_CALCULATION_VERSION,
            "result_id": _result_id(version, daily_id),
            "status": "CALCULATED",
            "scope": "SIMULATED_PORTFOLIO_PERIOD_MATCHED_SOFR_INDEX_RETURN",
            "simulation_only": True,
            "currency": "USD",
            "calculated_at": calculated.isoformat(),
            "portfolio_version": version,
            "daily_portfolio_return_id": daily_return["result_id"],
            "daily_portfolio_return_record_hash": daily_return["record_hash"],
            "previous_market_session_date": daily_return[
                "previous_market_session_date"
            ],
            "current_market_session_date": daily_return["current_market_session_date"],
            "previous_index_observation_id": previous["observation_id"],
            "previous_index_observation_record_hash": previous["record_hash"],
            "current_index_observation_id": current["observation_id"],
            "current_index_observation_record_hash": current["record_hash"],
            "source_series": "SOFR_INDEX",
            "source_provider": "FEDERAL_RESERVE_BANK_OF_NEW_YORK",
            "source_backfilled": any(
                item["availability"] == "BACKFILLED_FINAL"
                for item in (previous, current)
            ),
            "daily_risk_free_return_calculated": True,
            "excess_return_calculated": False,
            "sharpe_calculated": False,
            "sortino_calculated": False,
            "annualized": False,
            "risk_adjusted_metric_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
            "strategy_version": daily_return["strategy_version"],
            "model_versions": daily_return["model_versions"],
            "git_revision": daily_return["git_revision"],
            **economics,
            "formula": dict(FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(
                    f"Daily-risk-free-return record {index} has been modified."
                )
            daily_return, expected_previous, expected_current, reasons = self._support(
                str(record.get("portfolio_version") or ""),
                str(record.get("daily_portfolio_return_id") or ""),
            )
            observations = self.index_observation_ledger.verify()
            previous, previous_reasons = resolve_pinned_records(
                observations,
                [record.get("previous_index_observation_id")],
                [record.get("previous_index_observation_record_hash")],
                id_field="observation_id",
                label="previous SOFR Index observation",
            )
            current, current_reasons = resolve_pinned_records(
                observations,
                [record.get("current_index_observation_id")],
                [record.get("current_index_observation_record_hash")],
                id_field="observation_id",
                label="current SOFR Index observation",
            )
            if (
                reasons
                or previous_reasons
                or current_reasons
                or daily_return is None
                or not previous
                or not current
                or previous[0] != expected_previous
                or current[0] != expected_current
            ):
                raise LedgerIntegrityError(
                    f"Daily-risk-free-return record {index} lost supporting evidence."
                )
            try:
                economics = _economics(previous[0], current[0])
                calculated = _as_datetime(record["calculated_at"])
                latest = max(
                    _as_datetime(daily_return["calculated_at"]),
                    _as_datetime(previous[0]["recorded_at"]),
                    _as_datetime(current[0]["recorded_at"]),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Daily-risk-free-return record {index} has invalid values."
                ) from error
            expected_id = _result_id(record["portfolio_version"], daily_return["result_id"])
            boundary = (
                record.get("schema_version") == DAILY_RISK_FREE_RETURN_SCHEMA_VERSION
                and record.get("calculation_version")
                == DAILY_RISK_FREE_RETURN_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope")
                == "SIMULATED_PORTFOLIO_PERIOD_MATCHED_SOFR_INDEX_RETURN"
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and record.get("daily_portfolio_return_record_hash")
                == daily_return["record_hash"]
                and record.get("previous_market_session_date")
                == daily_return["previous_market_session_date"]
                and record.get("current_market_session_date")
                == daily_return["current_market_session_date"]
                and record.get("source_series") == "SOFR_INDEX"
                and record.get("source_provider")
                == "FEDERAL_RESERVE_BANK_OF_NEW_YORK"
                and record.get("source_backfilled")
                == any(
                    item["availability"] == "BACKFILLED_FINAL"
                    for item in (previous[0], current[0])
                )
                and record.get("daily_risk_free_return_calculated") is True
                and record.get("excess_return_calculated") is False
                and record.get("sharpe_calculated") is False
                and record.get("sortino_calculated") is False
                and record.get("annualized") is False
                and record.get("risk_adjusted_metric_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("live_trading_enabled") is False
                and record.get("strategy_version") == daily_return["strategy_version"]
                and record.get("model_versions") == daily_return["model_versions"]
                and record.get("git_revision") == daily_return["git_revision"]
                and record.get("formula") == FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and calculated >= latest
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Daily-risk-free-return record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
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
                (item for item in records if item["result_id"] == result["result_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "calculated_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Daily risk-free return {result['result_id']} already exists."
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
