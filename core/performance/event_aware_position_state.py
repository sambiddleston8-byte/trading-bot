from __future__ import annotations

"""Immutable FIFO paper positions with sourced split and dividend processing."""

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
from core.performance.corporate_action import CorporateActionLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import (
    _canonical_json,
    _decimal_string,
    _fraction_material,
    _record_hash,
    _write_all,
)


EVENT_POSITION_SCHEMA_VERSION = "1.0"
EVENT_POSITION_CALCULATION_VERSION = "event-aware-paper-fifo-position-state-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _fraction(value: Any, name: str, *, positive: bool = False) -> Fraction:
    try:
        result = Fraction(Decimal(str(value)))
    except (InvalidOperation, OverflowError, TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{name} must be a finite exact decimal") from error
    if result < 0 or (positive and result == 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be a {qualifier} finite exact decimal")
    return result


def _event_time(event: Mapping[str, Any]) -> datetime:
    field = "ex_at" if event.get("event_type") == "CASH_DIVIDEND" else "effective_at"
    return _as_datetime(event.get(field))


def _event_material(event: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key != "source_event_id"}


def _snapshot_id(
    portfolio_version: str,
    as_of: datetime,
    fill_hashes: Sequence[str],
    evidence_hashes: Sequence[str],
) -> str:
    material = [
        portfolio_version,
        as_of.isoformat(),
        list(fill_hashes),
        list(evidence_hashes),
        EVENT_POSITION_CALCULATION_VERSION,
    ]
    return "EPSTATE-" + hashlib.sha256(
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


def _events_by_ticker(
    fills: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    as_of: datetime,
) -> dict[str, list[dict[str, Any]]]:
    buys = {fill["fill_id"]: fill for fill in fills if fill.get("side") == "BUY"}
    evidence_by_fill = {item.get("fill_id"): item for item in evidence}
    if set(evidence_by_fill) != set(buys) or len(evidence_by_fill) != len(evidence):
        raise ValueError("Every historical BUY fill requires exactly one evidence record")
    source_identity: dict[str, tuple[Any, Any]] = {}
    ticker_evidence: dict[str, list[Mapping[str, Any]]] = {}
    by_ticker: dict[str, dict[str, dict[str, Any]]] = {}
    source_ids: dict[tuple[str, str], str] = {}
    for fill_id, fill in buys.items():
        item = evidence_by_fill[fill_id]
        ticker = str(fill["ticker"])
        if item.get("execution_record_hash") != fill.get("record_hash"):
            raise ValueError("Corporate-action evidence does not match its BUY fill")
        if item.get("ticker") != ticker or item.get("portfolio_version") != fill.get(
            "portfolio_version"
        ):
            raise ValueError("Corporate-action evidence changed portfolio identity")
        if item.get("completeness_status") != "COMPLETE":
            raise ValueError("Corporate-action evidence must be COMPLETE")
        if _as_datetime(item["covers_from_at"]) > _as_datetime(fill["filled_at"]):
            raise ValueError("Corporate-action evidence does not cover its BUY fill")
        if _as_datetime(item["through_at"]) < as_of:
            raise ValueError("Corporate-action evidence does not reach the exact as_of time")
        current_source = (item.get("data_source"), item.get("source_version"))
        prior_source = source_identity.setdefault(ticker, current_source)
        if current_source != prior_source:
            raise ValueError("One ticker's corporate-action evidence must share source and version")
        for prior in ticker_evidence.setdefault(ticker, []):
            overlap_start = max(
                _as_datetime(item["covers_from_at"]),
                _as_datetime(prior["covers_from_at"]),
            )
            overlap_end = min(
                as_of,
                _as_datetime(item["through_at"]),
                _as_datetime(prior["through_at"]),
            )
            current_terms = sorted(
                (
                    _event_material(event)
                    for event in item.get("events") or []
                    if overlap_start <= _event_time(event) <= overlap_end
                ),
                key=_canonical_json,
            )
            prior_terms = sorted(
                (
                    _event_material(event)
                    for event in prior.get("events") or []
                    if overlap_start <= _event_time(event) <= overlap_end
                ),
                key=_canonical_json,
            )
            if overlap_start <= overlap_end and current_terms != prior_terms:
                raise ValueError(
                    "Corporate-action evidence conflicts across overlapping BUY coverage"
                )
        ticker_evidence[ticker].append(item)
        ticker_events = by_ticker.setdefault(ticker, {})
        for event in item.get("events") or []:
            if _event_time(event) > as_of:
                continue
            event_type = event.get("event_type")
            if event_type == "OTHER":
                raise ValueError("Unsupported corporate action blocks event-aware position state")
            if event_type not in {"STOCK_SPLIT", "CASH_DIVIDEND"}:
                raise ValueError("Unknown corporate action blocks event-aware position state")
            material = _event_material(event)
            material_key = _canonical_json(material)
            source_key = (ticker, str(event.get("source_event_id") or ""))
            prior_material = source_ids.setdefault(source_key, material_key)
            if prior_material != material_key:
                raise ValueError("Corporate-action source event terms conflict across BUY evidence")
            ticker_events.setdefault(material_key, dict(event))
    return {
        ticker: sorted(events.values(), key=lambda item: (_event_time(item), _canonical_json(item)))
        for ticker, events in by_ticker.items()
    }


def _state(
    fills: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    as_of: datetime,
) -> dict[str, Any]:
    events = _events_by_ticker(fills, evidence, as_of)
    fill_sides_by_time: dict[tuple[str, datetime], set[str]] = {}
    for fill in fills:
        fill_sides_by_time.setdefault(
            (str(fill["ticker"]), _as_datetime(fill["filled_at"])), set()
        ).add(str(fill["side"]))
    if any(sides == {"BUY", "SELL"} for sides in fill_sides_by_time.values()):
        raise ValueError("Opposing fills for one ticker at the same time are ambiguous")
    trade_times = {
        (str(fill["ticker"]), _as_datetime(fill["filled_at"])) for fill in fills
    }
    action_times = {
        (ticker, _event_time(event))
        for ticker, ticker_events in events.items()
        for event in ticker_events
    }
    if trade_times.intersection(action_times):
        raise ValueError("A trade and corporate action at the same instant are ambiguous")
    for ticker, ticker_events in events.items():
        by_time: dict[datetime, list[Mapping[str, Any]]] = {}
        for event in ticker_events:
            by_time.setdefault(_event_time(event), []).append(event)
        if any(
            any(event["event_type"] == "STOCK_SPLIT" for event in same_time)
            and len(same_time) > 1
            for same_time in by_time.values()
        ):
            raise ValueError(
                f"A split simultaneous with another action for {ticker} is ambiguous"
            )

    timeline = []
    for fill in fills:
        timeline.append(
            (_as_datetime(fill["filled_at"]), str(fill["ticker"]), "TRADE", fill["fill_id"], fill)
        )
    for ticker, ticker_events in events.items():
        for event in ticker_events:
            timeline.append(
                (
                    _event_time(event),
                    ticker,
                    "ACTION",
                    _canonical_json(event),
                    event,
                )
            )
    timeline.sort(key=lambda item: (item[0], item[1], item[2], item[3]))

    lots: dict[str, list[dict[str, Any]]] = {}
    buy_gross = sell_gross = fees = dividends = Fraction(0)
    applied_events = []
    for timestamp, ticker, kind, _, item in timeline:
        queue = lots.setdefault(ticker, [])
        if kind == "TRADE":
            side = str(item["side"])
            quantity = _fraction(item.get("filled_quantity"), "filled_quantity", positive=True)
            gross = _fraction(item.get("gross_value"), "gross_value", positive=True)
            fee = _fraction(item.get("fees"), "fees")
            fees += fee
            if side == "BUY":
                buy_gross += gross
                queue.append(
                    {
                        "buy_fill_id": item["fill_id"],
                        "buy_fill_record_hash": item["record_hash"],
                        "acquired_at": item["filled_at"],
                        "remaining": quantity,
                    }
                )
                continue
            if side != "SELL":
                raise ValueError(f"Unsupported fill side {side!r}")
            sell_gross += gross
            if sum((lot["remaining"] for lot in queue), Fraction(0)) < quantity:
                raise ValueError(f"SELL for {ticker} exceeds the adjusted open paper position")
            remaining = quantity
            for lot in queue:
                consumed = min(lot["remaining"], remaining)
                lot["remaining"] -= consumed
                remaining -= consumed
                if remaining == 0:
                    break
            lots[ticker] = [lot for lot in queue if lot["remaining"] > 0]
            continue

        event_type = item["event_type"]
        if event_type == "STOCK_SPLIT":
            numerator = _fraction(item["numerator"], "split numerator", positive=True)
            denominator = _fraction(item["denominator"], "split denominator", positive=True)
            before = sum((lot["remaining"] for lot in queue), Fraction(0))
            for lot in queue:
                lot["remaining"] = lot["remaining"] * numerator / denominator
            after = sum((lot["remaining"] for lot in queue), Fraction(0))
            applied_events.append(
                {
                    "ticker": ticker,
                    "event_type": "STOCK_SPLIT",
                    "source_event_id": item["source_event_id"],
                    "effective_at": timestamp.isoformat(),
                    "numerator": item["numerator"],
                    "denominator": item["denominator"],
                    "exact_open_quantity_before": _fraction_material(before),
                    "exact_open_quantity_after": _fraction_material(after),
                }
            )
            continue

        if item.get("currency") != "USD":
            raise ValueError("Only USD cash dividends are supported without FX evidence")
        payment = item.get("payment_at")
        if payment is None or _as_datetime(payment) > as_of:
            raise ValueError("An entitled dividend must be paid by as_of")
        entitled = sum((lot["remaining"] for lot in queue), Fraction(0))
        amount = _fraction(item["amount_per_share"], "dividend amount", positive=True)
        cash = entitled * amount
        dividends += cash
        applied_events.append(
            {
                "ticker": ticker,
                "event_type": "CASH_DIVIDEND",
                "source_event_id": item["source_event_id"],
                "ex_at": timestamp.isoformat(),
                "payment_at": _as_datetime(payment).isoformat(),
                "amount_per_share": item["amount_per_share"],
                "currency": "USD",
                "exact_entitled_quantity": _fraction_material(entitled),
                "exact_gross_cash": _fraction_material(cash),
            }
        )

    positions = []
    for ticker in sorted(lots):
        queue = lots[ticker]
        if not queue:
            continue
        quantity = sum((lot["remaining"] for lot in queue), Fraction(0))
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
                    for lot in queue
                ],
            }
        )
    net_trade_cash = sell_gross - buy_gross - fees
    exact = {
        "total_buy_gross": buy_gross,
        "total_sell_gross": sell_gross,
        "total_recorded_fees": fees,
        "net_trade_cash_flow": net_trade_cash,
        "cumulative_paid_gross_usd_dividend_cash": dividends,
    }
    return {
        "open_positions": positions,
        "open_position_count": len(positions),
        "applied_corporate_actions": applied_events,
        **{key: _decimal_string(value) for key, value in exact.items()},
        "exact_fractions": {key: _fraction_material(value) for key, value in exact.items()},
    }


class EventAwarePaperPositionStateLedger:
    """Append-only event-aware paper positions; cannot submit or recommend orders."""

    def __init__(
        self,
        path: str | Path,
        execution_ledger: LocalPaperExecutionLedger,
        corporate_action_ledger: CorporateActionLedger,
    ) -> None:
        self.path = Path(path)
        self.execution_ledger = execution_ledger
        self.corporate_action_ledger = corporate_action_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Event-aware-position ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank event-aware-position line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at event-aware-position line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Event-aware-position line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def _eligible(
        prefix: Sequence[Mapping[str, Any]], portfolio_version: str, as_of: datetime
    ) -> list[dict[str, Any]]:
        return [
            dict(fill)
            for fill in prefix
            if fill.get("portfolio_version") == portfolio_version
            and _as_datetime(fill["filled_at"]) <= as_of
        ]

    def _evidence(
        self, fills: Sequence[Mapping[str, Any]], as_of: datetime
    ) -> list[dict[str, Any]]:
        result = []
        for fill in fills:
            if fill.get("side") != "BUY":
                continue
            item = self.corporate_action_ledger.evidence_for(
                fill_id=fill["fill_id"], through_at=as_of
            )
            if item is None:
                raise ValueError(
                    f"Complete corporate-action evidence through as_of is missing for {fill['fill_id']}"
                )
            result.append(item)
        return sorted(result, key=lambda item: item["fill_id"])

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
        all_fills = self.execution_ledger.verify()
        fills = self._eligible(all_fills, version, effective)
        identity = _common_identity(fills)
        evidence = self._evidence(fills, effective)
        if any(_as_datetime(item["retrieved_at"]) > calculated for item in evidence):
            raise ValueError("calculated_at cannot predate corporate-action evidence")
        state = _state(fills, evidence, effective)
        fill_hashes = [fill["record_hash"] for fill in fills]
        evidence_hashes = [item["record_hash"] for item in evidence]
        result = {
            "schema_version": EVENT_POSITION_SCHEMA_VERSION,
            "calculation_version": EVENT_POSITION_CALCULATION_VERSION,
            "snapshot_id": _snapshot_id(version, effective, fill_hashes, evidence_hashes),
            "status": "CALCULATED",
            "scope": "EVENT_AWARE_LOCAL_SIMULATED_PAPER_POSITION_STATE",
            "simulation_only": True,
            "currency": "USD",
            "portfolio_version": version,
            "as_of": effective.isoformat(),
            "calculated_at": calculated.isoformat(),
            "source_fill_count": len(all_fills),
            "source_fill_tail_hash": (
                all_fills[-1]["record_hash"] if all_fills else GENESIS_HASH
            ),
            "supporting_fill_ids": [fill["fill_id"] for fill in fills],
            "supporting_fill_record_hashes": fill_hashes,
            "supporting_corporate_action_evidence_ids": [
                item["evidence_id"] for item in evidence
            ],
            "supporting_corporate_action_record_hashes": evidence_hashes,
            "fill_inclusion_policy": "PINNED_PREFIX_AND_FILLED_AT_OR_BEFORE_AS_OF",
            "event_inclusion_policy": "COMPLETE_BUY_EVIDENCE_THROUGH_EXACT_AS_OF",
            "fifo_policy": "EVENT_ADJUSTED_FILLED_AT_THEN_FILL_ID_EXACT_PARTIAL_LOTS",
            "dividend_policy": "GROSS_USD_CASH_PAID_BY_AS_OF_NO_REINVESTMENT",
            "corporate_actions_modelled": True,
            "dividends_modelled": True,
            "broker_cash_reconciled": False,
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
            raise LedgerIntegrityError("Invalid event-aware source-fill count") from error
        if count <= 0 or count > len(fills):
            raise LedgerIntegrityError("Event-aware source prefix is unavailable")
        prefix = fills[:count]
        if prefix[-1].get("record_hash") != record.get("source_fill_tail_hash"):
            raise LedgerIntegrityError("Event-aware source prefix has changed")
        return prefix

    def _pinned_support(self, record: Mapping[str, Any]):
        prefix = self._source_prefix(record)
        fills, fill_reasons = resolve_pinned_records(
            prefix,
            record.get("supporting_fill_ids"),
            record.get("supporting_fill_record_hashes"),
            id_field="fill_id",
            label="event-aware fill",
        )
        evidence, evidence_reasons = resolve_pinned_records(
            self.corporate_action_ledger.verify(),
            record.get("supporting_corporate_action_evidence_ids"),
            record.get("supporting_corporate_action_record_hashes"),
            id_field="evidence_id",
            label="event-aware corporate-action",
        )
        return prefix, list(fills), list(evidence), [*fill_reasons, *evidence_reasons]

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(f"Event-aware-position chain is broken at {index}.")
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Event-aware-position record {index} was modified.")
            try:
                prefix, fills, evidence, reasons = self._pinned_support(record)
                if reasons:
                    raise ValueError("; ".join(reasons))
                effective = _as_datetime(record.get("as_of"))
                calculated = _as_datetime(record.get("calculated_at"))
                version = str(record.get("portfolio_version") or "")
                eligible = self._eligible(prefix, version, effective)
                if fills != eligible:
                    raise ValueError("Pinned fills are not the exact eligible source prefix")
                identity = _common_identity(fills)
                state = _state(fills, evidence, effective)
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Event-aware-position record {index} has invalid support or values."
                ) from error
            fill_hashes = [fill["record_hash"] for fill in fills]
            evidence_hashes = [item["record_hash"] for item in evidence]
            expected_id = _snapshot_id(version, effective, fill_hashes, evidence_hashes)
            fixed = {
                "schema_version": EVENT_POSITION_SCHEMA_VERSION,
                "calculation_version": EVENT_POSITION_CALCULATION_VERSION,
                "status": "CALCULATED",
                "scope": "EVENT_AWARE_LOCAL_SIMULATED_PAPER_POSITION_STATE",
                "simulation_only": True,
                "currency": "USD",
                "fill_inclusion_policy": "PINNED_PREFIX_AND_FILLED_AT_OR_BEFORE_AS_OF",
                "event_inclusion_policy": "COMPLETE_BUY_EVIDENCE_THROUGH_EXACT_AS_OF",
                "fifo_policy": "EVENT_ADJUSTED_FILLED_AT_THEN_FILL_ID_EXACT_PARTIAL_LOTS",
                "dividend_policy": "GROSS_USD_CASH_PAID_BY_AS_OF_NO_REINVESTMENT",
                "corporate_actions_modelled": True,
                "dividends_modelled": True,
                "broker_cash_reconciled": False,
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
                and record.get("source_fill_count") == len(prefix)
                and record.get("source_fill_tail_hash") == prefix[-1]["record_hash"]
                and effective <= calculated <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and all(_as_datetime(item["retrieved_at"]) <= calculated for item in evidence)
                and record.get("supporting_fill_ids") == [fill["fill_id"] for fill in fills]
                and record.get("supporting_fill_record_hashes") == fill_hashes
                and record.get("supporting_corporate_action_evidence_ids")
                == [item["evidence_id"] for item in evidence]
                and record.get("supporting_corporate_action_record_hashes") == evidence_hashes
                and all(record.get(key) == value for key, value in fixed.items())
                and all(record.get(key) == value for key, value in identity.items())
                and all(record.get(key) == value for key, value in state.items())
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Event-aware-position record {index} violates its boundary."
                )
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
                    f"Event-aware position snapshot {result['snapshot_id']} already exists."
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
