from __future__ import annotations

"""Causal replay signals derived only from verified immutable decisions."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from core.decision_ledger import (
    GENESIS_HASH,
    InvestmentDecisionLedger,
    LedgerIntegrityError,
    canonical_timestamp,
)
from core.guardrailed_backtest import (
    ACTION_ENTER_LONG,
    ACTION_EXIT_LONG,
    ACTION_HOLD,
    MarketBar,
    ReplayDataAttestation,
)
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "verified-recorded-decision-causal-signal-v1"
STRATEGY_POLICY_VERSION = "recorded-decision-signal-replay-strategy-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DECISION_TO_ACTION = {
    "STRONG_BUY": ACTION_ENTER_LONG,
    "BUY": ACTION_ENTER_LONG,
    "WATCHLIST": ACTION_HOLD,
    "HOLD": ACTION_HOLD,
    "AVOID": ACTION_EXIT_LONG,
}
FIXED_FALSE = (
    "forecast_validated",
    "performance_claim_allowed",
    "paper_trade_promotion_allowed",
    "broker_connection_allowed",
    "order_submitted",
    "live_trading_enabled",
)
_VERIFIED_LEDGER_CONSTRUCTION = object()


def _required(value: Any, name: str, maximum: int = 300) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _timestamp(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").strip().lower()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _action(decision: Any) -> tuple[str, str]:
    resolved = _required(decision, "decision", 40).upper()
    if resolved not in DECISION_TO_ACTION:
        raise ValueError("decision has no approved replay-action mapping")
    return resolved, DECISION_TO_ACTION[resolved]


def _signal_id(decision_hash: str, bound_close: str) -> str:
    return "RSIG-" + hashlib.sha256(
        _canonical_json([decision_hash, bound_close, POLICY_VERSION]).encode("utf-8")
    ).hexdigest()[:32].upper()


def _replay_source_identity(
    attestation: ReplayDataAttestation,
) -> tuple[dict[str, Any], str]:
    attestation.validate()
    material = {
        "source_id": attestation.source_id,
        "source_content_sha256": attestation.source_content_sha256,
        "validation_receipt_sha256": attestation.validation_receipt_sha256,
        "derivation_policy_version": attestation.derivation_policy_version,
        "evidence_role_hashes": [list(value) for value in attestation.evidence_role_hashes],
    }
    digest = hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    return material, digest


def _market_schedule(
    bars: Sequence[MarketBar],
    *,
    ticker: str,
    available_at: datetime,
) -> tuple[MarketBar, str]:
    symbol = _required(ticker, "ticker", 32).upper()
    relevant = tuple(
        sorted(
            (bar for bar in bars if bar.symbol.upper() == symbol),
            key=lambda bar: (bar.open_at, bar.close_at, bar.available_at),
        )
    )
    if not relevant:
        raise ValueError("market bars are required for the decision ticker")
    if relevant[0].open_at > available_at:
        raise ValueError("market-bar schedule does not cover decision availability")
    if len({(bar.open_at, bar.close_at) for bar in relevant}) != len(relevant):
        raise ValueError("market-bar schedule contains duplicate sessions")
    if any(left.close_at >= right.open_at for left, right in zip(relevant, relevant[1:])):
        raise ValueError("market-bar schedule overlaps or is out of order")
    eligible = tuple(bar for bar in relevant if bar.close_at >= available_at)
    if not eligible:
        raise ValueError("no eligible market close exists after decision availability")
    material = [
        {
            "ticker": bar.symbol.upper(),
            "open_at": bar.open_at.isoformat(),
            "close_at": bar.close_at.isoformat(),
            "available_at": bar.available_at.isoformat(),
        }
        for bar in relevant
    ]
    schedule_sha256 = hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()
    return eligible[0], schedule_sha256


@dataclass(frozen=True, slots=True)
class RecordedDecisionSignal:
    signal_id: str
    decision_id: str
    decision_record_hash: str
    ticker: str
    source_decision: str
    action: str
    data_as_of: datetime
    available_at: datetime
    bound_bar_close_at: datetime
    market_schedule_sha256: str
    replay_source_identity_sha256: str


def _to_signal(record: Mapping[str, Any]) -> RecordedDecisionSignal:
    return RecordedDecisionSignal(
        signal_id=_required(record.get("signal_id"), "signal_id", 100),
        decision_id=_required(record.get("decision_id"), "decision_id", 100),
        decision_record_hash=_sha256(
            record.get("decision_record_hash"), "decision_record_hash"
        ),
        ticker=_required(record.get("ticker"), "ticker", 32).upper(),
        source_decision=_required(record.get("source_decision"), "source_decision", 40).upper(),
        action=_required(record.get("action"), "action", 40).upper(),
        data_as_of=_timestamp(record.get("data_as_of"), "data_as_of"),
        available_at=_timestamp(record.get("available_at"), "available_at"),
        bound_bar_close_at=_timestamp(
            record.get("bound_bar_close_at"), "bound_bar_close_at"
        ),
        market_schedule_sha256=_sha256(
            record.get("market_schedule_sha256"), "market_schedule_sha256"
        ),
        replay_source_identity_sha256=_sha256(
            record.get("replay_source_identity_sha256"),
            "replay_source_identity_sha256",
        ),
    )


class RecordedDecisionSignalLedger:
    """Append-only signal bindings. It cannot execute, promote or trade."""

    def __init__(
        self,
        path: str | Path,
        decision_ledger: InvestmentDecisionLedger,
    ) -> None:
        self.path = Path(path)
        self.decision_ledger = decision_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Recorded-signal ledger has an incomplete final line."
            )
        values: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank recorded-signal line at {line_number}."
                    )
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at recorded-signal line {line_number}."
                    ) from error
                if not isinstance(value, dict):
                    raise LedgerIntegrityError(
                        f"Recorded-signal line {line_number} is not an object."
                    )
                values.append(value)
        return values

    def _decision(self, decision_id: str) -> dict[str, Any] | None:
        return next(
            (
                value
                for value in self.decision_ledger.verify()
                if value.get("decision_id") == decision_id
            ),
            None,
        )

    def record(
        self,
        *,
        decision_id: str,
        market_bars: Sequence[MarketBar],
        data_attestation: ReplayDataAttestation,
        recorded_by: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        decision_key = _required(decision_id, "decision_id", 100)
        decision = self._decision(decision_key)
        if decision is None:
            raise ValueError("a verified investment decision is required")
        source_decision, action = _action(decision.get("decision"))
        data_as_of = _timestamp(decision.get("data_as_of"), "decision.data_as_of")
        available = _timestamp(decision.get("decided_at"), "decision.decided_at")
        bound_bar, schedule_sha256 = _market_schedule(
            market_bars,
            ticker=str(decision.get("ticker") or ""),
            available_at=available,
        )
        bound_close = bound_bar.close_at
        replay_source_identity, replay_source_identity_sha256 = (
            _replay_source_identity(data_attestation)
        )
        if not data_as_of <= available <= bound_close:
            raise ValueError(
                "decision data and availability must not postdate the bound market close"
            )
        recorded = _timestamp(
            recorded_at or datetime.now(timezone.utc), "recorded_at"
        )
        if recorded < bound_close:
            raise ValueError("recorded_at cannot predate the bound market close")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        decision_hash = _sha256(decision.get("record_hash"), "decision_record_hash")
        signal_id = _signal_id(decision_hash, bound_close.isoformat())
        result = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "signal_id": signal_id,
            "record_type": "VERIFIED_RECORDED_DECISION_CAUSAL_SIGNAL",
            "status": "RECORDED_CAUSAL_SIGNAL_NOT_EXECUTED",
            "recorded_at": recorded.isoformat(),
            "recorded_by": _required(recorded_by, "recorded_by", 100),
            "decision_id": decision_key,
            "decision_record_hash": decision_hash,
            "ticker": _required(decision.get("ticker"), "ticker", 32).upper(),
            "source_decision": source_decision,
            "action": action,
            "data_as_of": data_as_of.isoformat(),
            "available_at": available.isoformat(),
            "bound_bar_close_at": bound_close.isoformat(),
            "market_schedule_sha256": schedule_sha256,
            "replay_source_identity": replay_source_identity,
            "replay_source_identity_sha256": replay_source_identity_sha256,
            "portfolio_version": decision.get("portfolio_version"),
            "strategy_git_revision": decision.get("git_revision"),
            "model_versions": decision.get("model_versions"),
            "mapping_is_fixed": True,
            "next_bar_execution_required": True,
            "simulation_only": True,
            **{field: False for field in FIXED_FALSE},
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        seen_decisions: set[str] = set()
        seen_slots: set[tuple[str, str]] = set()
        records = self.records()
        decisions = {
            str(value.get("decision_id")): value
            for value in self.decision_ledger.verify()
            if value.get("decision_id")
        }
        for index, record in enumerate(records, start=1):
            material = {
                key: value for key, value in record.items() if key != "record_hash"
            }
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(
                    f"Recorded-signal record {index} has been modified."
                )
            decision = decisions.get(str(record.get("decision_id") or ""))
            if decision is None:
                raise LedgerIntegrityError(
                    f"Recorded-signal record {index} lost its decision."
                )
            try:
                signal = _to_signal(record)
                source_decision, action = _action(decision.get("decision"))
                recorded = _timestamp(record.get("recorded_at"), "recorded_at")
                _required(record.get("recorded_by"), "recorded_by", 100)
                decision_hash = _sha256(
                    decision.get("record_hash"), "decision_record_hash"
                )
                decision_data = _timestamp(decision.get("data_as_of"), "data_as_of")
                decision_available = _timestamp(
                    decision.get("decided_at"), "decided_at"
                )
                replay_source_identity = record.get("replay_source_identity")
                if not isinstance(replay_source_identity, dict):
                    raise ValueError("replay_source_identity must be an object")
                replay_source_identity_sha256 = hashlib.sha256(
                    _canonical_json(replay_source_identity).encode("utf-8")
                ).hexdigest()
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Recorded-signal record {index} has invalid values."
                ) from error
            expected_id = _signal_id(
                decision_hash, signal.bound_bar_close_at.isoformat()
            )
            slot = (signal.ticker, signal.bound_bar_close_at.isoformat())
            boundary = (
                record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and signal.signal_id == expected_id
                and expected_id not in seen_ids
                and signal.decision_id not in seen_decisions
                and slot not in seen_slots
                and record.get("record_type")
                == "VERIFIED_RECORDED_DECISION_CAUSAL_SIGNAL"
                and record.get("status") == "RECORDED_CAUSAL_SIGNAL_NOT_EXECUTED"
                and signal.decision_record_hash == decision_hash
                and signal.ticker
                == str(decision.get("ticker") or "").strip().upper()
                and signal.source_decision == source_decision
                and signal.action == action
                and signal.data_as_of == decision_data
                and signal.available_at == decision_available
                and SHA256_PATTERN.fullmatch(signal.market_schedule_sha256)
                and signal.replay_source_identity_sha256
                == replay_source_identity_sha256
                and signal.data_as_of <= signal.available_at
                <= signal.bound_bar_close_at
                and recorded >= signal.bound_bar_close_at
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("portfolio_version") == decision.get("portfolio_version")
                and record.get("strategy_git_revision") == decision.get("git_revision")
                and record.get("model_versions") == decision.get("model_versions")
                and record.get("mapping_is_fixed") is True
                and record.get("next_bar_execution_required") is True
                and record.get("simulation_only") is True
                and all(record.get(field) is False for field in FIXED_FALSE)
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Recorded-signal record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            seen_decisions.add(signal.decision_id)
            seen_slots.add(slot)
            previous_hash = record["record_hash"]
        return records

    def _append(
        self,
        result: dict[str, Any],
        *,
        allow_existing: bool,
    ) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (
                    value
                    for value in records
                    if value.get("signal_id") == result["signal_id"]
                ),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {
                    key: value for key, value in result.items() if key not in ignored
                }:
                    return existing
                raise LedgerIntegrityError("recorded causal signal already exists")
            if any(
                value.get("decision_id") == result["decision_id"]
                for value in records
            ):
                raise LedgerIntegrityError(
                    "investment decision already has its one replay signal"
                )
            if any(
                value.get("ticker") == result["ticker"]
                and value.get("bound_bar_close_at") == result["bound_bar_close_at"]
                for value in records
            ):
                raise LedgerIntegrityError(
                    "ticker and bound market close already have a replay signal"
                )
            material = {
                **result,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            try:
                _write_all(
                    target,
                    (_canonical_json(record) + "\n").encode("utf-8"),
                )
                os.fsync(target)
            finally:
                os.close(target)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


class RecordedDecisionSignalStrategy:
    """Emits each verified signal only at its exact bound close."""

    def __init__(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        market_bars: Sequence[MarketBar] = (),
        data_attestation: ReplayDataAttestation | None = None,
        _verified_ledger_construction: object | None = None,
    ) -> None:
        if _verified_ledger_construction is not _VERIFIED_LEDGER_CONSTRUCTION:
            raise ValueError(
                "recorded-decision strategies must be built from a verified ledger"
            )
        if data_attestation is None:
            raise ValueError("authenticated replay-source identity is required")
        _, replay_source_identity_sha256 = _replay_source_identity(data_attestation)
        signals = tuple(_to_signal(record) for record in records)
        for record, signal in zip(records, signals, strict=True):
            earliest_bar, schedule_sha256 = _market_schedule(
                market_bars,
                ticker=signal.ticker,
                available_at=signal.available_at,
            )
            expected_id = _signal_id(
                signal.decision_record_hash,
                signal.bound_bar_close_at.isoformat(),
            )
            boundary = (
                record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("record_type")
                == "VERIFIED_RECORDED_DECISION_CAUSAL_SIGNAL"
                and record.get("status") == "RECORDED_CAUSAL_SIGNAL_NOT_EXECUTED"
                and signal.signal_id == expected_id
                and signal.source_decision in DECISION_TO_ACTION
                and signal.action == DECISION_TO_ACTION[signal.source_decision]
                and signal.data_as_of <= signal.available_at
                <= signal.bound_bar_close_at
                and signal.bound_bar_close_at == earliest_bar.close_at
                and signal.market_schedule_sha256 == schedule_sha256
                and signal.replay_source_identity_sha256
                == replay_source_identity_sha256
                and record.get("mapping_is_fixed") is True
                and record.get("next_bar_execution_required") is True
                and record.get("simulation_only") is True
                and all(record.get(field) is False for field in FIXED_FALSE)
            )
            if not boundary:
                raise ValueError("record is not a valid recorded-decision signal")
        ordered = tuple(
            sorted(
                signals,
                key=lambda value: (
                    value.bound_bar_close_at,
                    value.ticker,
                    value.signal_id,
                ),
            )
        )
        if len({value.signal_id for value in signals}) != len(signals):
            raise ValueError("recorded signal IDs must be unique")
        if len(
            {(value.ticker, value.bound_bar_close_at) for value in signals}
        ) != len(signals):
            raise ValueError("recorded signal ticker/close slots must be unique")
        self.signals = ordered
        material = [
            {
                "signal_id": value.signal_id,
                "decision_record_hash": value.decision_record_hash,
                "ticker": value.ticker,
                "action": value.action,
                "data_as_of": value.data_as_of.isoformat(),
                "available_at": value.available_at.isoformat(),
                "bound_bar_close_at": value.bound_bar_close_at.isoformat(),
                "market_schedule_sha256": value.market_schedule_sha256,
                "replay_source_identity_sha256": value.replay_source_identity_sha256,
            }
            for value in ordered
        ]
        self.signal_bundle_sha256 = hashlib.sha256(
            _canonical_json(material).encode("utf-8")
        ).hexdigest()
        self.version = f"{STRATEGY_POLICY_VERSION}@{self.signal_bundle_sha256}"
        self._emitted: set[str] = set()

    def __deepcopy__(self, memo: dict[int, Any]) -> "RecordedDecisionSignalStrategy":
        duplicate = object.__new__(type(self))
        memo[id(self)] = duplicate
        duplicate.__dict__ = copy.deepcopy(self.__dict__, memo)
        duplicate._emitted = set()
        return duplicate

    @classmethod
    def from_ledger(
        cls,
        ledger: RecordedDecisionSignalLedger,
        *,
        market_bars: Sequence[MarketBar],
        data_attestation: ReplayDataAttestation,
    ) -> "RecordedDecisionSignalStrategy":
        return cls(
            ledger.verify(),
            market_bars=market_bars,
            data_attestation=data_attestation,
            _verified_ledger_construction=_VERIFIED_LEDGER_CONSTRUCTION,
        )

    def parameters(self) -> dict[str, str]:
        return {"signal_bundle_sha256": self.signal_bundle_sha256}

    def validate_market_schedule(
        self,
        bars: Sequence[MarketBar],
        data_attestation: ReplayDataAttestation,
    ) -> None:
        """Bind the adapter to the exact authenticated bars the engine receives."""

        _, source_identity_sha256 = _replay_source_identity(data_attestation)
        for signal in self.signals:
            earliest_bar, schedule_sha256 = _market_schedule(
                bars,
                ticker=signal.ticker,
                available_at=signal.available_at,
            )
            if (
                earliest_bar.close_at != signal.bound_bar_close_at
                or schedule_sha256 != signal.market_schedule_sha256
                or source_identity_sha256 != signal.replay_source_identity_sha256
            ):
                raise ValueError(
                    "engine market bars do not match the signal's causal schedule"
                )

    def validate_replay_completion(self) -> None:
        missing = {
            signal.signal_id
            for signal in self.signals
            if signal.signal_id not in self._emitted
        }
        if missing:
            raise ValueError("replay did not consume every recorded causal signal")

    def decide(
        self,
        symbol: str,
        history_through_signal_close: Sequence[MarketBar],
        parameters: Mapping[str, Any],
    ) -> str:
        if dict(parameters) != self.parameters():
            raise ValueError("strategy parameters do not pin the recorded-signal bundle")
        if not history_through_signal_close:
            return ACTION_HOLD
        bar = history_through_signal_close[-1]
        skipped = [
            signal
            for signal in self.signals
            if signal.ticker == str(symbol).upper()
            and signal.signal_id not in self._emitted
            and signal.bound_bar_close_at < bar.close_at
        ]
        if skipped:
            raise ValueError("replay skipped a recorded signal's bound market close")
        matches = [
            signal
            for signal in self.signals
            if signal.ticker == str(symbol).upper()
            and signal.bound_bar_close_at == bar.close_at
        ]
        if len(matches) > 1:
            raise ValueError("multiple recorded signals occupy one ticker/close slot")
        if not matches:
            return ACTION_HOLD
        signal = matches[0]
        if signal.available_at > bar.available_at or signal.data_as_of > bar.close_at:
            raise ValueError("recorded signal was not causally available by the bound bar")
        if signal.signal_id in self._emitted:
            return ACTION_HOLD
        self._emitted.add(signal.signal_id)
        return signal.action
