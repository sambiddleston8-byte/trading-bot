from __future__ import annotations

"""Bounded proof that historical membership and delisting outcomes are complete."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.portfolio.delisting_outcome import DelistingOutcomeLedger
from core.portfolio.historical_universe import (
    HistoricalUniverseEventLedger,
    SUPPORTED_UNIVERSES,
    TICKER_PATTERN,
)


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "bounded-historical-universe-coverage-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete historical-universe coverage append")
        written += count


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


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


def _normalise_members(values: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    members: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in values:
        ticker = _required(item.get("ticker"), "starting member ticker").upper()
        if not TICKER_PATTERN.fullmatch(ticker) or ticker in seen:
            raise ValueError("starting members must have unique valid tickers")
        members.append({
            "ticker": ticker,
            "issuer_name": _required(item.get("issuer_name"), "starting member issuer_name"),
        })
        seen.add(ticker)
    if not members:
        raise ValueError("starting_members cannot be empty")
    return sorted(members, key=lambda item: item["ticker"])


def _normalise_refs(
    values: Sequence[Mapping[str, Any]], *, id_field: str, hash_field: str
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in values:
        identifier = _required(item.get(id_field), id_field)
        if identifier in seen:
            raise ValueError(f"{id_field} references must be unique")
        refs.append({id_field: identifier, hash_field: _sha256(item.get(hash_field), hash_field)})
        seen.add(identifier)
    return sorted(refs, key=lambda item: item[id_field])


def _coverage_id(
    universe: str, covers_from: str, through: str, source_hash: str, locator: str
) -> str:
    material = [universe, covers_from, through, source_hash, locator, POLICY_VERSION]
    return "UCOV-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class HistoricalUniverseCoverageLedger:
    """Append-only, source-attested completeness proof for one exact interval."""

    def __init__(
        self,
        path: str | Path,
        event_ledger: HistoricalUniverseEventLedger,
        outcome_ledger: DelistingOutcomeLedger,
    ) -> None:
        self.path = Path(path)
        self.event_ledger = event_ledger
        self.outcome_ledger = outcome_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Universe-coverage ledger has an incomplete final line.")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank universe-coverage line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at universe-coverage line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Universe-coverage line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def certify(
        self,
        *,
        universe: str,
        covers_from_at: str | datetime,
        through_at: str | datetime,
        starting_members: Sequence[Mapping[str, Any]],
        event_references: Sequence[Mapping[str, Any]],
        delisting_outcome_references: Sequence[Mapping[str, Any]],
        source_declares_complete_membership_and_changes: bool,
        source_methodology: str,
        source_version: str,
        publicly_available_at: str | datetime,
        retrieved_at: str | datetime,
        source_uri: str,
        source_input_sha256: str,
        source_locator: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        resolved_universe = _required(universe, "universe").upper()
        if resolved_universe not in SUPPORTED_UNIVERSES:
            raise ValueError("universe must be SP500 or NASDAQ100")
        start = _timestamp(covers_from_at)
        end = _timestamp(through_at)
        public = _timestamp(publicly_available_at)
        retrieved = _timestamp(retrieved_at)
        recorded = _timestamp(recorded_at or datetime.now(timezone.utc))
        if end <= start:
            raise ValueError("through_at must be later than covers_from_at")
        if not source_declares_complete_membership_and_changes:
            raise ValueError("source must explicitly attest complete membership and changes")
        if public > retrieved or retrieved > recorded:
            raise ValueError("public availability, retrieval and recording must be chronological")
        if end > retrieved:
            raise ValueError("coverage cannot extend beyond evidence retrieval")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        members = _normalise_members(starting_members)
        event_refs = _normalise_refs(
            event_references, id_field="event_id", hash_field="event_record_hash"
        )
        outcome_refs = _normalise_refs(
            delisting_outcome_references,
            id_field="outcome_id",
            hash_field="outcome_record_hash",
        )
        events = [
            item for item in self.event_ledger.verify()
            if item["universe"] == resolved_universe
            and start < _timestamp(item["effective_boundary_at"]) <= end
            and _timestamp(item["publicly_available_at"]) <= retrieved
        ]
        events.sort(key=lambda item: item["event_id"])
        actual_event_refs = [
            {"event_id": item["event_id"], "event_record_hash": item["record_hash"]}
            for item in events
        ]
        if event_refs != actual_event_refs:
            raise ValueError("event references do not exactly match the bounded verified events")
        delistings = [item for item in events if item["event_type"] == "DELISTED"]
        outcomes = [
            item for item in self.outcome_ledger.verify()
            if any(item["delisting_event_id"] == event["event_id"] for event in delistings)
            and _timestamp(item["publicly_available_at"]) <= retrieved
        ]
        outcomes.sort(key=lambda item: item["outcome_id"])
        actual_outcome_refs = [
            {"outcome_id": item["outcome_id"], "outcome_record_hash": item["record_hash"]}
            for item in outcomes
        ]
        if outcome_refs != actual_outcome_refs or len(outcomes) != len(delistings):
            raise ValueError("every bounded delisting must have exactly one verified terminal outcome")
        ending_members = self._ending_members(members, events)
        source_hash = _sha256(source_input_sha256, "source_input_sha256")
        locator = _required(source_locator, "source_locator")
        candidate_id = _coverage_id(
            resolved_universe, start.isoformat(), end.isoformat(), source_hash, locator
        )
        verified_records = self.verify()
        existing = next(
            (item for item in verified_records if item["coverage_id"] == candidate_id), None
        )
        prior = next(
            (item for item in reversed(verified_records) if item["universe"] == resolved_universe),
            None,
        )
        if prior is not None and existing is None:
            if start.isoformat() != prior["through_at"]:
                raise ValueError("successive coverage intervals must be exactly contiguous")
            if members != prior["ending_members"]:
                raise ValueError("starting members must equal the prior certificate ending members")
        result = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "coverage_id": candidate_id,
            "record_type": "BOUNDED_HISTORICAL_UNIVERSE_COVERAGE_CERTIFICATE",
            "status": "BOUNDED_MEMBERSHIP_AND_OUTCOME_COVERAGE_VERIFIED",
            "recorded_at": recorded.isoformat(),
            "universe": resolved_universe,
            "covers_from_at": start.isoformat(),
            "through_at": end.isoformat(),
            "starting_members": members,
            "starting_member_count": len(members),
            "ending_members": ending_members,
            "ending_member_count": len(ending_members),
            "event_references": event_refs,
            "event_count": len(event_refs),
            "delisting_outcome_references": outcome_refs,
            "delisting_count": len(delistings),
            "source_declares_complete_membership_and_changes": True,
            "source_methodology": _required(source_methodology, "source_methodology"),
            "source_version": _required(source_version, "source_version"),
            "publicly_available_at": public.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "source_uri": _https_uri(source_uri, "source_uri"),
            "source_input_sha256": source_hash,
            "source_locator": locator,
            "previous_universe_coverage_id": (
                existing["previous_universe_coverage_id"] if existing
                else prior["coverage_id"] if prior else None
            ),
            "previous_universe_coverage_record_hash": (
                existing["previous_universe_coverage_record_hash"] if existing
                else prior["record_hash"] if prior else None
            ),
            "source_coverage_attested": True,
            "ledger_reconciliation_complete": True,
            "coverage_completeness_proven": False,
            "terminal_outcome_evidence_complete": True,
            "survivorship_safe_replay_inputs_ready_for_bounded_interval": False,
            "global_historical_coverage_claimed": False,
            "performance_calculated": False,
            "recommendation_provided": False,
            "broker_submission_enabled": False,
            "live_trading_enabled": False,
        }
        return self._append(result, allow_existing=allow_existing)

    @staticmethod
    def _ending_members(
        starting_members: Sequence[Mapping[str, str]], events: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, str]]:
        active = {item["ticker"]: dict(item) for item in starting_members}
        ordered = sorted(events, key=lambda item: (item["effective_boundary_at"], item["event_id"]))
        for event in ordered:
            ticker = event["ticker"]
            if event["event_type"] == "ADDED":
                if ticker in active:
                    raise ValueError("bounded event sequence adds an already-active member")
                active[ticker] = {
                    "ticker": ticker,
                    "issuer_name": _required(event.get("issuer_name"), "event issuer_name"),
                }
            else:
                if ticker not in active:
                    raise ValueError("bounded event sequence removes a non-member")
                del active[ticker]
        return [active[ticker] for ticker in sorted(active)]

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen: set[str] = set()
        records = self.records()
        prior_by_universe: dict[str, dict[str, Any]] = {}
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Universe-coverage record {index} has been modified.")
            try:
                expected = self._revalidate_record(record)
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Universe-coverage record {index} violates its boundary."
                ) from error
            fixed_true = (
                "source_declares_complete_membership_and_changes",
                "source_coverage_attested",
                "ledger_reconciliation_complete",
                "terminal_outcome_evidence_complete",
            )
            fixed_false = (
                "coverage_completeness_proven",
                "survivorship_safe_replay_inputs_ready_for_bounded_interval",
                "global_historical_coverage_claimed", "performance_calculated",
                "recommendation_provided", "broker_submission_enabled", "live_trading_enabled",
            )
            universe = record.get("universe")
            prior = prior_by_universe.get(universe)
            continuity = (
                (
                    prior is None
                    and record.get("previous_universe_coverage_id") is None
                    and record.get("previous_universe_coverage_record_hash") is None
                )
                or (
                    prior is not None
                    and record.get("covers_from_at") == prior.get("through_at")
                    and record.get("starting_members") == prior.get("ending_members")
                    and record.get("previous_universe_coverage_id") == prior.get("coverage_id")
                    and record.get("previous_universe_coverage_record_hash")
                    == prior.get("record_hash")
                )
            )
            boundary = (
                record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("record_type")
                == "BOUNDED_HISTORICAL_UNIVERSE_COVERAGE_CERTIFICATE"
                and record.get("status") == "BOUNDED_MEMBERSHIP_AND_OUTCOME_COVERAGE_VERIFIED"
                and record.get("coverage_id") == expected
                and expected not in seen
                and all(record.get(field) is True for field in fixed_true)
                and all(record.get(field) is False for field in fixed_false)
                and continuity
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Universe-coverage record {index} violates its boundary."
                )
            seen.add(expected)
            prior_by_universe[universe] = record
            previous_hash = record["record_hash"]
        return records

    def _revalidate_record(self, record: Mapping[str, Any]) -> str:
        universe = _required(record.get("universe"), "universe").upper()
        start = _timestamp(record.get("covers_from_at"))
        end = _timestamp(record.get("through_at"))
        retrieved = _timestamp(record.get("retrieved_at"))
        recorded = _timestamp(record.get("recorded_at"))
        public = _timestamp(record.get("publicly_available_at"))
        members = _normalise_members(record.get("starting_members") or [])
        ending_members = _normalise_members(record.get("ending_members") or [])
        event_refs = _normalise_refs(
            record.get("event_references") or [], id_field="event_id",
            hash_field="event_record_hash",
        )
        outcome_refs = _normalise_refs(
            record.get("delisting_outcome_references") or [], id_field="outcome_id",
            hash_field="outcome_record_hash",
        )
        source_hash = _sha256(record.get("source_input_sha256"), "source hash")
        locator = _required(record.get("source_locator"), "source_locator")
        _https_uri(record.get("source_uri"), "source_uri")
        _required(record.get("source_methodology"), "source_methodology")
        _required(record.get("source_version"), "source_version")
        if universe not in SUPPORTED_UNIVERSES or not start < end <= retrieved:
            raise ValueError("invalid coverage interval")
        if not public <= retrieved <= recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("invalid evidence chronology")
        events_by_id = {item["event_id"]: item for item in self.event_ledger.verify()}
        events = []
        for ref in event_refs:
            event = events_by_id[ref["event_id"]]
            if event["record_hash"] != ref["event_record_hash"]:
                raise ValueError("event hash mismatch")
            events.append(event)
        bounded = [
            item for item in events_by_id.values()
            if item["universe"] == universe
            and start < _timestamp(item["effective_boundary_at"]) <= end
            and _timestamp(item["publicly_available_at"]) <= retrieved
        ]
        if sorted(item["event_id"] for item in events) != sorted(item["event_id"] for item in bounded):
            raise ValueError("event coverage mismatch")
        outcomes_by_id = {item["outcome_id"]: item for item in self.outcome_ledger.verify()}
        outcomes = []
        for ref in outcome_refs:
            outcome = outcomes_by_id[ref["outcome_id"]]
            if outcome["record_hash"] != ref["outcome_record_hash"]:
                raise ValueError("outcome hash mismatch")
            outcomes.append(outcome)
        delistings = [item for item in events if item["event_type"] == "DELISTED"]
        if sorted(item["delisting_event_id"] for item in outcomes) != sorted(
            item["event_id"] for item in delistings
        ) or any(_timestamp(item["publicly_available_at"]) > retrieved for item in outcomes):
            raise ValueError("terminal outcome coverage mismatch")
        if record.get("starting_member_count") != len(members):
            raise ValueError("starting member count mismatch")
        if ending_members != self._ending_members(members, events):
            raise ValueError("ending members mismatch")
        if record.get("ending_member_count") != len(ending_members):
            raise ValueError("ending member count mismatch")
        if record.get("event_count") != len(events):
            raise ValueError("event count mismatch")
        if record.get("delisting_count") != len(delistings):
            raise ValueError("delisting count mismatch")
        return _coverage_id(universe, start.isoformat(), end.isoformat(), source_hash, locator)

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["coverage_id"] == result["coverage_id"]), None
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in result.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError("Historical-universe coverage already exists.")
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
