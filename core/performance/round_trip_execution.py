from __future__ import annotations

"""Exact complete simulated round-trip evidence from verified local fills."""

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

from core.broker import LocalPaperExecutionLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import (
    _canonical_json,
    _decimal_string,
    _fraction_material,
    _record_hash,
    _write_all,
)


ROUND_TRIP_SCHEMA_VERSION = "1.0"
ROUND_TRIP_CALCULATION_VERSION = "verified-local-entry-exit-round-trip-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
FORMULA = {
    "recorded_entry_cost": "entry_fill_gross_value + entry_recorded_fee",
    "recorded_exit_proceeds": "exit_fill_gross_value - exit_recorded_fee",
    "net_profit_or_loss": "recorded_exit_proceeds - recorded_entry_cost",
    "net_round_trip_return": "net_profit_or_loss / recorded_entry_cost",
    "recorded_round_trip_fees": "entry_recorded_fee + exit_recorded_fee",
    "recorded_signed_slippage": "entry_signed_slippage + exit_signed_slippage",
    "cost_double_count_policy": "FILL_PRICES_AND_FEES_ALREADY_EMBEDDED_NO_REDEDUCTION",
    "unobserved_cost_policy": "NO_SPREAD_MARKET_IMPACT_LATENCY_TAX_OR_BORROW_COST_INVENTED",
    "arithmetic_policy": "EXACT_RATIONAL_WITH_34_DIGIT_DECIMAL_PRESENTATION",
}


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _number(value: Any, name: str, *, allow_zero: bool = False) -> Fraction:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite() or resolved < 0 or (resolved == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be a {qualifier} finite decimal")
    return Fraction(resolved)


def _signed_number(value: Any, name: str) -> Fraction:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite():
        raise ValueError(f"{name} must be a finite decimal")
    return Fraction(resolved)


def _result_id(entry_fill_id: str, exit_fill_id: str) -> str:
    material = [entry_fill_id, exit_fill_id, ROUND_TRIP_CALCULATION_VERSION]
    return "RTRIP-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _economics(entry: Mapping[str, Any], exit_fill: Mapping[str, Any]) -> dict[str, Any]:
    entry_quantity = _number(entry.get("filled_quantity"), "entry quantity")
    exit_quantity = _number(exit_fill.get("filled_quantity"), "exit quantity")
    if entry_quantity != exit_quantity:
        raise ValueError("Entry and exit filled quantities must match exactly")
    entry_gross = _number(entry.get("gross_value"), "entry gross value")
    exit_gross = _number(exit_fill.get("gross_value"), "exit gross value")
    entry_fee = _number(entry.get("fees"), "entry fee", allow_zero=True)
    exit_fee = _number(exit_fill.get("fees"), "exit fee", allow_zero=True)
    if exit_fee > exit_gross:
        raise ValueError("Exit fee cannot exceed exit gross proceeds")
    entry_slippage = _signed_number(entry.get("slippage_amount"), "entry slippage")
    exit_slippage = _signed_number(exit_fill.get("slippage_amount"), "exit slippage")
    entry_cost = entry_gross + entry_fee
    exit_proceeds = exit_gross - exit_fee
    profit = exit_proceeds - entry_cost
    net_return = profit / entry_cost
    exact = {
        "filled_quantity": entry_quantity,
        "entry_fill_gross_value": entry_gross,
        "entry_recorded_fee": entry_fee,
        "recorded_entry_cost": entry_cost,
        "exit_fill_gross_value": exit_gross,
        "exit_recorded_fee": exit_fee,
        "recorded_exit_proceeds": exit_proceeds,
        "recorded_round_trip_fees": entry_fee + exit_fee,
        "entry_signed_slippage_amount": entry_slippage,
        "exit_signed_slippage_amount": exit_slippage,
        "recorded_signed_round_trip_slippage": entry_slippage + exit_slippage,
        "net_profit_or_loss": profit,
        "net_round_trip_return": net_return,
    }
    return {
        **{key: _decimal_string(value) for key, value in exact.items()},
        "exact_fractions": {key: _fraction_material(value) for key, value in exact.items()},
    }


def _pair_reasons(entry: Mapping[str, Any], exit_fill: Mapping[str, Any]) -> list[str]:
    reasons = []
    if entry.get("side") != "BUY" or exit_fill.get("side") != "SELL":
        reasons.append("Round-trip evidence requires one BUY entry and one SELL exit.")
    identity_fields = (
        "decision_id",
        "portfolio_version",
        "ticker",
        "strategy_version",
        "model_versions",
        "git_revision",
    )
    if any(entry.get(field) != exit_fill.get(field) for field in identity_fields):
        reasons.append(
            "Entry and exit must share decision, portfolio, ticker, strategy, model and Git identity."
        )
    try:
        if _as_datetime(exit_fill["filled_at"]) <= _as_datetime(entry["filled_at"]):
            reasons.append("Exit fill must occur strictly after the entry fill.")
        _economics(entry, exit_fill)
    except (KeyError, TypeError, ValueError) as error:
        reasons.append(str(error))
    return sorted(set(reasons))


def _pairing_group(fill: Mapping[str, Any]) -> str:
    quantity = _number(fill.get("filled_quantity"), "filled quantity")
    return _canonical_json(
        [
            fill.get("decision_id"),
            fill.get("portfolio_version"),
            fill.get("ticker"),
            fill.get("strategy_version"),
            fill.get("model_versions"),
            fill.get("git_revision"),
            _fraction_material(quantity),
        ]
    )


def _canonical_fifo_pairs(fills: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
    """Oldest unmatched BUY receives the earliest strictly later matching SELL."""
    pending: dict[str, list[Mapping[str, Any]]] = {}
    pairs = set()
    ordered = sorted(
        fills,
        key=lambda item: (_as_datetime(item["filled_at"]), item["fill_id"]),
    )
    for fill in ordered:
        group = _pairing_group(fill)
        if fill.get("side") == "BUY":
            pending.setdefault(group, []).append(fill)
            continue
        if fill.get("side") != "SELL":
            continue
        queue = pending.get(group, [])
        candidate_index = next(
            (
                index
                for index, entry in enumerate(queue)
                if _as_datetime(entry["filled_at"]) < _as_datetime(fill["filled_at"])
            ),
            None,
        )
        if candidate_index is not None:
            entry = queue.pop(candidate_index)
            pairs.add((entry["fill_id"], fill["fill_id"]))
    return pairs


class SimulatedRoundTripExecutionLedger:
    """Append-only entry/exit results; never creates or submits an order."""

    def __init__(self, path: str | Path, execution_ledger: LocalPaperExecutionLedger) -> None:
        self.path = Path(path)
        self.execution_ledger = execution_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Round-trip-execution ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank round-trip line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at round-trip line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(f"Round-trip line {line_number} is not an object.")
                records.append(record)
        return records

    @staticmethod
    def not_calculable(entry_fill_id: str, exit_fill_id: str, reasons: Sequence[str]):
        return {
            "status": "NOT_CALCULABLE",
            "entry_fill_id": str(entry_fill_id),
            "exit_fill_id": str(exit_fill_id),
            "reasons": list(reasons),
            "record_appended": False,
            "simulation_only": True,
            "complete_round_trip_calculated": False,
            "recommendation_provided": False,
            "order_submission_enabled": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
        }

    def _support(self, entry_fill_id: str, exit_fill_id: str):
        verified_fills = self.execution_ledger.verify()
        fills = {item["fill_id"]: item for item in verified_fills}
        entry = fills.get(entry_fill_id)
        exit_fill = fills.get(exit_fill_id)
        reasons = []
        if entry is None:
            reasons.append("Verified simulated entry fill is missing.")
        if exit_fill is None:
            reasons.append("Verified simulated exit fill is missing.")
        if entry is not None and exit_fill is not None:
            reasons.extend(_pair_reasons(entry, exit_fill))
            if (entry_fill_id, exit_fill_id) not in _canonical_fifo_pairs(verified_fills):
                reasons.append(
                    "Entry and exit are not the deterministic FIFO pair for their matching group."
                )
        return entry, exit_fill, reasons

    def calculate(
        self,
        *,
        entry_fill_id: str,
        exit_fill_id: str,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        entry_id = str(entry_fill_id or "").strip()
        exit_id = str(exit_fill_id or "").strip()
        entry, exit_fill, reasons = self._support(entry_id, exit_id)
        if reasons or entry is None or exit_fill is None:
            return self.not_calculable(entry_id, exit_id, reasons)
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        if calculated < _as_datetime(exit_fill["filled_at"]):
            return self.not_calculable(
                entry_id, exit_id, ["calculated_at cannot predate the exit fill."]
            )
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            return self.not_calculable(entry_id, exit_id, ["calculated_at cannot be in the future."])
        economics = _economics(entry, exit_fill)
        result = {
            "schema_version": ROUND_TRIP_SCHEMA_VERSION,
            "calculation_version": ROUND_TRIP_CALCULATION_VERSION,
            "result_id": _result_id(entry_id, exit_id),
            "status": "CALCULATED",
            "scope": "COMPLETE_LOCAL_SIMULATED_ENTRY_EXIT_ROUND_TRIP",
            "simulation_only": True,
            "currency": "USD",
            "calculated_at": calculated.isoformat(),
            "entry_fill_id": entry_id,
            "entry_fill_record_hash": entry["record_hash"],
            "entry_filled_at": entry["filled_at"],
            "exit_fill_id": exit_id,
            "exit_fill_record_hash": exit_fill["record_hash"],
            "exit_filled_at": exit_fill["filled_at"],
            "decision_id": entry["decision_id"],
            "portfolio_version": entry["portfolio_version"],
            "ticker": entry["ticker"],
            "complete_round_trip_calculated": True,
            "pairing_policy": "FIFO_BY_FILLED_AT_THEN_FILL_ID",
            "entry_and_exit_fees_included": True,
            "entry_and_exit_fill_slippage_embedded": True,
            "bid_ask_spread_separately_included": False,
            "market_impact_included": False,
            "latency_cost_included": False,
            "tax_and_withholding_modelled": False,
            "borrow_cost_modelled": False,
            "recommendation_provided": False,
            "order_submission_enabled": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
            "strategy_version": entry["strategy_version"],
            "model_versions": entry["model_versions"],
            "git_revision": entry["git_revision"],
            **economics,
            "formula": dict(FORMULA),
        }
        return self._append(result, allow_existing=allow_existing)

    def _pinned_support(self, record: Mapping[str, Any]):
        fills = self.execution_ledger.verify()
        entry, entry_reasons = resolve_pinned_records(
            fills,
            [record.get("entry_fill_id")],
            [record.get("entry_fill_record_hash")],
            id_field="fill_id",
            label="entry-fill",
        )
        exit_fills, exit_reasons = resolve_pinned_records(
            fills,
            [record.get("exit_fill_id")],
            [record.get("exit_fill_record_hash")],
            id_field="fill_id",
            label="exit-fill",
        )
        return entry, exit_fills, [*entry_reasons, *exit_reasons]

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_results = set()
        used_fills = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(f"Round-trip chain is broken at record {index}.")
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Round-trip record {index} has been modified.")
            entries, exits, reasons = self._pinned_support(record)
            if reasons or len(entries) != 1 or len(exits) != 1:
                raise LedgerIntegrityError(f"Round-trip record {index} lost supporting evidence.")
            entry, exit_fill = entries[0], exits[0]
            support_entry, support_exit, pair_reasons = self._support(
                entry["fill_id"], exit_fill["fill_id"]
            )
            if pair_reasons or support_entry != entry or support_exit != exit_fill:
                raise LedgerIntegrityError(f"Round-trip record {index} has invalid paired fills.")
            try:
                economics = _economics(entry, exit_fill)
                calculated = _as_datetime(record.get("calculated_at"))
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Round-trip record {index} has invalid values.") from error
            expected_id = _result_id(entry["fill_id"], exit_fill["fill_id"])
            fill_ids = {entry["fill_id"], exit_fill["fill_id"]}
            boundary = (
                record.get("schema_version") == ROUND_TRIP_SCHEMA_VERSION
                and record.get("calculation_version") == ROUND_TRIP_CALCULATION_VERSION
                and record.get("result_id") == expected_id
                and expected_id not in seen_results
                and not used_fills.intersection(fill_ids)
                and record.get("status") == "CALCULATED"
                and record.get("scope") == "COMPLETE_LOCAL_SIMULATED_ENTRY_EXIT_ROUND_TRIP"
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and record.get("entry_fill_id") == entry["fill_id"]
                and record.get("entry_fill_record_hash") == entry["record_hash"]
                and record.get("entry_filled_at") == entry["filled_at"]
                and record.get("exit_fill_id") == exit_fill["fill_id"]
                and record.get("exit_fill_record_hash") == exit_fill["record_hash"]
                and record.get("exit_filled_at") == exit_fill["filled_at"]
                and all(
                    record.get(field) == entry.get(field)
                    for field in (
                        "decision_id", "portfolio_version", "ticker", "strategy_version",
                        "model_versions", "git_revision",
                    )
                )
                and record.get("complete_round_trip_calculated") is True
                and record.get("pairing_policy") == "FIFO_BY_FILLED_AT_THEN_FILL_ID"
                and record.get("entry_and_exit_fees_included") is True
                and record.get("entry_and_exit_fill_slippage_embedded") is True
                and record.get("bid_ask_spread_separately_included") is False
                and record.get("market_impact_included") is False
                and record.get("latency_cost_included") is False
                and record.get("tax_and_withholding_modelled") is False
                and record.get("borrow_cost_modelled") is False
                and record.get("recommendation_provided") is False
                and record.get("order_submission_enabled") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("live_trading_enabled") is False
                and record.get("formula") == FORMULA
                and all(record.get(key) == value for key, value in economics.items())
                and calculated >= _as_datetime(exit_fill["filled_at"])
                and calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
            )
            if not boundary:
                raise LedgerIntegrityError(f"Round-trip record {index} violates its boundary.")
            seen_results.add(expected_id)
            used_fills.update(fill_ids)
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
                ignored = {"previous_hash", "record_hash", "calculated_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(f"Round trip {result['result_id']} already exists.")
            proposed_fills = {result["entry_fill_id"], result["exit_fill_id"]}
            if any(
                proposed_fills.intersection({item["entry_fill_id"], item["exit_fill_id"]})
                for item in records
            ):
                raise LedgerIntegrityError("A simulated fill is already assigned to another round trip.")
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
