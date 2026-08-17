from __future__ import annotations

"""Immutable point-in-time security identity and index-membership ledger.

The legacy historical-universe ledger keys membership by ticker.  Tickers are
not permanent identities and can be reused, so this Stage-1 ledger keeps every
lifecycle and membership transition under a provider-neutral permanent
security identifier.  Snapshots apply both the event-effective cutoff and the
source-availability cutoff; a current constituent list is never an input.
"""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "pit-security-master-permanent-identity-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SUPPORTED_EVENT_TYPES = frozenset(
    {"LISTED", "TICKER_CHANGED", "INDEX_ADDED", "INDEX_REMOVED", "DELISTED"}
)
SUPPORTED_UNIVERSES = frozenset({"SP500", "NASDAQ100"})
SUPPORTED_EXCHANGES = frozenset({"XNAS", "XNYS", "XASE"})
SUPPORTED_TERMINAL_TREATMENTS = frozenset(
    {
        "NOT_APPLICABLE",
        "ACQUISITION_CASH_OR_STOCK_CONSIDERATION_REQUIRED",
        "BANKRUPTCY_OR_LIQUIDATION_OUTCOME_REQUIRED",
        "LAST_TRADABLE_TOTAL_RETURN_REQUIRED",
    }
)
SECURITY_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._:-]{2,63}$")
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
FIXED_FALSE = (
    "coverage_completeness_proven",
    "terminal_outcome_evidence_complete",
    "partition_admission_authorized",
    "performance_calculated",
    "performance_claim_allowed",
    "broker_submission_enabled",
    "live_trading_enabled",
)
RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "event_id",
        "record_type",
        "status",
        "security_id",
        "event_type",
        "ticker",
        "prior_ticker",
        "issuer_name",
        "exchange_mic",
        "universe",
        "effective_at",
        "reported_at",
        "available_at",
        "retrieved_at",
        "recorded_at",
        "source_uri",
        "source_input_sha256",
        "source_locator",
        "terminal_outcome_treatment",
        "current_membership_used",
        *FIXED_FALSE,
        "previous_hash",
        "record_hash",
    }
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _record_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("Unable to complete PIT security-master append")
        offset += written


def _required(value: Any, name: str, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    resolved = value.strip()
    if not resolved or resolved != value or len(resolved) > maximum:
        raise ValueError(f"{name} must be nonempty canonical text")
    if any(ord(character) < 32 for character in resolved):
        raise ValueError(f"{name} cannot contain control characters")
    return resolved


def _timestamp(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _sha256(value: Any, name: str) -> str:
    resolved = _required(value, name, 64).lower()
    if SHA256_PATTERN.fullmatch(resolved) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _https_uri(value: Any, name: str) -> str:
    resolved = _required(value, name)
    parsed = urlsplit(resolved)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{name} must be a credential-free HTTPS URL")
    if parsed.username or parsed.password or parsed.query:
        raise ValueError(f"{name} must be a credential-free HTTPS URL")
    return resolved


def _security_id(value: Any) -> str:
    resolved = _required(value, "security_id", 64).upper()
    if SECURITY_ID_PATTERN.fullmatch(resolved) is None:
        raise ValueError("security_id must be a canonical permanent identifier")
    return resolved


def _ticker(value: Any, name: str = "ticker") -> str:
    resolved = _required(value, name, 15).upper()
    if TICKER_PATTERN.fullmatch(resolved) is None:
        raise ValueError(f"{name} must be a canonical U.S. ticker")
    return resolved


def _event_id(record: Mapping[str, Any]) -> str:
    identity = [
        record["security_id"],
        record["event_type"],
        record["ticker"],
        record["prior_ticker"],
        record["universe"],
        record["effective_at"],
        record["available_at"],
        record["source_input_sha256"],
        record["source_locator"],
        POLICY_VERSION,
    ]
    return "SMEV-" + hashlib.sha256(
        _canonical_json(identity).encode("utf-8")
    ).hexdigest()[:32].upper()


def _new_state() -> dict[str, Any]:
    return {
        "listed": False,
        "ever_listed": False,
        "ticker": None,
        "issuer_name": None,
        "exchange_mic": None,
        "listing_event": None,
        "memberships": {},
    }


def _apply_event(state: dict[str, Any], record: Mapping[str, Any]) -> None:
    event_type = record["event_type"]
    if event_type == "LISTED":
        if state["ever_listed"]:
            raise LedgerIntegrityError("A permanent security identifier cannot be listed twice")
        state.update(
            {
                "listed": True,
                "ever_listed": True,
                "ticker": record["ticker"],
                "issuer_name": record["issuer_name"],
                "exchange_mic": record["exchange_mic"],
                "listing_event": record,
            }
        )
        return
    if not state["listed"]:
        raise LedgerIntegrityError("Security-master event requires a currently listed security")
    if event_type != "TICKER_CHANGED" and record["ticker"] != state["ticker"]:
        raise LedgerIntegrityError("Security-master event ticker does not match active identity")
    if (
        record["issuer_name"] != state["issuer_name"]
        or record["exchange_mic"] != state["exchange_mic"]
    ):
        raise LedgerIntegrityError(
            "Security-master event issuer or exchange contradicts active identity"
        )
    if event_type == "TICKER_CHANGED":
        if record["prior_ticker"] != state["ticker"] or record["ticker"] == state["ticker"]:
            raise LedgerIntegrityError("Ticker change does not match the active ticker")
        state["ticker"] = record["ticker"]
    elif event_type == "INDEX_ADDED":
        if record["universe"] in state["memberships"]:
            raise LedgerIntegrityError("Index addition duplicates active membership")
        state["memberships"][record["universe"]] = record
    elif event_type == "INDEX_REMOVED":
        if record["universe"] not in state["memberships"]:
            raise LedgerIntegrityError("Index removal lacks active membership")
        del state["memberships"][record["universe"]]
    elif event_type == "DELISTED":
        if record["ticker"] != state["ticker"]:
            raise LedgerIntegrityError("Delisting ticker does not match the active ticker")
        state["listed"] = False
        state["memberships"].clear()
    else:  # pragma: no cover - guarded by record validation
        raise LedgerIntegrityError("Unsupported security-master event")


class PointInTimeSecurityMasterLedger:
    """Append-only permanent-identity ledger with PIT universe reconstruction."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("PIT security-master ledger has an incomplete final line")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank PIT security-master line at {line_number}"
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at PIT security-master line {line_number}"
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"PIT security-master line {line_number} is not an object"
                    )
                records.append(record)
        return records

    def record_event(
        self,
        *,
        security_id: str,
        event_type: str,
        ticker: str,
        issuer_name: str,
        exchange_mic: str,
        effective_at: str | datetime,
        reported_at: str | datetime,
        available_at: str | datetime,
        retrieved_at: str | datetime,
        source_uri: str,
        source_input_sha256: str,
        source_locator: str,
        universe: str | None = None,
        prior_ticker: str | None = None,
        terminal_outcome_treatment: str = "NOT_APPLICABLE",
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        identifier = _security_id(security_id)
        resolved_event = _required(event_type, "event_type", 30).upper()
        if resolved_event not in SUPPORTED_EVENT_TYPES:
            raise ValueError("event_type is unsupported")
        resolved_ticker = _ticker(ticker)
        resolved_prior = (
            _ticker(prior_ticker, "prior_ticker") if prior_ticker is not None else None
        )
        exchange = _required(exchange_mic, "exchange_mic", 4).upper()
        if exchange not in SUPPORTED_EXCHANGES:
            raise ValueError("exchange_mic is unsupported")
        resolved_universe = universe.upper() if isinstance(universe, str) else None
        if resolved_event in {"INDEX_ADDED", "INDEX_REMOVED"}:
            if resolved_universe not in SUPPORTED_UNIVERSES:
                raise ValueError("index events require a supported universe")
        elif resolved_universe is not None:
            raise ValueError("universe is valid only for index membership events")
        if resolved_event == "TICKER_CHANGED":
            if resolved_prior is None:
                raise ValueError("TICKER_CHANGED requires prior_ticker")
        elif resolved_prior is not None:
            raise ValueError("prior_ticker is valid only for TICKER_CHANGED")
        treatment = _required(
            terminal_outcome_treatment, "terminal_outcome_treatment", 80
        ).upper()
        if treatment not in SUPPORTED_TERMINAL_TREATMENTS:
            raise ValueError("terminal_outcome_treatment is unsupported")
        if resolved_event == "DELISTED":
            if treatment == "NOT_APPLICABLE":
                raise ValueError("DELISTED requires an explicit terminal outcome treatment")
        elif treatment != "NOT_APPLICABLE":
            raise ValueError("terminal outcome treatment is valid only for DELISTED")
        effective = _timestamp(effective_at, "effective_at")
        reported = _timestamp(reported_at, "reported_at")
        available = _timestamp(available_at, "available_at")
        retrieved = _timestamp(retrieved_at, "retrieved_at")
        recorded = _timestamp(recorded_at or datetime.now(timezone.utc), "recorded_at")
        if reported > available or available > retrieved or retrieved > recorded:
            raise ValueError(
                "reported_at, available_at, retrieved_at and recorded_at must be chronological"
            )
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        result: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "event_id": "",
            "record_type": "POINT_IN_TIME_SECURITY_MASTER_EVENT",
            "status": "RECORDED_RESEARCH_ONLY",
            "security_id": identifier,
            "event_type": resolved_event,
            "ticker": resolved_ticker,
            "prior_ticker": resolved_prior,
            "issuer_name": _required(issuer_name, "issuer_name"),
            "exchange_mic": exchange,
            "universe": resolved_universe,
            "effective_at": effective.isoformat(),
            "reported_at": reported.isoformat(),
            "available_at": available.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "recorded_at": recorded.isoformat(),
            "source_uri": _https_uri(source_uri, "source_uri"),
            "source_input_sha256": _sha256(
                source_input_sha256, "source_input_sha256"
            ),
            "source_locator": _required(source_locator, "source_locator"),
            "terminal_outcome_treatment": treatment,
            "current_membership_used": False,
            **{name: False for name in FIXED_FALSE},
        }
        result["event_id"] = _event_id(result)
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        by_security: dict[str, list[dict[str, Any]]] = {}
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                set(record) != RECORD_FIELDS
                or record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(
                    f"PIT security-master record {index} has been modified"
                )
            try:
                identifier = _security_id(record.get("security_id"))
                event_type = _required(record.get("event_type"), "event_type", 30).upper()
                _ticker(record.get("ticker"))
                if record.get("prior_ticker") is not None:
                    _ticker(record.get("prior_ticker"), "prior_ticker")
                exchange = _required(record.get("exchange_mic"), "exchange_mic", 4).upper()
                effective = _timestamp(record.get("effective_at"), "effective_at")
                reported = _timestamp(record.get("reported_at"), "reported_at")
                available = _timestamp(record.get("available_at"), "available_at")
                retrieved = _timestamp(record.get("retrieved_at"), "retrieved_at")
                recorded = _timestamp(record.get("recorded_at"), "recorded_at")
                _https_uri(record.get("source_uri"), "source_uri")
                _sha256(record.get("source_input_sha256"), "source_input_sha256")
                _required(record.get("source_locator"), "source_locator")
                _required(record.get("issuer_name"), "issuer_name")
                treatment = _required(
                    record.get("terminal_outcome_treatment"),
                    "terminal_outcome_treatment",
                    80,
                ).upper()
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"PIT security-master record {index} is invalid"
                ) from error
            universe = record.get("universe")
            event_id = record.get("event_id")
            prior_ticker_valid = (
                event_type == "TICKER_CHANGED" and record.get("prior_ticker") is not None
            ) or (event_type != "TICKER_CHANGED" and record.get("prior_ticker") is None)
            universe_valid = (
                event_type in {"INDEX_ADDED", "INDEX_REMOVED"}
                and universe in SUPPORTED_UNIVERSES
            ) or (
                event_type not in {"INDEX_ADDED", "INDEX_REMOVED"}
                and universe is None
            )
            treatment_valid = (
                event_type == "DELISTED" and treatment != "NOT_APPLICABLE"
            ) or (
                event_type != "DELISTED" and treatment == "NOT_APPLICABLE"
            )
            boundary = (
                record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("record_type") == "POINT_IN_TIME_SECURITY_MASTER_EVENT"
                and record.get("status") == "RECORDED_RESEARCH_ONLY"
                and identifier == record.get("security_id")
                and event_type in SUPPORTED_EVENT_TYPES
                and exchange in SUPPORTED_EXCHANGES
                and prior_ticker_valid
                and universe_valid
                and treatment in SUPPORTED_TERMINAL_TREATMENTS
                and treatment_valid
                and reported <= available <= retrieved <= recorded
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and event_id == _event_id(record)
                and event_id not in seen_ids
                and record.get("current_membership_used") is False
                and all(record.get(name) is False for name in FIXED_FALSE)
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"PIT security-master record {index} violates its boundary"
                )
            security_records = by_security.setdefault(identifier, [])
            if security_records and effective < _timestamp(
                security_records[-1]["effective_at"], "effective_at"
            ):
                raise LedgerIntegrityError(
                    "Security-master effective_at cannot move backwards per security"
                )
            security_records.append(record)
            seen_ids.add(event_id)
            previous_hash = record["record_hash"]
        self._validate_transitions_and_ticker_intervals(by_security)
        return records

    @staticmethod
    def _validate_transitions_and_ticker_intervals(
        by_security: Mapping[str, list[dict[str, Any]]]
    ) -> None:
        intervals_by_ticker: dict[str, list[tuple[datetime, datetime | None, str]]] = {}
        for identifier, records in by_security.items():
            state = _new_state()
            interval_start: datetime | None = None
            interval_ticker: str | None = None
            for record in records:
                effective = _timestamp(record["effective_at"], "effective_at")
                if record["event_type"] in {"TICKER_CHANGED", "DELISTED"}:
                    if interval_start is None or interval_ticker is None:
                        raise LedgerIntegrityError("Ticker interval lacks a listing start")
                    intervals_by_ticker.setdefault(interval_ticker, []).append(
                        (interval_start, effective, identifier)
                    )
                _apply_event(state, record)
                if record["event_type"] in {"LISTED", "TICKER_CHANGED"}:
                    interval_start = effective
                    interval_ticker = record["ticker"]
                elif record["event_type"] == "DELISTED":
                    interval_start = None
                    interval_ticker = None
            if interval_start is not None and interval_ticker is not None:
                intervals_by_ticker.setdefault(interval_ticker, []).append(
                    (interval_start, None, identifier)
                )
        for ticker, intervals in intervals_by_ticker.items():
            ordered = sorted(
                intervals,
                key=lambda item: (item[0], item[1] or datetime.max.replace(tzinfo=timezone.utc)),
            )
            prior_end: datetime | None = None
            prior_identifier: str | None = None
            for start, end, identifier in ordered:
                if prior_end is None and prior_identifier is not None:
                    raise LedgerIntegrityError(
                        f"Ticker {ticker} is assigned to overlapping permanent securities"
                    )
                if prior_end is not None and start < prior_end:
                    raise LedgerIntegrityError(
                        f"Ticker {ticker} is assigned to overlapping permanent securities"
                    )
                prior_end = end
                prior_identifier = identifier

    def snapshot(
        self,
        *,
        universe: str,
        effective_as_of: str | datetime,
        known_as_of: str | datetime,
    ) -> dict[str, Any]:
        resolved_universe = _required(universe, "universe", 20).upper()
        if resolved_universe not in SUPPORTED_UNIVERSES:
            raise ValueError("universe is unsupported")
        effective_cutoff = _timestamp(effective_as_of, "effective_as_of")
        knowledge_cutoff = _timestamp(known_as_of, "known_as_of")
        if knowledge_cutoff > effective_cutoff:
            raise ValueError("knowledge cutoff cannot follow the effective decision cutoff")
        selected = [
            record
            for record in self.verify()
            if _timestamp(record["effective_at"], "effective_at") <= effective_cutoff
            and _timestamp(record["available_at"], "available_at") <= knowledge_cutoff
        ]
        states: dict[str, dict[str, Any]] = {}
        exclusions: list[dict[str, Any]] = []
        for record in selected:
            identifier = record["security_id"]
            state = states.setdefault(identifier, _new_state())
            prior_membership = state["memberships"].get(resolved_universe)
            prior_ticker = state["ticker"]
            _apply_event(state, record)
            if record["event_type"] == "INDEX_REMOVED" and record["universe"] == resolved_universe:
                exclusions.append(
                    {
                        "security_id": identifier,
                        "ticker": prior_ticker,
                        "issuer_name": state["issuer_name"],
                        "exit_type": "INDEX_REMOVED",
                        "exit_effective_at": record["effective_at"],
                        "exit_event_id": record["event_id"],
                        "exit_event_record_hash": record["record_hash"],
                        "terminal_outcome_treatment": "NOT_APPLICABLE",
                    }
                )
            elif record["event_type"] == "DELISTED" and prior_membership is not None:
                exclusions.append(
                    {
                        "security_id": identifier,
                        "ticker": prior_ticker,
                        "issuer_name": state["issuer_name"],
                        "exit_type": "DELISTED",
                        "exit_effective_at": record["effective_at"],
                        "exit_event_id": record["event_id"],
                        "exit_event_record_hash": record["record_hash"],
                        "terminal_outcome_treatment": record[
                            "terminal_outcome_treatment"
                        ],
                    }
                )
        members: list[dict[str, Any]] = []
        ticker_resolution: dict[str, str] = {}
        for identifier, state in states.items():
            if state["listed"] and state["ticker"] is not None:
                prior_identifier = ticker_resolution.get(state["ticker"])
                if prior_identifier is not None and prior_identifier != identifier:
                    raise LedgerIntegrityError(
                        "Knowledge cutoff makes ticker identity ambiguous"
                    )
                ticker_resolution[state["ticker"]] = identifier
            membership = state["memberships"].get(resolved_universe)
            if state["listed"] and membership is not None:
                listing = state["listing_event"]
                members.append(
                    {
                        "security_id": identifier,
                        "ticker": state["ticker"],
                        "issuer_name": state["issuer_name"],
                        "exchange_mic": state["exchange_mic"],
                        "listing_effective_at": listing["effective_at"],
                        "listing_event_id": listing["event_id"],
                        "membership_event_id": membership["event_id"],
                        "membership_event_record_hash": membership["record_hash"],
                    }
                )
        members.sort(key=lambda item: item["security_id"])
        exclusions.sort(
            key=lambda item: (
                item["exit_effective_at"],
                item["security_id"],
                item["exit_event_id"],
            )
        )
        identity = [
            resolved_universe,
            effective_cutoff.isoformat(),
            knowledge_cutoff.isoformat(),
            [record["event_id"] for record in selected],
            POLICY_VERSION,
        ]
        return {
            "snapshot_id": "SMSNAP-"
            + hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()[:32].upper(),
            "record_type": "POINT_IN_TIME_SECURITY_MASTER_SNAPSHOT",
            "status": "RESEARCH_ONLY_PARTIAL_COVERAGE",
            "universe": resolved_universe,
            "effective_as_of": effective_cutoff.isoformat(),
            "known_as_of": knowledge_cutoff.isoformat(),
            "supporting_event_ids": [record["event_id"] for record in selected],
            "supporting_event_hashes": [record["record_hash"] for record in selected],
            "members": members,
            "exclusions_retained": exclusions,
            "ticker_resolution": dict(sorted(ticker_resolution.items())),
            "member_count": len(members),
            "excluded_count": len(exclusions),
            "permanent_identity_used": True,
            "ticker_reuse_resolved": True,
            "current_membership_used": False,
            **{name: False for name in FIXED_FALSE},
        }

    def resolve_ticker(
        self,
        *,
        ticker: str,
        effective_as_of: str | datetime,
        known_as_of: str | datetime,
    ) -> str | None:
        symbol = _ticker(ticker)
        effective_cutoff = _timestamp(effective_as_of, "effective_as_of")
        knowledge_cutoff = _timestamp(known_as_of, "known_as_of")
        if knowledge_cutoff > effective_cutoff:
            raise ValueError("knowledge cutoff cannot follow the effective decision cutoff")
        states: dict[str, dict[str, Any]] = {}
        for record in self.verify():
            if (
                _timestamp(record["effective_at"], "effective_at") <= effective_cutoff
                and _timestamp(record["available_at"], "available_at") <= knowledge_cutoff
            ):
                state = states.setdefault(record["security_id"], _new_state())
                _apply_event(state, record)
        matches = sorted(
            identifier
            for identifier, state in states.items()
            if state["listed"] and state["ticker"] == symbol
        )
        if len(matches) > 1:
            raise LedgerIntegrityError("Ticker resolves to multiple permanent securities")
        return matches[0] if matches else None

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (record for record in records if record["event_id"] == result["event_id"]),
                None,
            )
            if existing is not None:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {
                    key: value for key, value in result.items() if key not in ignored
                }:
                    return existing
                raise LedgerIntegrityError("PIT security-master event already exists")
            security_records = [
                record
                for record in records
                if record["security_id"] == result["security_id"]
            ]
            if security_records and _timestamp(
                result["effective_at"], "effective_at"
            ) < _timestamp(security_records[-1]["effective_at"], "effective_at"):
                raise LedgerIntegrityError(
                    "Security-master effective_at cannot move backwards per security"
                )
            material = {
                **result,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            by_security: dict[str, list[dict[str, Any]]] = {}
            for item in [*records, record]:
                by_security.setdefault(item["security_id"], []).append(item)
            self._validate_transitions_and_ticker_intervals(by_security)
            target = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                _write_all(target, (_canonical_json(record) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            return record
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
