"""Provider-neutral PIT daily-bar snapshots for deterministic TRAIN research.

This module deliberately accepts caller-supplied synthetic fixtures only.  It
does not contact a provider, qualify a source, admit a dataset, open VALIDATION
or TEST, or authorize performance or promotion claims.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Iterator, Mapping, Sequence
from urllib.parse import urlsplit

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.guardrailed_backtest import MarketBar
from core.orchestration.pit_session_partitions import PITSessionPartitionLedger
from core.portfolio.pit_security_master import (
    PointInTimeSecurityMasterLedger,
    _apply_event,
    _new_state,
)


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "pit-daily-bar-snapshots-v1"
MAX_ROWS = 100_000
# A production-shaped 90,720-row JSON snapshot is projected at about 45 MiB.
# Bound each record independently while retaining room for several immutable
# correction generations in the append-only ledger.
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_LEDGER_BYTES = 512 * 1024 * 1024
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BAR_SUPERSESSION_REASONS = frozenset(
    {"SOURCE_CORRECTION", "COVERAGE_RECAPTURE", "CALENDAR_CORRECTION"}
)
_RAW_BAR_FIELDS = frozenset(
    {
        "security_id",
        "ticker",
        "session_date",
        "open_at",
        "close_at",
        "effective_at",
        "reported_at",
        "available_at",
        "retrieved_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "source_payload_sha256",
        "source_row_locator",
    }
)
_NORMALIZED_BAR_FIELDS = _RAW_BAR_FIELDS | {"session_id", "recorded_at"}
_FALSE_AUTHORITIES = (
    "coverage_completeness_proven",
    "qualified",
    "train_admitted",
    "validation_admitted",
    "test_admitted",
    "engine_input_ready",
    "performance_claim_allowed",
    "promotion_allowed",
)
_SNAPSHOT_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "record_type",
        "status",
        "bar_snapshot_id",
        "calendar_snapshot_id",
        "calendar_snapshot_record_hash",
        "partition_manifest_id",
        "partition_manifest_record_hash",
        "partition_role",
        "security_master_tip_record_hash",
        "security_master_event_count",
        "coverage_start",
        "coverage_end",
        "session_count",
        "security_ids",
        "row_count",
        "bars",
        "source_uri",
        "source_locator",
        "source_payload_sha256",
        "supersedes_bar_snapshot_id",
        "supersession_reason",
        "point_in_time_contract",
        "permanent_identity_used",
        "cross_sectionally_aligned",
        "coverage_shape",
        "synthetic_fixture",
        *_FALSE_AUTHORITIES,
        "appended_at",
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


def _text(value: Any, name: str, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    resolved = value.strip()
    if not resolved or resolved != value or len(resolved) > maximum:
        raise ValueError(f"{name} must be nonempty canonical text")
    if any(ord(character) < 32 for character in resolved):
        raise ValueError(f"{name} must not contain control characters")
    return resolved


def _sha256(value: Any, name: str) -> str:
    resolved = _text(value, name, 64).lower()
    if SHA256_PATTERN.fullmatch(resolved) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _timestamp(value: Any, name: str) -> datetime:
    try:
        resolved = datetime.fromisoformat(canonical_timestamp(value))
        if resolved.tzinfo is None or resolved.utcoffset() is None:
            raise ValueError(f"{name} must be a timezone-aware timestamp")
        return resolved.astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _date(value: Any, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a canonical ISO date")
    try:
        resolved = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a canonical ISO date") from error
    if resolved.isoformat() != value:
        raise ValueError(f"{name} must be a canonical ISO date")
    return resolved


def _decimal(
    value: Any,
    name: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> str:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite decimal")
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite():
        raise ValueError(f"{name} must be a finite decimal")
    if positive and resolved <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and resolved < 0:
        raise ValueError(f"{name} must be nonnegative")
    normalized = format(resolved, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return "0" if normalized in {"", "-0"} else normalized


def _source_uri(value: Any) -> str:
    resolved = _text(value, "source_uri", 1000)
    parsed = urlsplit(resolved)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("source_uri must be a credential-free HTTPS URL")
    return resolved


def _no_follow() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("PIT daily-bar ledger append made no progress")
        offset += count


def _regular_file(descriptor: int, *, name: str, maximum_bytes: int | None = None) -> os.stat_result:
    details = os.fstat(descriptor)
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_nlink != 1
        or (maximum_bytes is not None and details.st_size > maximum_bytes)
    ):
        raise LedgerIntegrityError(f"{name} is unsafe")
    return details


@contextmanager
def _exclusive_locks(paths: Sequence[Path]) -> Iterator[None]:
    descriptors: list[int] = []
    try:
        for path in sorted(set(paths), key=lambda item: str(item.resolve())):
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | _no_follow(), 0o600)
            try:
                _regular_file(descriptor, name="PIT dependency lock")
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except BaseException:
                os.close(descriptor)
                raise
            descriptors.append(descriptor)
        yield
    finally:
        for descriptor in reversed(descriptors):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


@dataclass(frozen=True)
class PITDailyBarResearchInputs:
    bar_snapshot_id: str
    bar_snapshot_record_hash: str
    calendar_snapshot_id: str
    partition_manifest_id: str
    partition_role: str
    bars: tuple[MarketBar, ...]
    security_ids: tuple[str, ...]
    synthetic_fixture: bool = True
    dataset_admitted: bool = False
    performance_claim_allowed: bool = False
    promotion_allowed: bool = False

    def __post_init__(self) -> None:
        _text(self.bar_snapshot_id, "bar_snapshot_id", 80)
        _sha256(self.bar_snapshot_record_hash, "bar_snapshot_record_hash")
        _text(self.calendar_snapshot_id, "calendar_snapshot_id", 80)
        _text(self.partition_manifest_id, "partition_manifest_id", 80)
        if (
            self.partition_role != "TRAIN"
            or not self.bars
            or len(self.security_ids) != len(self.bars)
            or any(not isinstance(bar, MarketBar) for bar in self.bars)
        ):
            raise ValueError("daily-bar research inputs must contain TRAIN bars")
        for security_id in self.security_ids:
            _text(security_id, "security_id", 80)
        if self.synthetic_fixture is not True or any(
            getattr(self, name) is not False
            for name in (
                "dataset_admitted",
                "performance_claim_allowed",
                "promotion_allowed",
            )
        ):
            raise ValueError("daily-bar research inputs cannot assert authority")


@dataclass(frozen=True)
class PITDailyBarReconciliation:
    bar_snapshot_id: str
    bar_snapshot_record_hash: str
    status: str
    superseded_by_bar_snapshot_id: str | None = None
    reason_code: str | None = None
    synthetic_fixture: bool = True
    dataset_admitted: bool = False
    performance_claim_allowed: bool = False
    promotion_allowed: bool = False

    def __post_init__(self) -> None:
        _text(self.bar_snapshot_id, "bar_snapshot_id", 80)
        _sha256(self.bar_snapshot_record_hash, "bar_snapshot_record_hash")
        if self.status not in {"CURRENT", "SUPERSEDED"}:
            raise ValueError("daily-bar reconciliation status is unsupported")
        if self.status == "SUPERSEDED":
            _text(
                self.superseded_by_bar_snapshot_id,
                "superseded_by_bar_snapshot_id",
                80,
            )
            if self.reason_code not in BAR_SUPERSESSION_REASONS:
                raise ValueError("superseded daily-bar snapshot requires an allowed reason")
        elif self.superseded_by_bar_snapshot_id is not None or self.reason_code is not None:
            raise ValueError("current daily-bar snapshot cannot assert supersession")
        if self.synthetic_fixture is not True or any(
            getattr(self, name) is not False
            for name in (
                "dataset_admitted",
                "performance_claim_allowed",
                "promotion_allowed",
            )
        ):
            raise ValueError("daily-bar reconciliation cannot assert authority")


def _master_event_index(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[tuple[datetime, datetime, Mapping[str, Any]], ...]]:
    indexed: dict[str, list[tuple[datetime, datetime, Mapping[str, Any]]]] = {}
    for record in records:
        indexed.setdefault(record["security_id"], []).append(
            (
                _timestamp(record["effective_at"], "security effective_at"),
                _timestamp(record["available_at"], "security available_at"),
                record,
            )
        )
    return {security_id: tuple(events) for security_id, events in indexed.items()}


def _master_state_at(
    index: Mapping[
        str,
        Sequence[tuple[datetime, datetime, Mapping[str, Any]]],
    ],
    security_id: str,
    cutoff: datetime,
    cache: dict[tuple[str, datetime], Mapping[str, Any]],
) -> Mapping[str, Any]:
    key = (security_id, cutoff)
    if key in cache:
        return cache[key]
    state = _new_state()
    for effective_at, available_at, record in index.get(security_id, ()):
        if effective_at <= cutoff and available_at <= cutoff:
            _apply_event(state, record)
    cache[key] = state
    return state


def _bar_snapshot_id(body: Mapping[str, Any]) -> str:
    material: dict[str, Any] = {
        key: value
        for key, value in body.items()
        if key
        not in {
            "bar_snapshot_id",
            "appended_at",
            "previous_hash",
            "record_hash",
        }
    }
    material["bars"] = [
        {key: value for key, value in item.items() if key != "recorded_at"}
        for item in body["bars"]
    ]
    return "PBAR-" + _record_hash(material)[:32].upper()


def _logical_snapshot(record: Mapping[str, Any]) -> dict[str, Any]:
    material = {
        key: value
        for key, value in record.items()
        if key not in {"appended_at", "previous_hash", "record_hash"}
    }
    material["bars"] = [
        {key: value for key, value in item.items() if key != "recorded_at"}
        for item in record["bars"]
    ]
    return material


class PITDailyBarLedger:
    """Append and consume immutable synthetic TRAIN daily-bar snapshots."""

    def __init__(
        self,
        path: str | Path,
        *,
        calendar_ledger: PITSessionPartitionLedger,
        security_master_ledger: PointInTimeSecurityMasterLedger,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self.calendar_ledger = calendar_ledger
        self.security_master_ledger = security_master_ledger
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        descriptor = os.open(self.path, os.O_RDONLY | _no_follow())
        try:
            details = _regular_file(
                descriptor,
                name="PIT daily-bar ledger",
                maximum_bytes=MAX_LEDGER_BYTES,
            )
            raw = b""
            while len(raw) < details.st_size:
                chunk = os.read(descriptor, min(1024 * 1024, details.st_size - len(raw)))
                if not chunk:
                    raise LedgerIntegrityError("PIT daily-bar ledger read ended early")
                raw += chunk
        finally:
            os.close(descriptor)
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("PIT daily-bar ledger has an incomplete final line")
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            try:
                value = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise LedgerIntegrityError(
                    f"invalid PIT daily-bar JSON at line {line_number}"
                ) from error
            if not isinstance(value, dict):
                raise LedgerIntegrityError("PIT daily-bar record is not an object")
            records.append(value)
        return records

    def append_snapshot(
        self,
        *,
        calendar_snapshot_id: str,
        partition_manifest_id: str,
        bars: Sequence[Mapping[str, Any]],
        source_uri: str,
        source_locator: str,
        source_payload_sha256: str,
        synthetic_fixture: bool,
        supersedes_bar_snapshot_id: str | None = None,
        supersession_reason: str | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        if synthetic_fixture is not True:
            raise ValueError("this research-only path accepts deterministic synthetic fixtures only")
        if isinstance(bars, (str, bytes)) or not isinstance(bars, Sequence):
            raise ValueError("bars must be a bounded sequence")
        if not 1 <= len(bars) <= MAX_ROWS:
            raise ValueError(f"bars must contain between 1 and {MAX_ROWS} records")
        supersedes = (
            None
            if supersedes_bar_snapshot_id is None
            else _text(supersedes_bar_snapshot_id, "supersedes_bar_snapshot_id", 80)
        )
        reason = (
            None
            if supersession_reason is None
            else _text(supersession_reason, "supersession_reason", 40).upper()
        )
        if (supersedes is None) != (reason is None):
            raise ValueError("daily-bar supersession id and reason must be provided together")
        if reason is not None and reason not in BAR_SUPERSESSION_REASONS:
            raise ValueError("daily-bar supersession_reason is unsupported")
        lock_paths = (
            _lock_path(self.calendar_ledger.path),
            _lock_path(self.security_master_ledger.path),
            _lock_path(self.path),
        )
        with _exclusive_locks(lock_paths):
            appended = _timestamp(self._clock(), "append clock")
            if appended > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
                raise ValueError("append clock cannot be materially in the future")
            calendar_records, calendar, manifest = self._calendar_dependencies(
                calendar_snapshot_id,
                partition_manifest_id,
                require_current=True,
            )
            master_records = self.security_master_ledger.verify()
            if not master_records:
                raise ValueError("a verified PIT security master is required")
            normalized = self._normalize_bars(
                bars,
                appended_at=appended,
                calendar=calendar,
                manifest=manifest,
                master_records=master_records,
            )
            security_ids = sorted({item["security_id"] for item in normalized})
            body: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "policy_version": POLICY_VERSION,
                "record_type": "PIT_DAILY_BAR_SNAPSHOT",
                "status": "SYNTHETIC_TRAIN_BARS_NOT_QUALIFIED",
                "bar_snapshot_id": "",
                "calendar_snapshot_id": calendar["calendar_snapshot_id"],
                "calendar_snapshot_record_hash": calendar["record_hash"],
                "partition_manifest_id": manifest["partition_manifest_id"],
                "partition_manifest_record_hash": manifest["record_hash"],
                "partition_role": "TRAIN",
                "security_master_tip_record_hash": master_records[-1]["record_hash"],
                "security_master_event_count": len(master_records),
                "coverage_start": normalized[0]["session_date"],
                "coverage_end": normalized[-1]["session_date"],
                "session_count": len({item["session_date"] for item in normalized}),
                "security_ids": security_ids,
                "row_count": len(normalized),
                "bars": normalized,
                "source_uri": _source_uri(source_uri),
                "source_locator": _text(source_locator, "source_locator", 1000),
                "source_payload_sha256": _sha256(
                    source_payload_sha256,
                    "source_payload_sha256",
                ),
                "supersedes_bar_snapshot_id": supersedes,
                "supersession_reason": reason,
                "point_in_time_contract": (
                    "effective_at/reported_at/available_at/retrieved_at/recorded_at"
                ),
                "permanent_identity_used": True,
                "cross_sectionally_aligned": True,
                "coverage_shape": "STRICT_RECTANGLE_CONSTANT_MEMBERSHIP",
                "synthetic_fixture": True,
                **{name: False for name in _FALSE_AUTHORITIES},
                "appended_at": appended.isoformat(timespec="microseconds"),
            }
            body["bar_snapshot_id"] = _bar_snapshot_id(body)
            records = self.verify()
            existing = next(
                (
                    item
                    for item in records
                    if item["bar_snapshot_id"] == body["bar_snapshot_id"]
                ),
                None,
            )
            if existing is not None:
                if allow_existing and _logical_snapshot(existing) == _logical_snapshot(body):
                    return existing
                raise LedgerIntegrityError("PIT daily-bar snapshot already exists")
            self._validate_supersession(body, records, calendar_records)
            material = {
                **body,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            payload = (_canonical_json(record) + "\n").encode("utf-8")
            if len(payload) > MAX_SNAPSHOT_BYTES:
                raise LedgerIntegrityError("PIT daily-bar snapshot exceeds its size limit")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | _no_follow(),
                0o600,
            )
            try:
                details = _regular_file(descriptor, name="PIT daily-bar ledger")
                if details.st_size + len(payload) > MAX_LEDGER_BYTES:
                    raise LedgerIntegrityError("PIT daily-bar ledger exceeds its size limit")
                _write_all(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return record

    def materialize_research_inputs(self, bar_snapshot_id: str) -> PITDailyBarResearchInputs:
        records = self.verify()
        snapshot = next(
            (item for item in records if item["bar_snapshot_id"] == bar_snapshot_id),
            None,
        )
        if snapshot is None:
            raise ValueError("bar_snapshot_id is not present in the verified ledger")
        if any(
            item["supersedes_bar_snapshot_id"] == bar_snapshot_id for item in records
        ):
            raise ValueError("superseded PIT daily-bar snapshot cannot materialize")
        self._calendar_dependencies(
            snapshot["calendar_snapshot_id"],
            snapshot["partition_manifest_id"],
            require_current=True,
        )
        bars = tuple(
            MarketBar(
                symbol=item["ticker"],
                open_at=_timestamp(item["open_at"], "open_at"),
                close_at=_timestamp(item["close_at"], "close_at"),
                available_at=_timestamp(item["available_at"], "available_at"),
                open=Decimal(item["open"]),
                high=Decimal(item["high"]),
                low=Decimal(item["low"]),
                close=Decimal(item["close"]),
                volume=Decimal(item["volume"]),
            )
            for item in snapshot["bars"]
        )
        return PITDailyBarResearchInputs(
            bar_snapshot_id=snapshot["bar_snapshot_id"],
            bar_snapshot_record_hash=snapshot["record_hash"],
            calendar_snapshot_id=snapshot["calendar_snapshot_id"],
            partition_manifest_id=snapshot["partition_manifest_id"],
            partition_role="TRAIN",
            bars=bars,
            security_ids=tuple(item["security_id"] for item in snapshot["bars"]),
        )

    def reconcile_snapshot(self, bar_snapshot_id: str) -> PITDailyBarReconciliation:
        records = self.verify()
        snapshot = next(
            (item for item in records if item["bar_snapshot_id"] == bar_snapshot_id),
            None,
        )
        if snapshot is None:
            raise ValueError("bar_snapshot_id is not present in the verified ledger")
        child = next(
            (
                item
                for item in records
                if item["supersedes_bar_snapshot_id"] == bar_snapshot_id
            ),
            None,
        )
        return PITDailyBarReconciliation(
            bar_snapshot_id=snapshot["bar_snapshot_id"],
            bar_snapshot_record_hash=snapshot["record_hash"],
            status="SUPERSEDED" if child is not None else "CURRENT",
            superseded_by_bar_snapshot_id=(
                child["bar_snapshot_id"] if child is not None else None
            ),
            reason_code=child["supersession_reason"] if child is not None else None,
        )

    def verify(self) -> list[dict[str, Any]]:
        records = self.records()
        calendar_records = self.calendar_ledger.verify()
        master_records = self.security_master_ledger.verify()
        previous = GENESIS_HASH
        seen: set[str] = set()
        previous_appended: datetime | None = None
        for index, record in enumerate(records, start=1):
            try:
                material = {
                    key: value for key, value in record.items() if key != "record_hash"
                }
                appended = _timestamp(record["appended_at"], "appended_at")
                common = (
                    set(record) == _SNAPSHOT_FIELDS
                    and record["schema_version"] == SCHEMA_VERSION
                    and record["policy_version"] == POLICY_VERSION
                    and record["record_type"] == "PIT_DAILY_BAR_SNAPSHOT"
                    and record["status"] == "SYNTHETIC_TRAIN_BARS_NOT_QUALIFIED"
                    and record["previous_hash"] == previous
                    and record["record_hash"] == _record_hash(material)
                    and record["bar_snapshot_id"] == _bar_snapshot_id(record)
                    and record["bar_snapshot_id"] not in seen
                    and (previous_appended is None or appended >= previous_appended)
                    and appended <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                    and record["partition_role"] == "TRAIN"
                    and record["point_in_time_contract"]
                    == "effective_at/reported_at/available_at/retrieved_at/recorded_at"
                    and record["permanent_identity_used"] is True
                    and record["cross_sectionally_aligned"] is True
                    and record["coverage_shape"]
                    == "STRICT_RECTANGLE_CONSTANT_MEMBERSHIP"
                    and record["synthetic_fixture"] is True
                    and all(record[name] is False for name in _FALSE_AUTHORITIES)
                )
                if not common:
                    raise ValueError("common daily-bar snapshot boundary failed")
                calendar, manifest = self._historical_calendar_dependencies(
                    record,
                    calendar_records,
                )
                prefix = self._master_prefix(record, master_records)
                normalized = self._normalize_bars(
                    [
                        {key: item[key] for key in _RAW_BAR_FIELDS}
                        for item in record["bars"]
                    ],
                    appended_at=appended,
                    calendar=calendar,
                    manifest=manifest,
                    master_records=prefix,
                )
                identity = (
                    normalized == record["bars"]
                    and record["coverage_start"] == normalized[0]["session_date"]
                    and record["coverage_end"] == normalized[-1]["session_date"]
                    and record["session_count"]
                    == len({item["session_date"] for item in normalized})
                    and record["security_ids"]
                    == sorted({item["security_id"] for item in normalized})
                    and record["row_count"] == len(normalized)
                    and record["security_master_event_count"] == len(prefix)
                )
                if not identity:
                    raise ValueError("daily-bar snapshot identity changed")
                _source_uri(record["source_uri"])
                _text(record["source_locator"], "source_locator", 1000)
                _sha256(record["source_payload_sha256"], "source_payload_sha256")
                self._validate_supersession(record, records[: index - 1], calendar_records)
            except (IndexError, KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"PIT daily-bar snapshot {index} is invalid"
                ) from error
            seen.add(record["bar_snapshot_id"])
            previous = record["record_hash"]
            previous_appended = appended
        return records

    def _calendar_dependencies(
        self,
        calendar_snapshot_id: str,
        partition_manifest_id: str,
        *,
        require_current: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        records = self.calendar_ledger.verify()
        calendar = next(
            (
                item
                for item in records
                if item.get("record_type") == "PIT_SESSION_CALENDAR_SNAPSHOT"
                and item["calendar_snapshot_id"] == calendar_snapshot_id
            ),
            None,
        )
        manifest = next(
            (
                item
                for item in records
                if item.get("record_type") == "PIT_CHRONOLOGICAL_PARTITION_MANIFEST"
                and item["partition_manifest_id"] == partition_manifest_id
            ),
            None,
        )
        if calendar is None or manifest is None:
            raise ValueError("verified calendar snapshot and partition manifest are required")
        if manifest["calendar_snapshot_id"] != calendar["calendar_snapshot_id"]:
            raise ValueError("partition manifest does not pin the requested calendar")
        if require_current and any(
            item.get("record_type") == "PIT_SESSION_CALENDAR_SNAPSHOT"
            and item["supersedes_calendar_snapshot_id"] == calendar["calendar_snapshot_id"]
            for item in records
        ):
            raise ValueError("superseded PIT calendar cannot serve daily bars")
        return records, calendar, manifest

    @staticmethod
    def _historical_calendar_dependencies(
        record: Mapping[str, Any],
        calendar_records: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        calendar = next(
            (
                item
                for item in calendar_records
                if item.get("record_type") == "PIT_SESSION_CALENDAR_SNAPSHOT"
                and item["calendar_snapshot_id"] == record["calendar_snapshot_id"]
            ),
            None,
        )
        manifest = next(
            (
                item
                for item in calendar_records
                if item.get("record_type") == "PIT_CHRONOLOGICAL_PARTITION_MANIFEST"
                and item["partition_manifest_id"] == record["partition_manifest_id"]
            ),
            None,
        )
        if (
            calendar is None
            or manifest is None
            or calendar["record_hash"] != record["calendar_snapshot_record_hash"]
            or manifest["record_hash"] != record["partition_manifest_record_hash"]
            or manifest["calendar_snapshot_id"] != calendar["calendar_snapshot_id"]
        ):
            raise ValueError("daily-bar calendar or partition pin is invalid")
        return dict(calendar), dict(manifest)

    @staticmethod
    def _master_prefix(
        record: Mapping[str, Any],
        master_records: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        tip = record["security_master_tip_record_hash"]
        for index, item in enumerate(master_records):
            if item["record_hash"] == tip:
                prefix = [dict(value) for value in master_records[: index + 1]]
                if len(prefix) != record["security_master_event_count"]:
                    raise ValueError("daily-bar security-master prefix length changed")
                return prefix
        raise ValueError("daily-bar security-master tip is unavailable")

    def _normalize_bars(
        self,
        values: Sequence[Mapping[str, Any]],
        *,
        appended_at: datetime,
        calendar: Mapping[str, Any],
        manifest: Mapping[str, Any],
        master_records: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        sessions = {item["session_date"]: item for item in calendar["sessions"]}
        master_index = _master_event_index(master_records)
        master_state_cache: dict[tuple[str, datetime], Mapping[str, Any]] = {}
        normalized = [
            self._normalize_bar(
                value,
                appended_at=appended_at,
                sessions=sessions,
                manifest=manifest,
                master_index=master_index,
                master_state_cache=master_state_cache,
            )
            for value in values
        ]
        normalized.sort(key=lambda item: (item["session_date"], item["security_id"]))
        keys = [(item["session_date"], item["security_id"]) for item in normalized]
        if len(keys) != len(set(keys)):
            raise ValueError("daily bars contain a duplicate security/session row")
        dates = sorted({item["session_date"] for item in normalized})
        expected_dates = [
            item["session_date"]
            for item in calendar["sessions"]
            if dates[0] <= item["session_date"] <= dates[-1]
        ]
        if dates != expected_dates:
            raise ValueError("daily-bar coverage has a missing calendar session")
        securities = sorted({item["security_id"] for item in normalized})
        if set(keys) != {(day, security_id) for day in dates for security_id in securities}:
            raise ValueError("daily bars are not cross-sectionally aligned")
        identity_tickers = {
            (item["security_id"], item["ticker"]) for item in normalized
        }
        tickers = {item["ticker"] for item in normalized}
        if len(identity_tickers) != len(securities) or len(tickers) != len(securities):
            raise ValueError(
                "daily-bar snapshot ticker/permanent-identity mapping is not bijective"
            )
        return normalized

    @staticmethod
    def _normalize_bar(
        value: Mapping[str, Any],
        *,
        appended_at: datetime,
        sessions: Mapping[str, Mapping[str, Any]],
        manifest: Mapping[str, Any],
        master_index: Mapping[
            str,
            Sequence[tuple[datetime, datetime, Mapping[str, Any]]],
        ],
        master_state_cache: dict[tuple[str, datetime], Mapping[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != _RAW_BAR_FIELDS:
            raise ValueError("each daily bar must contain exactly the PIT daily-bar fields")
        security_id = _text(value["security_id"], "security_id", 80)
        ticker = _text(value["ticker"], "ticker", 20).upper()
        if ticker != value["ticker"]:
            raise ValueError("ticker must be canonical uppercase text")
        session_day = _date(value["session_date"], "session_date").isoformat()
        session = sessions.get(session_day)
        if session is None:
            raise ValueError("daily bar is not an observed XNYS session")
        if PITDailyBarLedger._partition_role(manifest, session_day) != "TRAIN":
            raise ValueError("daily-bar snapshots may contain TRAIN sessions only")
        open_at = _timestamp(value["open_at"], "open_at")
        close_at = _timestamp(value["close_at"], "close_at")
        effective = _timestamp(value["effective_at"], "effective_at")
        reported = _timestamp(value["reported_at"], "reported_at")
        available = _timestamp(value["available_at"], "available_at")
        retrieved = _timestamp(value["retrieved_at"], "retrieved_at")
        if (
            open_at != _timestamp(session["open_at"], "session open_at")
            or close_at != _timestamp(session["close_at"], "session close_at")
            or effective != close_at
            or reported != close_at
            or not effective <= reported <= available <= retrieved <= appended_at
        ):
            raise ValueError("daily-bar timestamps violate the calendar or PIT order")
        state = _master_state_at(
            master_index,
            security_id,
            close_at,
            master_state_cache,
        )
        if state.get("listed") is not True or state.get("ticker") != ticker:
            raise ValueError("daily bar ticker does not match its permanent security identity")
        open_value = _decimal(value["open"], "open", positive=True)
        high_value = _decimal(value["high"], "high", positive=True)
        low_value = _decimal(value["low"], "low", positive=True)
        close_value = _decimal(value["close"], "close", positive=True)
        volume = _decimal(value["volume"], "volume", nonnegative=True)
        if (
            Decimal(high_value) < max(Decimal(open_value), Decimal(close_value))
            or Decimal(low_value) > min(Decimal(open_value), Decimal(close_value))
            or Decimal(high_value) < Decimal(low_value)
        ):
            raise ValueError("daily-bar OHLC values are inconsistent")
        return {
            "security_id": security_id,
            "ticker": ticker,
            "session_id": session["session_id"],
            "session_date": session_day,
            "open_at": open_at.isoformat(timespec="microseconds"),
            "close_at": close_at.isoformat(timespec="microseconds"),
            "effective_at": effective.isoformat(timespec="microseconds"),
            "reported_at": reported.isoformat(timespec="microseconds"),
            "available_at": available.isoformat(timespec="microseconds"),
            "retrieved_at": retrieved.isoformat(timespec="microseconds"),
            "recorded_at": appended_at.isoformat(timespec="microseconds"),
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close_value,
            "volume": volume,
            "source_payload_sha256": _sha256(
                value["source_payload_sha256"],
                "bar source_payload_sha256",
            ),
            "source_row_locator": _text(
                value["source_row_locator"],
                "source_row_locator",
                1000,
            ),
        }

    @staticmethod
    def _partition_role(manifest: Mapping[str, Any], session_day: str) -> str:
        session_id = f"XNYS:{session_day}"
        for seam in manifest["seams"]:
            if session_id in seam["dead_zone_session_ids"]:
                return "DEAD_ZONE"
        for partition in manifest["partitions"]:
            if partition["start_session_id"] <= session_id <= partition["end_session_id"]:
                return partition["role"]
        return "OUTSIDE"

    @staticmethod
    def _validate_supersession(
        body: Mapping[str, Any],
        prior: Sequence[Mapping[str, Any]],
        calendar_records: Sequence[Mapping[str, Any]],
    ) -> None:
        new_start = _date(body["coverage_start"], "coverage_start")
        new_end = _date(body["coverage_end"], "coverage_end")
        new_ids = set(body["security_ids"])
        overlapping = [
            item
            for item in prior
            if new_start <= _date(item["coverage_end"], "coverage_end")
            and _date(item["coverage_start"], "coverage_start") <= new_end
            and new_ids.intersection(item["security_ids"])
        ]
        target_id = body["supersedes_bar_snapshot_id"]
        reason = body["supersession_reason"]
        if target_id is None:
            if reason is not None:
                raise ValueError("daily-bar supersession id and reason must be provided together")
            if overlapping:
                raise LedgerIntegrityError("overlapping PIT daily-bar coverage is ambiguous")
            return
        if reason not in BAR_SUPERSESSION_REASONS:
            raise ValueError("daily-bar supersession_reason is unsupported")
        target = next(
            (item for item in prior if item["bar_snapshot_id"] == target_id),
            None,
        )
        if target is None:
            raise LedgerIntegrityError("daily-bar supersession target does not exist")
        if any(item["supersedes_bar_snapshot_id"] == target_id for item in prior):
            raise LedgerIntegrityError("daily-bar supersession chain cannot fork")
        if body["security_ids"] != target["security_ids"]:
            raise LedgerIntegrityError("daily-bar supersession must preserve permanent identities")
        if reason == "CALENDAR_CORRECTION":
            if not PITDailyBarLedger._calendar_descends_from(
                body["calendar_snapshot_id"],
                target["calendar_snapshot_id"],
                calendar_records,
            ):
                raise LedgerIntegrityError(
                    "calendar-corrected bars must pin a descendant calendar"
                )
            if target not in overlapping:
                raise LedgerIntegrityError(
                    "calendar-corrected daily bars must overlap their target"
                )
        elif any(
            body[field] != target[field]
            for field in (
                "calendar_snapshot_id",
                "partition_manifest_id",
                "coverage_start",
                "coverage_end",
            )
        ):
            raise LedgerIntegrityError(
                "daily-bar source correction must preserve calendar and coverage"
            )
        ancestry: set[str] = set()
        cursor: Mapping[str, Any] | None = target
        while cursor is not None:
            cursor_id = cursor["bar_snapshot_id"]
            if cursor_id in ancestry:
                raise LedgerIntegrityError("daily-bar supersession chain contains a cycle")
            ancestry.add(cursor_id)
            parent_id = cursor["supersedes_bar_snapshot_id"]
            cursor = (
                None
                if parent_id is None
                else next(
                    (item for item in prior if item["bar_snapshot_id"] == parent_id),
                    None,
                )
            )
            if parent_id is not None and cursor is None:
                raise LedgerIntegrityError("daily-bar supersession ancestry is incomplete")
        if any(item["bar_snapshot_id"] not in ancestry for item in overlapping):
            raise LedgerIntegrityError(
                "daily-bar supersession overlaps evidence outside its replacement chain"
            )

    @staticmethod
    def _calendar_descends_from(
        child_id: str,
        ancestor_id: str,
        records: Sequence[Mapping[str, Any]],
    ) -> bool:
        calendars = {
            item["calendar_snapshot_id"]: item
            for item in records
            if item.get("record_type") == "PIT_SESSION_CALENDAR_SNAPSHOT"
        }
        cursor = calendars.get(child_id)
        seen: set[str] = set()
        while cursor is not None:
            cursor_id = cursor["calendar_snapshot_id"]
            if cursor_id in seen:
                return False
            seen.add(cursor_id)
            parent_id = cursor["supersedes_calendar_snapshot_id"]
            if parent_id == ancestor_id:
                return True
            cursor = calendars.get(parent_id) if parent_id is not None else None
        return False
