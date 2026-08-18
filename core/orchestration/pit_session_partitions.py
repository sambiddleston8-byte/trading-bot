"""Immutable research mechanics for PIT XNYS sessions and partition seams.

This module deliberately does not qualify a calendar or admit a dataset.  It
records deterministic synthetic mechanics so a later, separately authorised
source/admission workflow can supply authoritative bytes without changing the
partition policy.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp


SCHEMA_VERSION = "1.1"
POLICY_VERSION = "pit-xnys-session-partitions-v2"
EXCHANGE = "XNYS"
EXCHANGE_TIMEZONE = "America/New_York"
DECISION_PERIOD_UNIT = "XNYS_DAILY_SESSION"
MAX_SESSIONS = 5000
MAX_LEDGER_BYTES = 32 * 1024 * 1024
MAX_CLOCK_SKEW = timedelta(minutes=5)
_NY = ZoneInfo(EXCHANGE_TIMEZONE)
CALENDAR_SUPERSESSION_REASONS = frozenset(
    {"SOURCE_CORRECTION", "COVERAGE_RECAPTURE", "COVERAGE_BOUNDARY_CORRECTION"}
)
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
_SESSION_FIELDS = {
    "session_date",
    "session_type",
    "open_at",
    "close_at",
    "effective_at",
    "reported_at",
    "available_at",
    "retrieved_at",
    "recorded_at",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    try:
        bytes.fromhex(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest") from error
    if value != value.lower():
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _text(value: Any, name: str, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or len(value) > maximum:
        raise ValueError(f"{name} must be canonical nonempty text")
    return value


def _date(value: Any, name: str) -> date:
    try:
        resolved = date.fromisoformat(_text(value, name, 10))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO date") from error
    if resolved.isoformat() != value:
        raise ValueError(f"{name} must be a canonical ISO date")
    return resolved


def _timestamp(value: Any, name: str) -> datetime:
    try:
        resolved = datetime.fromisoformat(canonical_timestamp(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error
    return resolved


def _positive_integer(value: Any, name: str, maximum: int = MAX_SESSIONS) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer between 1 and {maximum}")
    return value


def _source_uri(value: Any) -> str:
    resolved = _text(value, "source_uri", 1000)
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


def _session_id(session_day: str) -> str:
    return f"{EXCHANGE}:{session_day}"


def _normalize_session(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _SESSION_FIELDS:
        raise ValueError("each session must contain exactly the five-timestamp session fields")
    session_day = _date(value.get("session_date"), "session_date")
    session_type = _text(value.get("session_type"), "session_type", 20)
    if session_type not in {"REGULAR", "EARLY_CLOSE"}:
        raise ValueError("session_type must be REGULAR or EARLY_CLOSE")
    open_at = _timestamp(value.get("open_at"), "open_at")
    close_at = _timestamp(value.get("close_at"), "close_at")
    expected_open = datetime.combine(session_day, time(9, 30), _NY).astimezone(timezone.utc)
    expected_close_time = time(16) if session_type == "REGULAR" else time(13)
    expected_close = datetime.combine(session_day, expected_close_time, _NY).astimezone(timezone.utc)
    if open_at != expected_open or close_at != expected_close:
        raise ValueError("session open/close must match its XNYS local schedule and type")
    effective = _timestamp(value.get("effective_at"), "effective_at")
    reported = _timestamp(value.get("reported_at"), "reported_at")
    available = _timestamp(value.get("available_at"), "available_at")
    retrieved = _timestamp(value.get("retrieved_at"), "retrieved_at")
    recorded = _timestamp(value.get("recorded_at"), "recorded_at")
    if effective != open_at:
        raise ValueError("effective_at must equal the session open")
    if not reported <= available <= retrieved <= recorded:
        raise ValueError("session PIT timestamps must satisfy reported <= available <= retrieved <= recorded")
    return {
        "session_id": _session_id(session_day.isoformat()),
        "session_date": session_day.isoformat(),
        "session_type": session_type,
        "open_at": open_at.isoformat(timespec="microseconds"),
        "close_at": close_at.isoformat(timespec="microseconds"),
        "effective_at": effective.isoformat(timespec="microseconds"),
        "reported_at": reported.isoformat(timespec="microseconds"),
        "available_at": available.isoformat(timespec="microseconds"),
        "retrieved_at": retrieved.isoformat(timespec="microseconds"),
        "recorded_at": recorded.isoformat(timespec="microseconds"),
        "available_before_session_open": available <= open_at,
    }


def _normalize_sessions(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("sessions must be a bounded sequence")
    if not 1 <= len(values) <= MAX_SESSIONS:
        raise ValueError(f"sessions must contain between 1 and {MAX_SESSIONS} records")
    normalized = sorted((_normalize_session(item) for item in values), key=lambda item: item["session_date"])
    dates = [item["session_date"] for item in normalized]
    if len(set(dates)) != len(dates):
        raise ValueError("session dates must be unique")
    if any(date.fromisoformat(item).weekday() >= 5 for item in dates):
        raise ValueError("XNYS sessions cannot be Saturday or Sunday")
    return normalized


def _calendar_identity(body: Mapping[str, Any]) -> str:
    material = {key: value for key, value in body.items() if key != "calendar_snapshot_id"}
    return "XCAL-" + _record_hash(material)[:32].upper()


def _partition_identity(body: Mapping[str, Any]) -> str:
    material = {key: value for key, value in body.items() if key != "partition_manifest_id"}
    return "PPM-" + _record_hash(material)[:32].upper()


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _period_at_least(start: date, end: date, months: int) -> bool:
    return end >= _add_months(start, months) - timedelta(days=1)


def _no_follow() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("unable to complete append-only PIT partition write")
        written += count


@dataclass(frozen=True)
class PITCalendarReconciliation:
    calendar_snapshot_id: str
    calendar_snapshot_record_hash: str
    status: str
    superseded_by_calendar_snapshot_id: str | None = None
    reason_code: str | None = None
    synthetic_fixture: bool = True
    dataset_admitted: bool = False
    performance_claim_allowed: bool = False
    promotion_allowed: bool = False

    def __post_init__(self) -> None:
        _text(self.calendar_snapshot_id, "calendar_snapshot_id", 80)
        _sha256(self.calendar_snapshot_record_hash, "calendar_snapshot_record_hash")
        if self.status not in {"CURRENT", "SUPERSEDED"}:
            raise ValueError("calendar reconciliation status is unsupported")
        if self.status == "SUPERSEDED":
            _text(
                self.superseded_by_calendar_snapshot_id,
                "superseded_by_calendar_snapshot_id",
                80,
            )
            if self.reason_code not in CALENDAR_SUPERSESSION_REASONS:
                raise ValueError("superseded calendar requires an allowed reason")
        elif self.superseded_by_calendar_snapshot_id is not None or self.reason_code is not None:
            raise ValueError("current calendar cannot assert supersession")
        if self.synthetic_fixture is not True or any(
            getattr(self, name) is not False
            for name in (
                "dataset_admitted",
                "performance_claim_allowed",
                "promotion_allowed",
            )
        ):
            raise ValueError("calendar reconciliation cannot assert admission or authority")


def _validate_calendar_supersession(
    body: Mapping[str, Any],
    prior_calendars: Sequence[Mapping[str, Any]],
) -> None:
    new_start = _date(body["coverage_start"], "coverage_start")
    new_end = _date(body["coverage_end"], "coverage_end")
    overlapping = [
        item
        for item in prior_calendars
        if new_start <= _date(item["coverage_end"], "coverage_end")
        and _date(item["coverage_start"], "coverage_start") <= new_end
    ]
    target_id = body["supersedes_calendar_snapshot_id"]
    reason = body["supersession_reason"]
    if target_id is None:
        if reason is not None:
            raise ValueError("calendar supersession id and reason must be provided together")
        if overlapping:
            raise LedgerIntegrityError("overlapping PIT calendar coverage is ambiguous")
        return
    if reason not in CALENDAR_SUPERSESSION_REASONS:
        raise ValueError("calendar supersession_reason is unsupported")
    target = next(
        (item for item in prior_calendars if item["calendar_snapshot_id"] == target_id),
        None,
    )
    if target is None:
        raise LedgerIntegrityError("calendar supersession target does not exist")
    if any(
        item["supersedes_calendar_snapshot_id"] == target_id
        for item in prior_calendars
    ):
        raise LedgerIntegrityError("calendar supersession chain cannot fork")
    identity_fields = [
        "exchange",
        "exchange_timezone",
        "decision_period_unit",
    ]
    if reason != "COVERAGE_BOUNDARY_CORRECTION":
        identity_fields.extend(("coverage_start", "coverage_end"))
    if any(body[field] != target[field] for field in identity_fields):
        raise LedgerIntegrityError(
            "calendar supersession must preserve the exact exchange and coverage interval"
        )
    if not any(item["calendar_snapshot_id"] == target_id for item in overlapping):
        raise LedgerIntegrityError(
            "calendar boundary correction must overlap its supersession target"
        )
    ancestry: set[str] = set()
    cursor: Mapping[str, Any] | None = target
    while cursor is not None:
        cursor_id = cursor["calendar_snapshot_id"]
        if cursor_id in ancestry:
            raise LedgerIntegrityError("calendar supersession chain contains a cycle")
        ancestry.add(cursor_id)
        parent_id = cursor["supersedes_calendar_snapshot_id"]
        if parent_id is None:
            cursor = None
        else:
            cursor = next(
                (
                    item
                    for item in prior_calendars
                    if item["calendar_snapshot_id"] == parent_id
                ),
                None,
            )
            if cursor is None:
                raise LedgerIntegrityError("calendar supersession ancestry is incomplete")
    if any(item["calendar_snapshot_id"] not in ancestry for item in overlapping):
        raise LedgerIntegrityError(
            "calendar supersession overlaps evidence outside its replacement chain"
        )


class PITSessionPartitionLedger:
    """Append and verify synthetic XNYS snapshots and their sealed partitions."""

    def __init__(self, path: str | Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self.path = Path(path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        descriptor = os.open(self.path, os.O_RDONLY | _no_follow())
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or stat.S_IMODE(details.st_mode) != 0o600
                or details.st_nlink != 1
                or details.st_size > MAX_LEDGER_BYTES
            ):
                raise LedgerIntegrityError("PIT session/partition ledger is unsafe")
            raw = b""
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                raw += chunk
        finally:
            os.close(descriptor)
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("PIT session/partition ledger has an incomplete final line")
        result = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            try:
                item = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise LedgerIntegrityError(f"invalid PIT session/partition JSON at line {line_number}") from error
            if not isinstance(item, dict):
                raise LedgerIntegrityError("PIT session/partition record is not an object")
            result.append(item)
        return result

    def append_calendar_snapshot(
        self,
        *,
        sessions: Sequence[Mapping[str, Any]],
        source_uri: str,
        source_locator: str,
        source_payload_sha256: str,
        synthetic_fixture: bool,
        supersedes_calendar_snapshot_id: str | None = None,
        supersession_reason: str | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        if synthetic_fixture is not True:
            raise ValueError("this research-only path accepts deterministic synthetic fixtures only")
        normalized = _normalize_sessions(sessions)
        supersedes = (
            None
            if supersedes_calendar_snapshot_id is None
            else _text(
                supersedes_calendar_snapshot_id,
                "supersedes_calendar_snapshot_id",
                80,
            )
        )
        reason = (
            None
            if supersession_reason is None
            else _text(supersession_reason, "supersession_reason", 40).upper()
        )
        if (supersedes is None) != (reason is None):
            raise ValueError("calendar supersession id and reason must be provided together")
        if reason is not None and reason not in CALENDAR_SUPERSESSION_REASONS:
            raise ValueError("calendar supersession_reason is unsupported")
        body: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "record_type": "PIT_SESSION_CALENDAR_SNAPSHOT",
            "status": "SYNTHETIC_RESEARCH_CALENDAR_NOT_QUALIFIED",
            "exchange": EXCHANGE,
            "exchange_timezone": EXCHANGE_TIMEZONE,
            "decision_period_unit": DECISION_PERIOD_UNIT,
            "coverage_start": normalized[0]["session_date"],
            "coverage_end": normalized[-1]["session_date"],
            "supersedes_calendar_snapshot_id": supersedes,
            "supersession_reason": reason,
            "session_count": len(normalized),
            "sessions": normalized,
            "source_uri": _source_uri(source_uri),
            "source_locator": _text(source_locator, "source_locator", 500),
            "source_payload_sha256": _sha256(source_payload_sha256, "source_payload_sha256"),
            "point_in_time_contract": "effective_at/reported_at/available_at/retrieved_at/recorded_at",
            "synthetic_fixture": True,
            **{name: False for name in _FALSE_AUTHORITIES},
        }
        body["calendar_snapshot_id"] = _calendar_identity(body)
        return self._append(body, identity_field="calendar_snapshot_id", allow_existing=allow_existing)

    def append_partition_manifest(
        self,
        *,
        calendar_snapshot_id: str,
        train_start: str,
        train_end: str,
        validation_start: str,
        validation_end: str,
        test_start: str,
        test_end: str,
        longest_label_horizon_decision_periods: int,
        embargo_decision_periods: int,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        records = self.verify()
        snapshot = next(
            (
                item
                for item in records
                if item["record_type"] == "PIT_SESSION_CALENDAR_SNAPSHOT"
                and item["calendar_snapshot_id"] == calendar_snapshot_id
            ),
            None,
        )
        if snapshot is None:
            raise ValueError("calendar_snapshot_id is not present in the verified ledger")
        if any(
            item.get("record_type") == "PIT_SESSION_CALENDAR_SNAPSHOT"
            and item["supersedes_calendar_snapshot_id"] == calendar_snapshot_id
            for item in records
        ):
            raise ValueError("superseded PIT calendar cannot derive a partition manifest")
        horizon = _positive_integer(
            longest_label_horizon_decision_periods,
            "longest_label_horizon_decision_periods",
        )
        embargo = _positive_integer(embargo_decision_periods, "embargo_decision_periods")
        dead_count = horizon + embargo
        sessions = snapshot["sessions"]
        by_date = {item["session_date"]: index for index, item in enumerate(sessions)}
        names = {
            "TRAIN start": train_start,
            "TRAIN end": train_end,
            "VALIDATION start": validation_start,
            "VALIDATION end": validation_end,
            "TEST start": test_start,
            "TEST end": test_end,
        }
        try:
            indexes = {name: by_date[_date(value, name).isoformat()] for name, value in names.items()}
        except KeyError as error:
            raise ValueError("every partition boundary must be an observed XNYS session") from error
        ts, te = indexes["TRAIN start"], indexes["TRAIN end"]
        vs, ve = indexes["VALIDATION start"], indexes["VALIDATION end"]
        xs, xe = indexes["TEST start"], indexes["TEST end"]
        if not (ts <= te < vs <= ve < xs <= xe):
            raise ValueError("TRAIN, VALIDATION and TEST must be chronological, nonempty and non-overlapping")
        if vs - te - 1 != dead_count or xs - ve - 1 != dead_count:
            raise ValueError("both partition seams must contain exactly H + embargo XNYS sessions")

        def partition(role: str, start_index: int, end_index: int) -> dict[str, Any]:
            return {
                "role": role,
                "start_session_id": sessions[start_index]["session_id"],
                "start_session_date": sessions[start_index]["session_date"],
                "end_session_id": sessions[end_index]["session_id"],
                "end_session_date": sessions[end_index]["session_date"],
                "decision_period_count": end_index - start_index + 1,
            }

        def seam(left: str, right: str, left_end: int, right_start: int) -> dict[str, Any]:
            dead = sessions[left_end + 1 : right_start]
            return {
                "seam": f"{left}|{right}",
                "left_end_session_id": sessions[left_end]["session_id"],
                "right_start_session_id": sessions[right_start]["session_id"],
                "dead_zone_session_ids": [item["session_id"] for item in dead],
                "dead_zone_start_session_id": dead[0]["session_id"],
                "dead_zone_end_session_id": dead[-1]["session_id"],
                "dead_zone_decision_periods": len(dead),
                "formula": "longest_label_horizon_decision_periods + embargo_decision_periods",
                "belongs_to_no_partition": True,
            }

        partitions = [
            partition("TRAIN", ts, te),
            partition("VALIDATION", vs, ve),
            partition("TEST", xs, xe),
        ]
        train_start_date = date.fromisoformat(partitions[0]["start_session_date"])
        train_end_date = date.fromisoformat(partitions[0]["end_session_date"])
        validation_start_date = date.fromisoformat(partitions[1]["start_session_date"])
        validation_end_date = date.fromisoformat(partitions[1]["end_session_date"])
        train_span = _period_at_least(train_start_date, train_end_date, 36)
        validation_span = _period_at_least(validation_start_date, validation_end_date, 6)
        validation_count = partitions[1]["decision_period_count"] >= 60
        body = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "record_type": "PIT_CHRONOLOGICAL_PARTITION_MANIFEST",
            "status": "SYNTHETIC_RESEARCH_PARTITIONS_RECORDED_NOT_ADMITTED",
            "calendar_snapshot_id": snapshot["calendar_snapshot_id"],
            "calendar_snapshot_record_hash": snapshot["record_hash"],
            "decision_period_unit": DECISION_PERIOD_UNIT,
            "longest_label_horizon_decision_periods": horizon,
            "embargo_decision_periods": embargo,
            "dead_zone_decision_periods": dead_count,
            "partitions": partitions,
            "seams": [
                seam("TRAIN", "VALIDATION", te, vs),
                seam("VALIDATION", "TEST", ve, xs),
            ],
            "partition_boundaries_immutable": True,
            "partitions_strictly_chronological": True,
            "dead_zones_belong_to_no_partition": True,
            "purge_and_embargo_share_daily_decision_period_unit": True,
            "train_span_at_least_three_years": train_span,
            "validation_span_at_least_six_months": validation_span,
            "validation_at_least_60_decision_periods": validation_count,
            "declared_drawdown_regime_evidence": False,
            "production_history_count_requirements_met": False,
            "synthetic_fixture": True,
            **{name: False for name in _FALSE_AUTHORITIES},
        }
        body["partition_manifest_id"] = _partition_identity(body)
        return self._append(body, identity_field="partition_manifest_id", allow_existing=allow_existing)

    def partition_role(self, partition_manifest_id: str, session_date: str) -> str:
        """Return structural membership without opening any partition's market data."""

        records = self.verify()
        manifest = next(
            (
                item
                for item in records
                if item["record_type"] == "PIT_CHRONOLOGICAL_PARTITION_MANIFEST"
                and item["partition_manifest_id"] == partition_manifest_id
            ),
            None,
        )
        if manifest is None:
            raise ValueError("partition_manifest_id is not present in the verified ledger")
        resolved = _date(session_date, "session_date").isoformat()
        snapshot = next(
            item
            for item in records
            if item["record_type"] == "PIT_SESSION_CALENDAR_SNAPSHOT"
            and item["calendar_snapshot_id"] == manifest["calendar_snapshot_id"]
        )
        if any(
            item.get("record_type") == "PIT_SESSION_CALENDAR_SNAPSHOT"
            and item["supersedes_calendar_snapshot_id"] == snapshot["calendar_snapshot_id"]
            for item in records
        ):
            raise ValueError("partition manifest pins a superseded PIT calendar")
        observed_ids = {item["session_id"] for item in snapshot["sessions"]}
        resolved_id = _session_id(resolved)
        if resolved_id not in observed_ids:
            return "OUTSIDE"
        for seam in manifest["seams"]:
            if resolved_id in seam["dead_zone_session_ids"]:
                return "DEAD_ZONE"
        for partition in manifest["partitions"]:
            if partition["start_session_id"] <= resolved_id <= partition["end_session_id"]:
                return partition["role"]
        return "OUTSIDE"

    def reconcile_calendar_snapshot(
        self,
        calendar_snapshot_id: str,
    ) -> PITCalendarReconciliation:
        records = self.verify()
        snapshot = next(
            (
                item
                for item in records
                if item["record_type"] == "PIT_SESSION_CALENDAR_SNAPSHOT"
                and item["calendar_snapshot_id"] == calendar_snapshot_id
            ),
            None,
        )
        if snapshot is None:
            raise ValueError("calendar_snapshot_id is not present in the verified ledger")
        child = next(
            (
                item
                for item in records
                if item["record_type"] == "PIT_SESSION_CALENDAR_SNAPSHOT"
                and item["supersedes_calendar_snapshot_id"] == calendar_snapshot_id
            ),
            None,
        )
        return PITCalendarReconciliation(
            calendar_snapshot_id=calendar_snapshot_id,
            calendar_snapshot_record_hash=snapshot["record_hash"],
            status="SUPERSEDED" if child is not None else "CURRENT",
            superseded_by_calendar_snapshot_id=(
                child["calendar_snapshot_id"] if child is not None else None
            ),
            reason_code=child["supersession_reason"] if child is not None else None,
        )

    def verify(self) -> list[dict[str, Any]]:
        records = self.records()
        previous = GENESIS_HASH
        seen: set[str] = set()
        calendars: dict[str, dict[str, Any]] = {}
        previous_appended: datetime | None = None
        for index, record in enumerate(records, start=1):
            try:
                record_type = record["record_type"]
                identity_field = (
                    "calendar_snapshot_id"
                    if record_type == "PIT_SESSION_CALENDAR_SNAPSHOT"
                    else "partition_manifest_id"
                )
                appended = _timestamp(record["appended_at"], "appended_at")
                material = {key: value for key, value in record.items() if key != "record_hash"}
                common = (
                    record["schema_version"] == SCHEMA_VERSION
                    and record["policy_version"] == POLICY_VERSION
                    and record["previous_hash"] == previous
                    and record["record_hash"] == _record_hash(material)
                    and record[identity_field] not in seen
                    and (previous_appended is None or appended >= previous_appended)
                    and appended <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                )
                if not common:
                    raise ValueError("common append-only boundary failed")
                if record_type == "PIT_SESSION_CALENDAR_SNAPSHOT":
                    self._verify_calendar(record)
                    _validate_calendar_supersession(record, list(calendars.values()))
                    calendars[record["calendar_snapshot_id"]] = record
                elif record_type == "PIT_CHRONOLOGICAL_PARTITION_MANIFEST":
                    if any(
                        item["supersedes_calendar_snapshot_id"]
                        == record["calendar_snapshot_id"]
                        for item in calendars.values()
                    ):
                        raise ValueError("superseded calendar cannot derive a partition manifest")
                    self._verify_partition(record, calendars)
                    if any(
                        item.get("record_type") == "PIT_CHRONOLOGICAL_PARTITION_MANIFEST"
                        and item["calendar_snapshot_id"] == record["calendar_snapshot_id"]
                        for item in records[: index - 1]
                    ):
                        raise ValueError("calendar already has a partition manifest")
                else:
                    raise ValueError("unsupported record_type")
            except (IndexError, KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"PIT session/partition record {index} is invalid") from error
            seen.add(record[identity_field])
            previous = record["record_hash"]
            previous_appended = appended
        return records

    @staticmethod
    def _verify_calendar(record: Mapping[str, Any]) -> None:
        expected_keys = {
            "schema_version", "policy_version", "record_type", "status",
            "calendar_snapshot_id", "exchange", "exchange_timezone",
            "decision_period_unit", "coverage_start", "coverage_end",
            "supersedes_calendar_snapshot_id", "supersession_reason", "session_count",
            "sessions", "source_uri", "source_locator", "source_payload_sha256",
            "point_in_time_contract", "synthetic_fixture", *_FALSE_AUTHORITIES,
            "appended_at", "previous_hash", "record_hash",
        }
        if set(record) != expected_keys:
            raise ValueError("calendar record has missing or unsupported fields")
        sessions = _normalize_sessions(
            [{key: item[key] for key in _SESSION_FIELDS} for item in record["sessions"]]
        )
        if any(set(actual) != set(expected) for actual, expected in zip(record["sessions"], sessions)):
            raise ValueError("calendar session has missing or unsupported normalized fields")
        body = {key: value for key, value in record.items() if key not in {"appended_at", "previous_hash", "record_hash"}}
        boundary = (
            record["status"] == "SYNTHETIC_RESEARCH_CALENDAR_NOT_QUALIFIED"
            and record["exchange"] == EXCHANGE
            and record["exchange_timezone"] == EXCHANGE_TIMEZONE
            and record["decision_period_unit"] == DECISION_PERIOD_UNIT
            and record["coverage_start"] == sessions[0]["session_date"]
            and record["coverage_end"] == sessions[-1]["session_date"]
            and record["session_count"] == len(sessions)
            and record["sessions"] == sessions
            and record["source_uri"] == _source_uri(record["source_uri"])
            and record["source_locator"] == _text(record["source_locator"], "source_locator", 500)
            and record["source_payload_sha256"] == _sha256(record["source_payload_sha256"], "source_payload_sha256")
            and record["point_in_time_contract"] == "effective_at/reported_at/available_at/retrieved_at/recorded_at"
            and all(
                _timestamp(item["recorded_at"], "session recorded_at")
                <= _timestamp(record["appended_at"], "appended_at")
                for item in sessions
            )
            and record["synthetic_fixture"] is True
            and all(record[name] is False for name in _FALSE_AUTHORITIES)
            and record["calendar_snapshot_id"] == _calendar_identity(body)
        )
        if not boundary:
            raise ValueError("calendar authority or identity boundary failed")

    @staticmethod
    def _verify_partition(record: Mapping[str, Any], calendars: Mapping[str, Mapping[str, Any]]) -> None:
        expected_keys = {
            "schema_version", "policy_version", "record_type", "status",
            "partition_manifest_id", "calendar_snapshot_id", "calendar_snapshot_record_hash",
            "decision_period_unit", "longest_label_horizon_decision_periods",
            "embargo_decision_periods", "dead_zone_decision_periods", "partitions", "seams",
            "partition_boundaries_immutable", "partitions_strictly_chronological",
            "dead_zones_belong_to_no_partition", "purge_and_embargo_share_daily_decision_period_unit",
            "train_span_at_least_three_years", "validation_span_at_least_six_months",
            "validation_at_least_60_decision_periods", "declared_drawdown_regime_evidence",
            "production_history_count_requirements_met", "synthetic_fixture", *_FALSE_AUTHORITIES,
            "appended_at", "previous_hash", "record_hash",
        }
        if set(record) != expected_keys:
            raise ValueError("partition record has missing or unsupported fields")
        calendar = calendars.get(record["calendar_snapshot_id"])
        if calendar is None or calendar["record_hash"] != record["calendar_snapshot_record_hash"]:
            raise ValueError("partition calendar pin is absent or changed")
        horizon = _positive_integer(record["longest_label_horizon_decision_periods"], "longest_label_horizon_decision_periods")
        embargo = _positive_integer(record["embargo_decision_periods"], "embargo_decision_periods")
        partitions = record["partitions"]
        seams = record["seams"]
        if not isinstance(partitions, list) or len(partitions) != 3 or [item.get("role") for item in partitions] != ["TRAIN", "VALIDATION", "TEST"]:
            raise ValueError("partition roles are invalid")
        if not isinstance(seams, list) or len(seams) != 2:
            raise ValueError("partition seams are invalid")
        by_id = {item["session_id"]: index for index, item in enumerate(calendar["sessions"])}
        indexes = []
        for item in partitions:
            if set(item) != {"role", "start_session_id", "start_session_date", "end_session_id", "end_session_date", "decision_period_count"}:
                raise ValueError("partition boundary has missing or unsupported fields")
            start = by_id[item["start_session_id"]]
            end = by_id[item["end_session_id"]]
            if item["start_session_id"] != _session_id(item["start_session_date"]) or item["end_session_id"] != _session_id(item["end_session_date"]):
                raise ValueError("partition session identity mismatch")
            if start > end or item["decision_period_count"] != end - start + 1:
                raise ValueError("partition count is invalid")
            indexes.append((start, end))
        dead_count = horizon + embargo
        expected_seams = []
        for position, (left, right) in enumerate(((indexes[0], indexes[1]), (indexes[1], indexes[2]))):
            dead = calendar["sessions"][left[1] + 1 : right[0]]
            expected_seams.append({
                "seam": "TRAIN|VALIDATION" if position == 0 else "VALIDATION|TEST",
                "left_end_session_id": calendar["sessions"][left[1]]["session_id"],
                "right_start_session_id": calendar["sessions"][right[0]]["session_id"],
                "dead_zone_session_ids": [item["session_id"] for item in dead],
                "dead_zone_start_session_id": dead[0]["session_id"],
                "dead_zone_end_session_id": dead[-1]["session_id"],
                "dead_zone_decision_periods": len(dead),
                "formula": "longest_label_horizon_decision_periods + embargo_decision_periods",
                "belongs_to_no_partition": True,
            })
        train_span = _period_at_least(date.fromisoformat(partitions[0]["start_session_date"]), date.fromisoformat(partitions[0]["end_session_date"]), 36)
        validation_span = _period_at_least(date.fromisoformat(partitions[1]["start_session_date"]), date.fromisoformat(partitions[1]["end_session_date"]), 6)
        body = {key: value for key, value in record.items() if key not in {"appended_at", "previous_hash", "record_hash"}}
        boundary = (
            record["status"] == "SYNTHETIC_RESEARCH_PARTITIONS_RECORDED_NOT_ADMITTED"
            and record["decision_period_unit"] == DECISION_PERIOD_UNIT
            and record["dead_zone_decision_periods"] == dead_count
            and all(right[0] - left[1] - 1 == dead_count for left, right in ((indexes[0], indexes[1]), (indexes[1], indexes[2])))
            and record["seams"] == expected_seams
            and record["partition_boundaries_immutable"] is True
            and record["partitions_strictly_chronological"] is True
            and record["dead_zones_belong_to_no_partition"] is True
            and record["purge_and_embargo_share_daily_decision_period_unit"] is True
            and record["train_span_at_least_three_years"] is train_span
            and record["validation_span_at_least_six_months"] is validation_span
            and record["validation_at_least_60_decision_periods"] is (partitions[1]["decision_period_count"] >= 60)
            and record["declared_drawdown_regime_evidence"] is False
            and record["production_history_count_requirements_met"] is False
            and record["synthetic_fixture"] is True
            and all(record[name] is False for name in _FALSE_AUTHORITIES)
            and record["partition_manifest_id"] == _partition_identity(body)
        )
        if not boundary:
            raise ValueError("partition seam, authority or identity boundary failed")

    def _append(self, body: dict[str, Any], *, identity_field: str, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_descriptor = os.open(self.path.parent, os.O_RDONLY)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next((item for item in records if item.get(identity_field) == body[identity_field]), None)
            if existing is not None:
                if allow_existing:
                    return existing
                raise ValueError(f"{identity_field} is already recorded")
            if body["record_type"] == "PIT_SESSION_CALENDAR_SNAPSHOT":
                _validate_calendar_supersession(
                    body,
                    [
                        item
                        for item in records
                        if item["record_type"] == "PIT_SESSION_CALENDAR_SNAPSHOT"
                    ],
                )
            else:
                if any(
                    item["record_type"] == "PIT_SESSION_CALENDAR_SNAPSHOT"
                    and item["supersedes_calendar_snapshot_id"]
                    == body["calendar_snapshot_id"]
                    for item in records
                ):
                    raise LedgerIntegrityError(
                        "superseded PIT calendar cannot derive a partition manifest"
                    )
                if any(
                    item["record_type"] == "PIT_CHRONOLOGICAL_PARTITION_MANIFEST"
                    and item["calendar_snapshot_id"] == body["calendar_snapshot_id"]
                    for item in records
                ):
                    raise LedgerIntegrityError("calendar already has a partition manifest")
            appended = _timestamp(self._clock(), "append clock")
            if appended > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
                raise ValueError("append clock cannot be materially in the future")
            if records and appended < _timestamp(records[-1]["appended_at"], "prior appended_at"):
                raise ValueError("append clock cannot precede the prior immutable record")
            if body["record_type"] == "PIT_SESSION_CALENDAR_SNAPSHOT" and any(
                _timestamp(item["recorded_at"], "session recorded_at") > appended
                for item in body["sessions"]
            ):
                raise ValueError("session recorded_at cannot follow the immutable append time")
            material = {
                **body,
                "appended_at": appended.isoformat(timespec="microseconds"),
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            payload = (_canonical_json(record) + "\n").encode("utf-8")
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | _no_follow(), 0o600)
            try:
                details = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(details.st_mode)
                    or stat.S_IMODE(details.st_mode) != 0o600
                    or details.st_nlink != 1
                    or details.st_size + len(payload) > MAX_LEDGER_BYTES
                ):
                    raise LedgerIntegrityError("PIT session/partition ledger target is unsafe")
                _write_all(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return self.verify()[-1]
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
