"""Provider-neutral PIT corporate-action research mechanics.

The ledger in this module is deliberately synthetic and research-only.  It
proves the permanent-identity, five-timestamp and replay-conversion seams that
an authorised data source must later satisfy; it cannot qualify or admit data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.guardrailed_backtest import CorporateAction, TerminalOutcome
from core.portfolio.pit_security_master import PointInTimeSecurityMasterLedger


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "pit-corporate-action-research-v1"
MAX_EVENTS = 10_000
MAX_LEDGER_BYTES = 32 * 1024 * 1024
MAX_CLOCK_SKEW = timedelta(minutes=5)
EVENT_TYPES = frozenset({"SPLIT", "CASH_DIVIDEND", "TERMINAL_OUTCOME"})
TERMINAL_TYPES = frozenset({"DELISTED", "BANKRUPT", "ACQUIRED", "MERGED"})
_FALSE_AUTHORITIES = (
    "coverage_completeness_proven",
    "qualified",
    "train_admitted",
    "validation_admitted",
    "validation_access_authorized",
    "test_admitted",
    "test_access_authorized",
    "performance_claim_allowed",
    "candidate_freeze_allowed",
    "promotion_allowed",
    "broker_submission_enabled",
    "live_trading_enabled",
)
_COMMON_EVENT_FIELDS = {
    "source_event_id",
    "event_type",
    "effective_at",
    "reported_at",
    "available_at",
    "retrieved_at",
    "recorded_at",
    "source_locator",
}
_RECORD_FIELDS = {
    "schema_version",
    "policy_version",
    "snapshot_id",
    "record_type",
    "status",
    "security_id",
    "ticker",
    "covers_from_at",
    "through_at",
    "security_master_record_count",
    "security_master_record_hash",
    "point_in_time_contract",
    "events",
    "event_count",
    "all_events_available_by_effective_at",
    "source_uri",
    "source_locator",
    "source_payload_sha256",
    "synthetic_fixture",
    "provider_request_made",
    "current_ticker_lookup_used",
    *_FALSE_AUTHORITIES,
    "appended_at",
    "previous_hash",
    "record_hash",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, name: str, maximum: int = 1000) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or len(value) > maximum:
        raise ValueError(f"{name} must be canonical nonempty text")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{name} cannot contain control characters")
    return value


def _timestamp(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _decimal(value: Any, name: str, *, positive: bool = False) -> str:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite() or resolved < 0 or (positive and resolved <= 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'non-negative'}")
    return format(resolved, "f")


def _sha256(value: Any, name: str) -> str:
    resolved = _text(value, name, 64)
    if len(resolved) != 64 or resolved != resolved.lower():
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    try:
        bytes.fromhex(resolved)
    except ValueError as error:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest") from error
    return resolved


def _source_uri(value: Any) -> str:
    resolved = _text(value, "source_uri")
    parsed = urlsplit(resolved)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("source_uri must be a credential-free HTTPS URL")
    return resolved


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("unable to complete PIT corporate-action append")
        offset += written


def _identity_at(
    records: Sequence[Mapping[str, Any]],
    *,
    security_id: str,
    effective_at: datetime,
    known_at: datetime,
) -> tuple[bool, str | None, Mapping[str, Any] | None]:
    listed = False
    ticker: str | None = None
    identity_event: Mapping[str, Any] | None = None
    for record in records:
        if record["security_id"] != security_id:
            continue
        if _timestamp(record["effective_at"], "security effective_at") > effective_at:
            continue
        if _timestamp(record["available_at"], "security available_at") > known_at:
            continue
        if record["event_type"] == "LISTED":
            listed, ticker, identity_event = True, record["ticker"], record
        elif record["event_type"] == "TICKER_CHANGED" and listed:
            ticker, identity_event = record["ticker"], record
        elif record["event_type"] == "DELISTED":
            listed = False
    return listed, ticker, identity_event


def _normalize_event(
    value: Mapping[str, Any],
    *,
    security_id: str,
    ticker: str,
    covers_from: datetime,
    through: datetime,
    security_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("each corporate-action event must be an object")
    event_type = str(value.get("event_type") or "").upper()
    extra_fields = {
        "SPLIT": {"split_ratio"},
        "CASH_DIVIDEND": {"cash_per_share", "currency", "cash_paid_at"},
        "TERMINAL_OUTCOME": {
            "terminal_type",
            "recovery_per_share",
            "currency",
            "cash_settled_at",
            "delisting_event_id",
            "delisting_event_record_hash",
        },
    }
    if event_type not in EVENT_TYPES:
        raise ValueError("event_type is unsupported")
    if set(value) != _COMMON_EVENT_FIELDS | extra_fields[event_type]:
        raise ValueError("event fields do not exactly match their event_type")
    effective = _timestamp(value.get("effective_at"), "effective_at")
    reported = _timestamp(value.get("reported_at"), "reported_at")
    available = _timestamp(value.get("available_at"), "available_at")
    retrieved = _timestamp(value.get("retrieved_at"), "retrieved_at")
    recorded = _timestamp(value.get("recorded_at"), "recorded_at")
    if not reported <= available <= retrieved <= recorded:
        raise ValueError("event PIT timestamps must satisfy reported <= available <= retrieved <= recorded")
    if not covers_from <= effective <= through:
        raise ValueError("event effective_at falls outside declared snapshot coverage")
    common = {
        "source_event_id": _text(value.get("source_event_id"), "source_event_id"),
        "event_type": event_type,
        "effective_at": effective.isoformat(timespec="microseconds"),
        "reported_at": reported.isoformat(timespec="microseconds"),
        "available_at": available.isoformat(timespec="microseconds"),
        "retrieved_at": retrieved.isoformat(timespec="microseconds"),
        "recorded_at": recorded.isoformat(timespec="microseconds"),
        "source_locator": _text(value.get("source_locator"), "source_locator"),
        "available_by_effective_at": available <= effective,
    }
    if event_type != "TERMINAL_OUTCOME":
        listed, active_ticker, identity_event = _identity_at(
            security_records,
            security_id=security_id,
            effective_at=effective,
            known_at=effective,
        )
        if not listed or active_ticker != ticker:
            raise ValueError("corporate action does not match a known active permanent identity")
        if identity_event is None:  # pragma: no cover - implied by listed
            raise ValueError("corporate action lacks permanent-identity evidence")
        common.update(
            identity_event_id=identity_event["event_id"],
            identity_event_record_hash=identity_event["record_hash"],
        )
    if event_type == "SPLIT":
        ratio = _decimal(value.get("split_ratio"), "split_ratio", positive=True)
        if Decimal(ratio) == 1:
            raise ValueError("split_ratio cannot be one")
        return {**common, "split_ratio": ratio}
    currency = _text(value.get("currency"), "currency", 3).upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("currency must be a three-letter code")
    if currency != "USD":
        raise ValueError("non-USD cash economics require a separately qualified FX model")
    if event_type == "CASH_DIVIDEND":
        paid = _timestamp(value.get("cash_paid_at"), "cash_paid_at")
        if paid < effective:
            raise ValueError("cash dividend cannot be paid before its effective time")
        return {
            **common,
            "cash_per_share": _decimal(value.get("cash_per_share"), "cash_per_share", positive=True),
            "currency": currency,
            "cash_paid_at": paid.isoformat(timespec="microseconds"),
        }
    terminal_type = _text(value.get("terminal_type"), "terminal_type", 20).upper()
    if terminal_type not in TERMINAL_TYPES:
        raise ValueError("terminal_type is unsupported")
    settled = _timestamp(value.get("cash_settled_at"), "cash_settled_at")
    if settled < effective:
        raise ValueError("terminal cash cannot settle before the outcome is effective")
    delisting_id = _text(value.get("delisting_event_id"), "delisting_event_id")
    delisting_hash = _sha256(
        value.get("delisting_event_record_hash"), "delisting_event_record_hash"
    )
    delisting = next(
        (
            item
            for item in security_records
            if item["event_id"] == delisting_id
            and item["record_hash"] == delisting_hash
        ),
        None,
    )
    if (
        delisting is None
        or delisting["event_type"] != "DELISTED"
        or delisting["security_id"] != security_id
        or delisting["ticker"] != ticker
        or _timestamp(delisting["effective_at"], "delisting effective_at") > effective
        or _timestamp(delisting["available_at"], "delisting available_at") > available
    ):
        raise ValueError("terminal outcome must link the exact permanent-identity delisting")
    common.update(
        identity_event_id=delisting["event_id"],
        identity_event_record_hash=delisting["record_hash"],
    )
    return {
        **common,
        "terminal_type": terminal_type,
        "recovery_per_share": _decimal(value.get("recovery_per_share"), "recovery_per_share"),
        "currency": currency,
        "cash_settled_at": settled.isoformat(timespec="microseconds"),
        "delisting_event_id": delisting_id,
        "delisting_event_record_hash": delisting_hash,
    }


@dataclass(frozen=True)
class PITCorporateActionResearchInputs:
    snapshot_id: str
    snapshot_record_hash: str
    corporate_actions: tuple[CorporateAction, ...]
    terminal_outcomes: tuple[TerminalOutcome, ...]
    synthetic_fixture: bool = True
    qualified: bool = False
    admitted: bool = False
    performance_claim_allowed: bool = False
    promotion_allowed: bool = False
    broker_submission_enabled: bool = False
    live_trading_enabled: bool = False

    def __post_init__(self) -> None:
        if self.synthetic_fixture is not True or any(
            getattr(self, name) is not False
            for name in (
                "qualified",
                "admitted",
                "performance_claim_allowed",
                "promotion_allowed",
                "broker_submission_enabled",
                "live_trading_enabled",
            )
        ):
            raise ValueError("research inputs cannot assert qualification or authority")


class PITCorporateActionLedger:
    """Append-only synthetic snapshots linked to the PIT security master."""

    def __init__(
        self,
        path: str | Path,
        security_master: PointInTimeSecurityMasterLedger,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self.security_master = security_master
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or stat.S_IMODE(details.st_mode) != 0o600
                or details.st_nlink != 1
                or details.st_size > MAX_LEDGER_BYTES
            ):
                raise LedgerIntegrityError("PIT corporate-action ledger is unsafe")
            raw = b""
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                raw += chunk
        finally:
            os.close(descriptor)
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("PIT corporate-action ledger has an incomplete final line")
        result = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            try:
                item = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise LedgerIntegrityError(
                    f"invalid PIT corporate-action JSON at line {line_number}"
                ) from error
            if not isinstance(item, dict):
                raise LedgerIntegrityError("PIT corporate-action record is not an object")
            result.append(item)
        return result

    def append_snapshot(
        self,
        *,
        security_id: str,
        ticker: str,
        covers_from_at: str | datetime,
        through_at: str | datetime,
        events: Sequence[Mapping[str, Any]],
        source_uri: str,
        source_locator: str,
        source_payload_sha256: str,
        synthetic_fixture: bool,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        if synthetic_fixture is not True:
            raise ValueError("this research ledger accepts deterministic synthetic fixtures only")
        identifier = _text(security_id, "security_id", 64).upper()
        symbol = _text(ticker, "ticker", 15).upper()
        covers_from = _timestamp(covers_from_at, "covers_from_at")
        through = _timestamp(through_at, "through_at")
        if covers_from > through:
            raise ValueError("snapshot coverage interval is invalid")
        if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
            raise ValueError("events must be a bounded sequence")
        if len(events) > MAX_EVENTS:
            raise ValueError(f"events cannot exceed {MAX_EVENTS}")
        security_records = self.security_master.verify()
        if not security_records:  # pragma: no cover - active identity requires evidence
            raise ValueError("snapshot coverage lacks security-master evidence")
        listed_at_start, ticker_at_start, _ = _identity_at(
            security_records,
            security_id=identifier,
            effective_at=covers_from,
            known_at=covers_from,
        )
        if not listed_at_start or ticker_at_start != symbol:
            raise ValueError("snapshot coverage must begin with a known active permanent identity")
        normalized = [
            _normalize_event(
                event,
                security_id=identifier,
                ticker=symbol,
                covers_from=covers_from,
                through=through,
                security_records=security_records,
            )
            for event in events
        ]
        normalized.sort(
            key=lambda item: (item["effective_at"], item["event_type"], item["source_event_id"])
        )
        event_ids = [item["source_event_id"] for item in normalized]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("source_event_id values must be unique within a snapshot")
        economic_keys = [
            (item["effective_at"], item["event_type"]) for item in normalized
        ]
        if len(economic_keys) != len(set(economic_keys)):
            raise ValueError("economic corporate-action events must be unique")
        if sum(item["event_type"] == "TERMINAL_OUTCOME" for item in normalized) > 1:
            raise ValueError("each snapshot may contain at most one terminal outcome")
        appended = _timestamp(self._clock(), "append clock")
        if appended > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("append clock cannot be materially in the future")
        if any(_timestamp(item["recorded_at"], "recorded_at") > appended for item in normalized):
            raise ValueError("event recorded_at cannot follow the immutable append time")
        body: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "snapshot_id": "",
            "record_type": "PIT_CORPORATE_ACTION_SNAPSHOT",
            "status": "RESEARCH_ONLY_SYNTHETIC",
            "security_id": identifier,
            "ticker": symbol,
            "covers_from_at": covers_from.isoformat(timespec="microseconds"),
            "through_at": through.isoformat(timespec="microseconds"),
            "security_master_record_count": len(security_records),
            "security_master_record_hash": security_records[-1]["record_hash"],
            "point_in_time_contract": "effective_at/reported_at/available_at/retrieved_at/recorded_at",
            "events": normalized,
            "event_count": len(normalized),
            "all_events_available_by_effective_at": all(
                item["available_by_effective_at"] for item in normalized
            ),
            "source_uri": _source_uri(source_uri),
            "source_locator": _text(source_locator, "source_locator"),
            "source_payload_sha256": _sha256(source_payload_sha256, "source_payload_sha256"),
            "synthetic_fixture": True,
            "provider_request_made": False,
            "current_ticker_lookup_used": False,
            **{name: False for name in _FALSE_AUTHORITIES},
        }
        identity = {key: value for key, value in body.items() if key != "snapshot_id"}
        body["snapshot_id"] = "PCAS-" + _hash(identity)[:32].upper()
        return self._append(body, appended=appended, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        records = self.records()
        previous = GENESIS_HASH
        prior_appended: datetime | None = None
        seen: set[str] = set()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            body = {
                key: value
                for key, value in record.items()
                if key not in {"snapshot_id", "appended_at", "previous_hash", "record_hash"}
            }
            expected_id = "PCAS-" + _hash(body)[:32].upper()
            boundary = (
                set(record) == _RECORD_FIELDS
                and record.get("previous_hash") == previous
                and record.get("record_hash") == _hash(material)
                and record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("snapshot_id") == expected_id
                and expected_id not in seen
                and record.get("record_type") == "PIT_CORPORATE_ACTION_SNAPSHOT"
                and record.get("status") == "RESEARCH_ONLY_SYNTHETIC"
                and record.get("point_in_time_contract")
                == "effective_at/reported_at/available_at/retrieved_at/recorded_at"
                and record.get("synthetic_fixture") is True
                and record.get("provider_request_made") is False
                and record.get("current_ticker_lookup_used") is False
                and all(record.get(name) is False for name in _FALSE_AUTHORITIES)
            )
            if not boundary:
                raise LedgerIntegrityError(f"PIT corporate-action record {index} violates its boundary")
            try:
                rebuilt = self._rebuild_body(record)
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"PIT corporate-action record {index} is invalid"
                ) from error
            if rebuilt != {key: record[key] for key in rebuilt}:
                raise LedgerIntegrityError(f"PIT corporate-action record {index} was modified")
            appended = _timestamp(record["appended_at"], "appended_at")
            if prior_appended is not None and appended < prior_appended:
                raise LedgerIntegrityError("PIT corporate-action append time moved backwards")
            seen.add(expected_id)
            previous = record["record_hash"]
            prior_appended = appended
        return records

    def materialize_research_inputs(self, snapshot_id: str) -> PITCorporateActionResearchInputs:
        snapshot = next(
            (item for item in self.verify() if item["snapshot_id"] == snapshot_id),
            None,
        )
        if snapshot is None:
            raise ValueError("unknown PIT corporate-action snapshot")
        late = [
            item["source_event_id"]
            for item in snapshot["events"]
            if item["available_by_effective_at"] is not True
        ]
        if late:
            raise ValueError("late corporate-action knowledge cannot be materialized for replay")
        actions: list[CorporateAction] = []
        outcomes: list[TerminalOutcome] = []
        for event in snapshot["events"]:
            common = {
                "symbol": snapshot["ticker"],
                "effective_at": _timestamp(event["effective_at"], "effective_at"),
                "available_at": _timestamp(event["available_at"], "available_at"),
                "source_locator": event["source_locator"],
            }
            if event["event_type"] == "SPLIT":
                actions.append(
                    CorporateAction(
                        **common,
                        action_type="SPLIT",
                        split_ratio=Decimal(event["split_ratio"]),
                    )
                )
            elif event["event_type"] == "CASH_DIVIDEND":
                actions.append(
                    CorporateAction(
                        **common,
                        action_type="CASH_DIVIDEND",
                        cash_per_share=Decimal(event["cash_per_share"]),
                        cash_paid_at=_timestamp(event["cash_paid_at"], "cash_paid_at"),
                    )
                )
            else:
                outcomes.append(
                    TerminalOutcome(
                        **common,
                        terminal_type=event["terminal_type"],
                        recovery_per_share=Decimal(event["recovery_per_share"]),
                        cash_settled_at=_timestamp(event["cash_settled_at"], "cash_settled_at"),
                    )
                )
        return PITCorporateActionResearchInputs(
            snapshot_id=snapshot["snapshot_id"],
            snapshot_record_hash=snapshot["record_hash"],
            corporate_actions=tuple(actions),
            terminal_outcomes=tuple(outcomes),
        )

    def _rebuild_body(self, record: Mapping[str, Any]) -> dict[str, Any]:
        covers_from = _timestamp(record.get("covers_from_at"), "covers_from_at")
        through = _timestamp(record.get("through_at"), "through_at")
        if covers_from > through:
            raise ValueError("snapshot coverage interval is invalid")
        identifier = _text(record.get("security_id"), "security_id", 64).upper()
        symbol = _text(record.get("ticker"), "ticker", 15).upper()
        master_records = self.security_master.verify()
        master_count = record.get("security_master_record_count")
        if (
            not isinstance(master_count, int)
            or isinstance(master_count, bool)
            or master_count <= 0
            or master_count > len(master_records)
        ):
            raise ValueError("security-master evidence prefix is invalid")
        master_hash = _sha256(
            record.get("security_master_record_hash"),
            "security_master_record_hash",
        )
        if master_records[master_count - 1]["record_hash"] != master_hash:
            raise ValueError("security-master evidence prefix does not match its pinned hash")
        security_records = master_records[:master_count]
        listed_at_start, ticker_at_start, _ = _identity_at(
            security_records,
            security_id=identifier,
            effective_at=covers_from,
            known_at=covers_from,
        )
        if not listed_at_start or ticker_at_start != symbol:
            raise ValueError("snapshot coverage must begin with a known active permanent identity")
        events = [
            _normalize_event(
                {
                    key: value
                    for key, value in event.items()
                    if key
                    not in {
                        "available_by_effective_at",
                        "identity_event_id",
                        "identity_event_record_hash",
                    }
                },
                security_id=identifier,
                ticker=symbol,
                covers_from=covers_from,
                through=through,
                security_records=security_records,
            )
            for event in record.get("events", [])
        ]
        events.sort(key=lambda item: (item["effective_at"], item["event_type"], item["source_event_id"]))
        if len(events) > MAX_EVENTS:
            raise ValueError(f"events cannot exceed {MAX_EVENTS}")
        source_ids = [item["source_event_id"] for item in events]
        economic_keys = [(item["effective_at"], item["event_type"]) for item in events]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_event_id values must be unique within a snapshot")
        if len(economic_keys) != len(set(economic_keys)):
            raise ValueError("economic corporate-action events must be unique")
        if sum(item["event_type"] == "TERMINAL_OUTCOME" for item in events) > 1:
            raise ValueError("each snapshot may contain at most one terminal outcome")
        body = {
            key: record.get(key)
            for key in record
            if key not in {"appended_at", "previous_hash", "record_hash"}
        }
        body["events"] = events
        body["security_id"] = identifier
        body["ticker"] = symbol
        body["covers_from_at"] = covers_from.isoformat(timespec="microseconds")
        body["through_at"] = through.isoformat(timespec="microseconds")
        body["event_count"] = len(events)
        body["all_events_available_by_effective_at"] = all(
            item["available_by_effective_at"] for item in events
        )
        _source_uri(record.get("source_uri"))
        _text(record.get("source_locator"), "source_locator")
        _sha256(record.get("source_payload_sha256"), "source_payload_sha256")
        appended = _timestamp(record.get("appended_at"), "appended_at")
        if appended > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("appended_at cannot be materially in the future")
        if any(_timestamp(item["recorded_at"], "recorded_at") > appended for item in events):
            raise ValueError("event recorded_at cannot follow the immutable append time")
        return body

    def _append(
        self,
        body: dict[str, Any],
        *,
        appended: datetime,
        allow_existing: bool,
    ) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["snapshot_id"] == body["snapshot_id"]),
                None,
            )
            if existing is not None:
                ignored = {"appended_at", "previous_hash", "record_hash"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == body:
                    return existing
                raise LedgerIntegrityError("PIT corporate-action snapshot already exists")
            new_start = _timestamp(body["covers_from_at"], "covers_from_at")
            new_end = _timestamp(body["through_at"], "through_at")
            if any(
                item["security_id"] == body["security_id"]
                and new_start <= _timestamp(item["through_at"], "through_at")
                and _timestamp(item["covers_from_at"], "covers_from_at") <= new_end
                for item in records
            ):
                raise LedgerIntegrityError(
                    "overlapping PIT corporate-action snapshot coverage is ambiguous"
                )
            if records and appended < _timestamp(records[-1]["appended_at"], "prior appended_at"):
                raise ValueError("append clock cannot precede the prior immutable record")
            material = {
                **body,
                "appended_at": appended.isoformat(timespec="microseconds"),
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _hash(material)}
            payload = (_canonical_json(record) + "\n").encode("utf-8")
            target = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                details = os.fstat(target)
                if (
                    not stat.S_ISREG(details.st_mode)
                    or stat.S_IMODE(details.st_mode) != 0o600
                    or details.st_nlink != 1
                    or details.st_size + len(payload) > MAX_LEDGER_BYTES
                ):
                    raise LedgerIntegrityError("PIT corporate-action ledger target is unsafe")
                _write_all(target, payload)
                os.fsync(target)
            finally:
                os.close(target)
            return self.verify()[-1]
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
