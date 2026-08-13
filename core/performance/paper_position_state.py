from __future__ import annotations

"""Immutable, point-in-time position state derived only from local paper fills."""

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
from core.performance.portfolio_valuation import (
    _canonical_json,
    _decimal_string,
    _fraction_material,
    _record_hash,
    _write_all,
)


POSITION_STATE_SCHEMA_VERSION = "1.0"
POSITION_STATE_CALCULATION_VERSION = "local-paper-fifo-position-state-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _nonnegative_fraction(value: Any, name: str, *, positive: bool = False) -> Fraction:
    try:
        result = Fraction(Decimal(str(value)))
    except (InvalidOperation, OverflowError, TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{name} must be a finite exact decimal") from error
    if result < 0 or (positive and result == 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be a {qualifier} finite exact decimal")
    return result


def _snapshot_id(portfolio_version: str, as_of: datetime, source_fill_tail_hash: str) -> str:
    material = [
        portfolio_version,
        as_of.isoformat(),
        source_fill_tail_hash,
        POSITION_STATE_CALCULATION_VERSION,
    ]
    return "PSTATE-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _common_identity(fills: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not fills:
        raise ValueError("At least one eligible verified local paper fill is required")
    identity = {
        "strategy_version": fills[0].get("strategy_version"),
        "model_versions": fills[0].get("model_versions"),
        "git_revision": fills[0].get("git_revision"),
    }
    if any(
        any(fill.get(key) != value for key, value in identity.items())
        for fill in fills
    ):
        raise ValueError("Portfolio fills must share strategy, model and Git identity")
    return identity


def _state(fills: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Consume exact quantities from oldest lots without mutating source records."""
    ordered = sorted(fills, key=lambda item: (_as_datetime(item["filled_at"]), item["fill_id"]))
    by_time: dict[tuple[str, str], set[str]] = {}
    for fill in ordered:
        by_time.setdefault(
            (str(fill["ticker"]), _as_datetime(fill["filled_at"]).isoformat()), set()
        ).add(
            str(fill["side"])
        )
    if any(sides == {"BUY", "SELL"} for sides in by_time.values()):
        raise ValueError(
            "Opposing fills for one ticker at the same filled_at are ambiguous"
        )

    lots: dict[str, list[dict[str, Any]]] = {}
    buy_gross = sell_gross = fees = Fraction(0)
    for fill in ordered:
        ticker = str(fill["ticker"])
        side = str(fill["side"])
        quantity = _nonnegative_fraction(
            fill.get("filled_quantity"), "filled_quantity", positive=True
        )
        gross = _nonnegative_fraction(fill.get("gross_value"), "gross_value", positive=True)
        fee = _nonnegative_fraction(fill.get("fees"), "fees")
        fees += fee
        if side == "BUY":
            buy_gross += gross
            lots.setdefault(ticker, []).append(
                {
                    "buy_fill_id": fill["fill_id"],
                    "buy_fill_record_hash": fill["record_hash"],
                    "acquired_at": fill["filled_at"],
                    "remaining": quantity,
                }
            )
            continue
        if side != "SELL":
            raise ValueError(f"Unsupported fill side {side!r}")
        sell_gross += gross
        queue = lots.setdefault(ticker, [])
        if sum((lot["remaining"] for lot in queue), Fraction(0)) < quantity:
            raise ValueError(f"SELL for {ticker} exceeds the cumulative open paper position")
        remaining = quantity
        for lot in queue:
            consumed = min(lot["remaining"], remaining)
            lot["remaining"] -= consumed
            remaining -= consumed
            if remaining == 0:
                break
        lots[ticker] = [lot for lot in queue if lot["remaining"] > 0]

    positions = []
    for ticker in sorted(lots):
        open_lots = lots[ticker]
        if not open_lots:
            continue
        quantity = sum((lot["remaining"] for lot in open_lots), Fraction(0))
        positions.append(
            {
                "ticker": ticker,
                "open_quantity": _decimal_string(quantity),
                "exact_open_quantity": _fraction_material(quantity),
                "supporting_open_lots": [
                    {
                        "buy_fill_id": lot["buy_fill_id"],
                        "buy_fill_record_hash": lot["buy_fill_record_hash"],
                        "acquired_at": lot["acquired_at"],
                        "remaining_quantity": _decimal_string(lot["remaining"]),
                        "exact_remaining_quantity": _fraction_material(lot["remaining"]),
                    }
                    for lot in open_lots
                ],
            }
        )
    net_cash = sell_gross - buy_gross - fees
    exact = {
        "total_buy_gross": buy_gross,
        "total_sell_gross": sell_gross,
        "total_recorded_fees": fees,
        "net_trade_cash_flow": net_cash,
    }
    return {
        "open_positions": positions,
        "open_position_count": len(positions),
        **{key: _decimal_string(value) for key, value in exact.items()},
        "exact_fractions": {key: _fraction_material(value) for key, value in exact.items()},
    }


class PaperPositionStateLedger:
    """Append-only paper holdings snapshots; has no broker or order authority."""

    def __init__(self, path: str | Path, execution_ledger: LocalPaperExecutionLedger) -> None:
        self.path = Path(path)
        self.execution_ledger = execution_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Paper-position-state ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank paper-position-state line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at paper-position-state line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Paper-position-state line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def _eligible(
        prefix: Sequence[Mapping[str, Any]], portfolio_version: str, as_of: datetime
    ) -> list[Mapping[str, Any]]:
        return [
            fill
            for fill in prefix
            if fill.get("portfolio_version") == portfolio_version
            and _as_datetime(fill["filled_at"]) <= as_of
        ]

    def calculate(
        self,
        *,
        portfolio_version: str,
        as_of: str | datetime,
        calculated_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        if not version:
            raise ValueError("portfolio_version is required")
        effective = _as_datetime(as_of)
        calculated = _as_datetime(calculated_at or datetime.now(timezone.utc))
        if effective > calculated:
            raise ValueError("as_of cannot be later than calculated_at")
        if calculated > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("calculated_at cannot be in the future")
        fills = self.execution_ledger.verify()
        eligible = self._eligible(fills, version, effective)
        identity = _common_identity(eligible)
        state = _state(eligible)
        source_tail_hash = fills[-1]["record_hash"] if fills else GENESIS_HASH
        result = {
            "schema_version": POSITION_STATE_SCHEMA_VERSION,
            "calculation_version": POSITION_STATE_CALCULATION_VERSION,
            "snapshot_id": _snapshot_id(version, effective, source_tail_hash),
            "status": "CALCULATED",
            "scope": "LOCAL_SIMULATED_PAPER_POSITION_STATE",
            "simulation_only": True,
            "currency": "USD",
            "portfolio_version": version,
            "as_of": effective.isoformat(),
            "calculated_at": calculated.isoformat(),
            "source_fill_count": len(fills),
            "source_fill_tail_hash": source_tail_hash,
            "supporting_fill_ids": [fill["fill_id"] for fill in eligible],
            "supporting_fill_record_hashes": [fill["record_hash"] for fill in eligible],
            "source_knowledge_policy": "VERIFIED_EXECUTION_CHAIN_PREFIX_AT_CALCULATION",
            "fill_inclusion_policy": "PINNED_PREFIX_AND_FILLED_AT_OR_BEFORE_AS_OF",
            "lot_pooling_policy": "PORTFOLIO_VERSION_AND_TICKER_ONLY",
            "fifo_policy": "FILLED_AT_THEN_FILL_ID_EXACT_PARTIAL_LOT_CONSUMPTION",
            "later_backdated_fills_change_new_reconstructions": True,
            "broker_cash_reconciled": False,
            "dividends_modelled": False,
            "corporate_actions_modelled": False,
            "tax_and_withholding_modelled": False,
            "realized_profit_calculated": False,
            "performance_metric_calculated": False,
            "recommendation_provided": False,
            "order_submission_enabled": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
            **identity,
            **state,
        }
        return self._append(result, allow_existing=allow_existing)

    def _source_prefix(self, record: Mapping[str, Any]) -> list[dict[str, Any]]:
        fills = self.execution_ledger.verify()
        try:
            count = int(record.get("source_fill_count"))
        except (TypeError, ValueError) as error:
            raise LedgerIntegrityError("Invalid paper-position source-fill count") from error
        if count <= 0 or count > len(fills):
            raise LedgerIntegrityError("Paper-position source prefix is unavailable")
        prefix = fills[:count]
        if prefix[-1].get("record_hash") != record.get("source_fill_tail_hash"):
            raise LedgerIntegrityError("Paper-position source prefix has changed")
        return prefix

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(f"Paper-position chain is broken at record {index}.")
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Paper-position record {index} has been modified.")
            try:
                prefix = self._source_prefix(record)
                effective = _as_datetime(record.get("as_of"))
                calculated = _as_datetime(record.get("calculated_at"))
                version = str(record.get("portfolio_version") or "")
                eligible = self._eligible(prefix, version, effective)
                identity = _common_identity(eligible)
                state = _state(eligible)
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Paper-position record {index} has invalid support or values."
                ) from error
            expected_id = _snapshot_id(
                version, effective, str(record.get("source_fill_tail_hash") or "")
            )
            fixed = {
                "schema_version": POSITION_STATE_SCHEMA_VERSION,
                "calculation_version": POSITION_STATE_CALCULATION_VERSION,
                "status": "CALCULATED",
                "scope": "LOCAL_SIMULATED_PAPER_POSITION_STATE",
                "simulation_only": True,
                "currency": "USD",
                "source_knowledge_policy": "VERIFIED_EXECUTION_CHAIN_PREFIX_AT_CALCULATION",
                "fill_inclusion_policy": "PINNED_PREFIX_AND_FILLED_AT_OR_BEFORE_AS_OF",
                "lot_pooling_policy": "PORTFOLIO_VERSION_AND_TICKER_ONLY",
                "fifo_policy": "FILLED_AT_THEN_FILL_ID_EXACT_PARTIAL_LOT_CONSUMPTION",
                "later_backdated_fills_change_new_reconstructions": True,
                "broker_cash_reconciled": False,
                "dividends_modelled": False,
                "corporate_actions_modelled": False,
                "tax_and_withholding_modelled": False,
                "realized_profit_calculated": False,
                "performance_metric_calculated": False,
                "recommendation_provided": False,
                "order_submission_enabled": False,
                "learning_eligible": False,
                "track_record_claim": False,
                "live_trading_enabled": False,
            }
            boundary = (
                bool(version)
                and record.get("snapshot_id") == expected_id
                and expected_id not in seen
                and record.get("as_of") == effective.isoformat()
                and record.get("calculated_at") == calculated.isoformat()
                and effective <= calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("source_fill_count") == len(prefix)
                and record.get("supporting_fill_ids") == [fill["fill_id"] for fill in eligible]
                and record.get("supporting_fill_record_hashes")
                == [fill["record_hash"] for fill in eligible]
                and all(record.get(key) == value for key, value in fixed.items())
                and all(record.get(key) == value for key, value in identity.items())
                and all(record.get(key) == value for key, value in state.items())
            )
            if not boundary:
                raise LedgerIntegrityError(f"Paper-position record {index} violates its boundary.")
            seen.add(expected_id)
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
                (item for item in records if item["snapshot_id"] == result["snapshot_id"]), None
            )
            if existing:
                if allow_existing:
                    return existing
                raise LedgerIntegrityError(
                    f"Paper-position snapshot {result['snapshot_id']} already exists."
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
