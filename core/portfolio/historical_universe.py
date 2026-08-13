from __future__ import annotations

"""Point-in-time constituent events and survivorship-safe universe snapshots."""

from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp


HISTORICAL_UNIVERSE_SCHEMA_VERSION = "1.0"
HISTORICAL_UNIVERSE_POLICY_VERSION = "point-in-time-membership-events-v2"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
SUPPORTED_UNIVERSES = {"SP500", "NASDAQ100"}
SUPPORTED_EVENTS = {"ADDED", "REMOVED", "DELISTED"}
SUPPORTED_TERMINAL_TREATMENTS = {
    "NOT_APPLICABLE",
    "ACQUISITION_CASH_OR_STOCK_CONSIDERATION_REQUIRED",
    "BANKRUPTCY_OR_LIQUIDATION_OUTCOME_REQUIRED",
    "LAST_TRADABLE_TOTAL_RETURN_REQUIRED",
}
POLICY = {
    "selection_policy": "EVENT_EFFECTIVE_AND_PUBLICLY_AVAILABLE_BY_SNAPSHOT_CUTOFF",
    "survivorship_policy": "REMOVALS_AND_DELISTINGS_RETAINED_NOT_DROPPED",
    "delisting_policy": "DELISTED_SECURITY_REQUIRES_EXPLICIT_TERMINAL_OUTCOME_TREATMENT",
    "snapshot_policy": "RECONSTRUCT_FROM_VERIFIED_EVENTS_NO_CURRENT_MEMBERSHIP_BACKFILL",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete historical-universe event append")
        written += count


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _date(value: str | date) -> date:
    if isinstance(value, datetime):
        raise ValueError("effective_date must be a date, not a datetime")
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError("effective_date must be YYYY-MM-DD") from error


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").lower()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _https_uri(value: Any, name: str) -> str:
    resolved = _required(value, name)
    parsed = urlsplit(resolved)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{name} must be an HTTPS URL with a hostname")
    if parsed.username or parsed.password:
        raise ValueError(f"{name} cannot contain credentials")
    return resolved


def _event_id(
    universe: str,
    ticker: str,
    event_type: str,
    effective_date: str,
    source_hash: str,
    locator: str,
) -> str:
    return "UEVT-" + hashlib.sha256(
        _canonical_json(
            [universe, ticker, event_type, effective_date, source_hash, locator,
             HISTORICAL_UNIVERSE_POLICY_VERSION]
        ).encode("utf-8")
    ).hexdigest()[:32].upper()


class HistoricalUniverseEventLedger:
    """Append-only membership history; no current list can substitute for it."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Historical-universe ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank universe-event line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at universe-event line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Universe-event line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def record_event(
        self,
        *,
        universe: str,
        ticker: str,
        issuer_name: str,
        event_type: str,
        effective_date: str | date,
        publicly_available_at: str | datetime,
        retrieved_at: str | datetime,
        source_uri: str,
        source_input_sha256: str,
        source_locator: str,
        recorded_at: str | datetime | None = None,
        terminal_outcome_treatment: str = "NOT_APPLICABLE",
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        resolved_universe = _required(universe, "universe").upper()
        if resolved_universe not in SUPPORTED_UNIVERSES:
            raise ValueError("universe must be SP500 or NASDAQ100")
        symbol = _required(ticker, "ticker").upper()
        if not TICKER_PATTERN.fullmatch(symbol):
            raise ValueError("ticker is invalid")
        event = _required(event_type, "event_type").upper()
        if event not in SUPPORTED_EVENTS:
            raise ValueError("event_type must be ADDED, REMOVED or DELISTED")
        effective = _date(effective_date)
        public = _timestamp(publicly_available_at)
        retrieved = _timestamp(retrieved_at)
        recorded = _timestamp(recorded_at or datetime.now(timezone.utc))
        effective_boundary = datetime.combine(effective, datetime.min.time(), timezone.utc)
        if public > retrieved or retrieved > recorded:
            raise ValueError("public availability, retrieval and recording must be chronological")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        treatment = _required(terminal_outcome_treatment, "terminal_outcome_treatment").upper()
        if treatment not in SUPPORTED_TERMINAL_TREATMENTS:
            raise ValueError("terminal_outcome_treatment is unsupported")
        if event == "DELISTED" and treatment == "NOT_APPLICABLE":
            raise ValueError("DELISTED requires an explicit terminal outcome treatment")
        if event != "DELISTED" and treatment != "NOT_APPLICABLE":
            raise ValueError("terminal outcome treatment is only valid for DELISTED")
        source_hash = _sha256(source_input_sha256, "source_input_sha256")
        locator = _required(source_locator, "source_locator")
        result = {
            "schema_version": HISTORICAL_UNIVERSE_SCHEMA_VERSION,
            "policy_version": HISTORICAL_UNIVERSE_POLICY_VERSION,
            "event_id": _event_id(
                resolved_universe, symbol, event, effective.isoformat(), source_hash, locator
            ),
            "record_type": "POINT_IN_TIME_HISTORICAL_UNIVERSE_EVENT",
            "status": "RECORDED",
            "recorded_at": recorded.isoformat(),
            "universe": resolved_universe,
            "ticker": symbol,
            "issuer_name": _required(issuer_name, "issuer_name"),
            "event_type": event,
            "effective_date": effective.isoformat(),
            "effective_boundary_at": effective_boundary.isoformat(),
            "publicly_available_at": public.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "availability": (
                "KNOWN_BEFORE_EFFECTIVE_DATE"
                if public <= effective_boundary
                else "BACKFILLED_AFTER_EFFECTIVE_DATE"
            ),
            "source_uri": _https_uri(source_uri, "source_uri"),
            "source_input_sha256": source_hash,
            "source_locator": locator,
            "terminal_outcome_treatment": treatment,
            "terminal_outcome_evidence_required": event == "DELISTED",
            "performance_claim_allowed": False,
            "survivorship_safe_replay_ready": False,
            "recommendation_provided": False,
            "broker_submission_enabled": False,
            "live_trading_enabled": False,
            **POLICY,
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        states: dict[tuple[str, str], bool] = {}
        last_effective_boundaries: dict[tuple[str, str], datetime] = {}
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Universe-event record {index} has been modified.")
            try:
                universe = _required(record.get("universe"), "universe").upper()
                ticker = _required(record.get("ticker"), "ticker").upper()
                event = _required(record.get("event_type"), "event_type").upper()
                effective = _date(record.get("effective_date"))
                public = _timestamp(record.get("publicly_available_at"))
                retrieved = _timestamp(record.get("retrieved_at"))
                recorded = _timestamp(record.get("recorded_at"))
                source_hash = _sha256(record.get("source_input_sha256"), "source hash")
                locator = _required(record.get("source_locator"), "source_locator")
                treatment = _required(
                    record.get("terminal_outcome_treatment"), "terminal treatment"
                ).upper()
                _https_uri(record.get("source_uri"), "source_uri")
                _required(record.get("issuer_name"), "issuer_name")
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Universe-event record {index} is invalid.") from error
            if universe not in SUPPORTED_UNIVERSES or event not in SUPPORTED_EVENTS:
                raise LedgerIntegrityError(f"Universe-event record {index} has unsupported values.")
            effective_boundary = datetime.combine(effective, datetime.min.time(), timezone.utc)
            expected_id = _event_id(
                universe, ticker, event, effective.isoformat(), source_hash, locator
            )
            key = (universe, ticker)
            before = states.get(key, False)
            prior_effective_boundary = last_effective_boundaries.get(key)
            chronology_valid = (
                prior_effective_boundary is None
                or effective_boundary >= prior_effective_boundary
            )
            transition_valid = (event == "ADDED" and not before) or (
                event in {"REMOVED", "DELISTED"} and before
            )
            after = event == "ADDED"
            boundary = (
                record.get("schema_version") == HISTORICAL_UNIVERSE_SCHEMA_VERSION
                and record.get("policy_version") == HISTORICAL_UNIVERSE_POLICY_VERSION
                and record.get("event_id") == expected_id
                and expected_id not in seen_ids
                and record.get("record_type") == "POINT_IN_TIME_HISTORICAL_UNIVERSE_EVENT"
                and record.get("status") == "RECORDED"
                and TICKER_PATTERN.fullmatch(ticker) is not None
                and record.get("effective_boundary_at") == effective_boundary.isoformat()
                and public <= retrieved <= recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("availability") == (
                    "KNOWN_BEFORE_EFFECTIVE_DATE"
                    if public <= effective_boundary else "BACKFILLED_AFTER_EFFECTIVE_DATE"
                )
                and treatment in SUPPORTED_TERMINAL_TREATMENTS
                and (event == "DELISTED") is (treatment != "NOT_APPLICABLE")
                and record.get("terminal_outcome_evidence_required") is (event == "DELISTED")
                and all(record.get(field) is False for field in (
                    "performance_claim_allowed", "survivorship_safe_replay_ready",
                    "recommendation_provided", "broker_submission_enabled", "live_trading_enabled",
                ))
                and all(record.get(name) == value for name, value in POLICY.items())
                and transition_valid
                and chronology_valid
            )
            if not boundary:
                raise LedgerIntegrityError(f"Universe-event record {index} violates its boundary.")
            states[key] = after
            last_effective_boundaries[key] = effective_boundary
            seen_ids.add(expected_id)
            previous_hash = record["record_hash"]
        return records

    def snapshot(
        self,
        *,
        universe: str,
        effective_as_of: str | datetime,
        known_as_of: str | datetime,
    ) -> dict[str, Any]:
        resolved_universe = _required(universe, "universe").upper()
        if resolved_universe not in SUPPORTED_UNIVERSES:
            raise ValueError("universe must be SP500 or NASDAQ100")
        effective_cutoff = _timestamp(effective_as_of)
        knowledge_cutoff = _timestamp(known_as_of)
        events = [
            item
            for item in self.verify()
            if item["universe"] == resolved_universe
            and _timestamp(item["effective_boundary_at"]) <= effective_cutoff
            and _timestamp(item["publicly_available_at"]) <= knowledge_cutoff
        ]
        # ``verify`` requires each ticker's effective dates to be non-decreasing in
        # append order. Python's stable sort therefore preserves unambiguous order
        # for same-boundary transitions while interleaving different tickers.
        events.sort(key=lambda item: item["effective_boundary_at"])
        members: dict[str, dict[str, Any]] = {}
        exclusions: list[dict[str, Any]] = []
        for event in events:
            if event["event_type"] == "ADDED":
                if event["ticker"] in members:
                    raise LedgerIntegrityError(
                        "Historical-universe snapshot contains an impossible duplicate addition."
                    )
                members[event["ticker"]] = {
                    "ticker": event["ticker"],
                    "issuer_name": event["issuer_name"],
                    "added_event_id": event["event_id"],
                    "added_event_record_hash": event["record_hash"],
                }
            else:
                member = members.pop(event["ticker"], None)
                if member is None:
                    raise LedgerIntegrityError(
                        "Historical-universe snapshot contains an exit without a known member."
                    )
                exclusions.append({
                    **member,
                    "exit_event_id": event["event_id"],
                    "exit_event_record_hash": event["record_hash"],
                    "exit_type": event["event_type"],
                    "terminal_outcome_treatment": event["terminal_outcome_treatment"],
                })
        included = [members[key] for key in sorted(members)]
        exclusions.sort(key=lambda item: (item["ticker"], item["exit_event_id"]))
        identity = [
            resolved_universe, effective_cutoff.isoformat(), knowledge_cutoff.isoformat(),
            [item["event_id"] for item in events], HISTORICAL_UNIVERSE_POLICY_VERSION,
        ]
        return {
            "snapshot_id": "USNAP-" + hashlib.sha256(
                _canonical_json(identity).encode("utf-8")
            ).hexdigest()[:32].upper(),
            "status": "PARTIAL_POINT_IN_TIME_MEMBERSHIP_RECONSTRUCTION",
            "universe": resolved_universe,
            "effective_as_of": effective_cutoff.isoformat(),
            "known_as_of": knowledge_cutoff.isoformat(),
            "supporting_event_ids": [item["event_id"] for item in events],
            "supporting_event_hashes": [item["record_hash"] for item in events],
            "members": included,
            "exclusions_retained": exclusions,
            "member_count": len(included),
            "excluded_count": len(exclusions),
            "coverage_completeness_proven": False,
            "terminal_outcome_evidence_complete": False,
            "survivorship_safe_replay_ready": False,
            "current_membership_used": False,
            "performance_calculated": False,
            "recommendation_provided": False,
            "broker_submission_enabled": False,
            "live_trading_enabled": False,
        }

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next((item for item in records if item["event_id"] == result["event_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                if allow_existing and {k: v for k, v in existing.items() if k not in ignored} == {
                    k: v for k, v in result.items() if k not in ignored
                }:
                    return existing
                raise LedgerIntegrityError("Historical-universe event already exists.")
            key = (result["universe"], result["ticker"])
            active = False
            last_effective_boundary: datetime | None = None
            for item in records:
                if (item["universe"], item["ticker"]) == key:
                    active = item["event_type"] == "ADDED"
                    last_effective_boundary = _timestamp(item["effective_boundary_at"])
            result_effective_boundary = _timestamp(result["effective_boundary_at"])
            if (
                last_effective_boundary is not None
                and result_effective_boundary < last_effective_boundary
            ):
                raise LedgerIntegrityError(
                    "Universe event effective date cannot move backwards in append order."
                )
            if (result["event_type"] == "ADDED") is active:
                raise LedgerIntegrityError("Universe event is not a valid membership transition.")
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
