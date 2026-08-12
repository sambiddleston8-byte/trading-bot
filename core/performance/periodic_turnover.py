from __future__ import annotations

"""Verified simulated gross turnover between consecutive portfolio valuations."""

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
from core.performance.daily_portfolio_valuation import DailyPortfolioValuationLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import (
    _canonical_json,
    _decimal_string,
    _fraction,
    _fraction_material,
    _record_hash,
    _write_all,
)


PERIODIC_TURNOVER_SCHEMA_VERSION = "1.0"
PERIODIC_TURNOVER_CALCULATION_VERSION = "verified-gross-two-way-periodic-turnover-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
FORMULA = {
    "interval": "(previous_valuation_effective_at, current_valuation_effective_at]",
    "total_absolute_gross_trade_notional": "sum(abs(simulated_fill_gross_value))",
    "average_boundary_equity": "(previous_total_equity + current_total_equity) / 2",
    "gross_period_turnover": "total_absolute_gross_trade_notional / average_boundary_equity",
    "convention": "GROSS_TWO_WAY_BUYS_PLUS_SELLS_NON_ANNUALIZED",
    "initial_deployment": "EXCLUDED_BECAUSE_TRADES_AT_OR_BEFORE_FIRST_VALUATION_ARE_OUTSIDE_INTERVAL",
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


def _result_id(portfolio_version: str, current_valuation_id: str) -> str:
    material = [portfolio_version, current_valuation_id, PERIODIC_TURNOVER_CALCULATION_VERSION]
    return "PTURN-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    fills: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    previous_equity = _fraction(previous["exact_fractions"]["total_equity"], "previous equity")
    current_equity = _fraction(current["exact_fractions"]["total_equity"], "current equity")
    if previous_equity <= 0 or current_equity <= 0:
        raise ValueError("Both boundary valuations must have positive total equity")
    buy_notional = Fraction(0)
    sell_notional = Fraction(0)
    fees = Fraction(0)
    for fill in fills:
        gross = _decimal_fraction(fill.get("gross_value"), "fill gross value")
        fee = _decimal_fraction(fill.get("fees"), "fill fee", allow_zero=True)
        side = fill.get("side")
        if side == "BUY":
            buy_notional += gross
        elif side == "SELL":
            sell_notional += gross
        else:
            raise ValueError("Every turnover fill must have BUY or SELL side")
        fees += fee
    total_notional = buy_notional + sell_notional
    average_equity = (previous_equity + current_equity) / 2
    turnover = total_notional / average_equity
    exact = {
        "previous_boundary_total_equity": previous_equity,
        "current_boundary_total_equity": current_equity,
        "average_boundary_equity": average_equity,
        "gross_buy_notional": buy_notional,
        "gross_sell_notional": sell_notional,
        "total_absolute_gross_trade_notional": total_notional,
        "recorded_execution_fees": fees,
        "gross_period_turnover": turnover,
    }
    return {
        **{key: _decimal_string(value) for key, value in exact.items()},
        "exact_fractions": {key: _fraction_material(value) for key, value in exact.items()},
        "execution_count": len(fills),
        "buy_execution_count": sum(item.get("side") == "BUY" for item in fills),
        "sell_execution_count": sum(item.get("side") == "SELL" for item in fills),
    }


class PeriodicTurnoverLedger:
    """Append-only gross turnover evidence; never submits or recommends an order."""

    def __init__(self, path: str | Path, valuation_ledger: DailyPortfolioValuationLedger) -> None:
        self.path = Path(path)
        self.valuation_ledger = valuation_ledger

    @property
    def execution_ledger(self):
        return self.valuation_ledger.position_value_ledger.observation_ledger.execution_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Periodic-turnover ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank periodic-turnover line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at periodic-turnover line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Periodic-turnover line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def not_calculable(portfolio_version: str, current_valuation_id: str, reasons: Sequence[str]):
        return {
            "status": "NOT_CALCULABLE",
            "portfolio_version": str(portfolio_version),
            "current_valuation_id": str(current_valuation_id),
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "turnover_calculated": False,
            "annualized": False,
            "recommendation_provided": False,
            "order_submission_enabled": False,
            "learning_eligible": False,
            "track_record_claim": False,
        }

    def _support(self, portfolio_version: str, current_valuation_id: str):
        valuations = sorted(
            (
                item for item in self.valuation_ledger.verify()
                if item.get("portfolio_version") == portfolio_version
            ),
            key=lambda item: _as_datetime(item["effective_at"]),
        )
        index = next(
            (position for position, item in enumerate(valuations)
             if item.get("valuation_id") == current_valuation_id),
            None,
        )
        if index is None:
            return None, None, [], ["Verified current daily portfolio valuation is missing."]
        if index == 0:
            return None, valuations[index], [], ["A previous daily portfolio valuation is required."]
        previous, current = valuations[index - 1], valuations[index]
        previous_at = _as_datetime(previous["effective_at"])
        current_at = _as_datetime(current["effective_at"])
        fills = sorted(
            (
                item for item in self.execution_ledger.verify()
                if item.get("portfolio_version") == portfolio_version
                and previous_at < _as_datetime(item["filled_at"]) <= current_at
            ),
            key=lambda item: (item["filled_at"], item["fill_id"]),
        )
        reasons = []
        if current_at <= previous_at:
            reasons.append("Valuation effective times must strictly increase.")
        identity_fields = ("strategy_version", "model_versions", "git_revision")
        if any(previous.get(field) != current.get(field) for field in identity_fields):
            reasons.append("Consecutive valuations must share strategy, model and Git identity.")
        if any(
            any(item.get(field) != current.get(field) for field in identity_fields)
            for item in fills
        ):
            reasons.append("Interval fills must share valuation strategy, model and Git identity.")
        previous_ids = set(previous.get("supporting_fill_ids") or [])
        current_ids = set(current.get("supporting_fill_ids") or [])
        interval_ids = {item["fill_id"] for item in fills}
        if not previous_ids.issubset(current_ids) or interval_ids != current_ids - previous_ids:
            reasons.append("Interval fills must exactly reconcile with consecutive valuation support.")
        return previous, current, fills, reasons

    def calculate(
        self,
        *,
        portfolio_version: str,
        current_valuation_id: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        valuation_id = str(current_valuation_id or "").strip()
        previous, current, fills, reasons = self._support(version, valuation_id)
        if reasons or previous is None or current is None:
            return self.not_calculable(version, valuation_id, reasons)
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        latest = max(
            [_as_datetime(current["calculated_at"])]
            + [_as_datetime(item["filled_at"]) for item in fills]
        )
        if calculated < latest:
            return self.not_calculable(version, valuation_id, ["calculated_at cannot predate supporting evidence."])
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(version, valuation_id, ["calculated_at cannot be in the future."])
        try:
            economics = _economics(previous, current, fills)
        except (TypeError, ValueError) as error:
            return self.not_calculable(version, valuation_id, [str(error)])
        result = {
            "schema_version": PERIODIC_TURNOVER_SCHEMA_VERSION,
            "calculation_version": PERIODIC_TURNOVER_CALCULATION_VERSION,
            "turnover_id": _result_id(version, valuation_id),
            "status": "CALCULATED",
            "scope": "SIMULATED_GROSS_TWO_WAY_PERIODIC_TURNOVER",
            "simulation_only": True,
            "currency": "USD",
            "calculated_at": calculated.isoformat(),
            "portfolio_version": version,
            "previous_valuation_id": previous["valuation_id"],
            "previous_valuation_record_hash": previous["record_hash"],
            "current_valuation_id": current["valuation_id"],
            "current_valuation_record_hash": current["record_hash"],
            "interval_start_exclusive": previous["effective_at"],
            "interval_end_inclusive": current["effective_at"],
            "supporting_fill_ids": [item["fill_id"] for item in fills],
            "supporting_fill_hashes": [item["record_hash"] for item in fills],
            "initial_deployment_excluded": True,
            "gross_two_way_convention": True,
            "complete_round_trip_claim": False,
            "broker_cash_reconciled": False,
            "tax_and_withholding_modelled": False,
            "turnover_calculated": True,
            "annualized": False,
            "recommendation_provided": False,
            "order_submission_enabled": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "strategy_version": current["strategy_version"],
            "model_versions": current["model_versions"],
            "git_revision": current["git_revision"],
            **economics,
            "formula": dict(FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def _pinned_support(self, record: Mapping[str, Any]):
        valuations = self.valuation_ledger.verify()
        previous, previous_reasons = resolve_pinned_records(
            valuations,
            [record.get("previous_valuation_id")],
            [record.get("previous_valuation_record_hash")],
            id_field="valuation_id",
            label="previous-valuation",
        )
        current, current_reasons = resolve_pinned_records(
            valuations,
            [record.get("current_valuation_id")],
            [record.get("current_valuation_record_hash")],
            id_field="valuation_id",
            label="current-valuation",
        )
        fills, fill_reasons = resolve_pinned_records(
            self.execution_ledger.verify(),
            record.get("supporting_fill_ids"),
            record.get("supporting_fill_hashes"),
            id_field="fill_id",
            label="fill",
        )
        return previous, current, list(fills), [*previous_reasons, *current_reasons, *fill_reasons]

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(f"Periodic-turnover chain is broken at record {index}.")
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Periodic-turnover record {index} has been modified.")
            previous_values, current_values, fills, reasons = self._pinned_support(record)
            if reasons or len(previous_values) != 1 or len(current_values) != 1:
                raise LedgerIntegrityError(f"Periodic-turnover record {index} lost supporting evidence.")
            previous, current = previous_values[0], current_values[0]
            support_previous, support_current, support_fills, support_reasons = self._support(
                record.get("portfolio_version", ""), record.get("current_valuation_id", "")
            )
            if support_reasons or support_previous != previous or support_current != current:
                raise LedgerIntegrityError(f"Periodic-turnover record {index} violates interval support.")
            try:
                economics = _economics(previous, current, fills)
                calculated = _as_datetime(record.get("calculated_at"))
                latest = max(
                    [_as_datetime(current["calculated_at"])]
                    + [_as_datetime(item["filled_at"]) for item in fills]
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Periodic-turnover record {index} has invalid values.") from error
            fills = sorted(fills, key=lambda item: (item["filled_at"], item["fill_id"]))
            expected_id = _result_id(record.get("portfolio_version", ""), record.get("current_valuation_id", ""))
            boundary = (
                record.get("schema_version") == PERIODIC_TURNOVER_SCHEMA_VERSION
                and record.get("calculation_version") == PERIODIC_TURNOVER_CALCULATION_VERSION
                and record.get("turnover_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "CALCULATED"
                and record.get("scope") == "SIMULATED_GROSS_TWO_WAY_PERIODIC_TURNOVER"
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and record.get("previous_valuation_id") == previous["valuation_id"]
                and record.get("previous_valuation_record_hash") == previous["record_hash"]
                and record.get("current_valuation_id") == current["valuation_id"]
                and record.get("current_valuation_record_hash") == current["record_hash"]
                and record.get("interval_start_exclusive") == previous["effective_at"]
                and record.get("interval_end_inclusive") == current["effective_at"]
                and record.get("supporting_fill_ids") == [item["fill_id"] for item in fills]
                and record.get("supporting_fill_hashes") == [item["record_hash"] for item in fills]
                and support_fills == fills
                and record.get("initial_deployment_excluded") is True
                and record.get("gross_two_way_convention") is True
                and record.get("complete_round_trip_claim") is False
                and record.get("broker_cash_reconciled") is False
                and record.get("tax_and_withholding_modelled") is False
                and record.get("turnover_calculated") is True
                and record.get("annualized") is False
                and record.get("recommendation_provided") is False
                and record.get("order_submission_enabled") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("strategy_version") == current["strategy_version"]
                and record.get("model_versions") == current["model_versions"]
                and record.get("git_revision") == current["git_revision"]
                and record.get("formula") == FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and calculated >= latest
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(f"Periodic-turnover record {index} violates its boundary.")
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
            existing = next((item for item in records if item["turnover_id"] == result["turnover_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash", "calculated_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(f"Periodic turnover {result['turnover_id']} already exists.")
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
