"""Fail-closed assembly of synthetic PIT TRAIN source ledgers.

This module proves that the independently verified security-master, calendar,
partition, daily-bar, corporate-action, and authenticated-byte ledgers can be
bound and materialized as one coherent replay input.  It deliberately does not
prove source coverage, qualify data, admit a partition, or authorize
performance, candidate freeze, promotion, brokerage, or live trading.

All dependency writers use the same ``<ledger>.lock`` sidecar convention.
Bundle append and materialization acquire those locks in resolved-path order;
the dependency verification and materialization methods called while locked
are read-only and do not reacquire them.

Source ``recorded_at`` values preserve the upstream operator assertion; they
are ordering evidence, not an independent truth or completeness guarantee.
Schema/policy v1 validation must remain available if a future version is
introduced so immutable v1 history is never reinterpreted under new rules.

As with the dependency ledgers, the hash chain detects mutation, insertion and
reordering but cannot by itself detect rollback to an older valid tip.  Any
future admission path must checkpoint the selected bundle ID and record hash in
its separately controlled admission evidence.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable, Iterator, Mapping, Sequence

from core.data_quality.authenticated_source_content import (
    AuthenticatedSourceContentLedger,
)
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.guardrailed_backtest import CorporateAction, MarketBar, TerminalOutcome, UniverseEvent
from core.orchestration.pit_corporate_actions import PITCorporateActionLedger
from core.orchestration.pit_daily_bars import PITDailyBarLedger
from core.orchestration.pit_session_partitions import PITSessionPartitionLedger
from core.portfolio.pit_security_master import PointInTimeSecurityMasterLedger


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "pit-train-source-bundle-v1"
STATUS = "SYNTHETIC_CROSS_LEDGER_BUNDLE_COMPLETE_NOT_SOURCE_COVERAGE_PROOF"
# Bundle rows are synthetic dataset-generation manifests, not per-run records.
# The cap bounds audit reads; any future production capture must define a
# segmented persistence/rotation policy before it can use this boundary.
MAX_LEDGER_BYTES = 128 * 1024 * 1024
MAX_CLOCK_SKEW = timedelta(minutes=5)
SOURCE_ROLES = frozenset(
    {
        "SECURITY_MASTER_EVENT",
        "XNYS_CALENDAR_SNAPSHOT",
        "DAILY_BAR_SNAPSHOT",
        "CORPORATE_ACTION_SNAPSHOT",
    }
)
_FALSE_AUTHORITIES = (
    "coverage_completeness_proven",
    "semantic_claim_validated",
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
_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "bundle_id",
        "record_type",
        "status",
        "partition_role",
        "bar_snapshot_id",
        "bar_snapshot_record_hash",
        "calendar_snapshot_id",
        "calendar_snapshot_record_hash",
        "partition_manifest_id",
        "partition_manifest_record_hash",
        "security_master_event_count",
        "security_master_tip_record_hash",
        "corporate_action_snapshot_ids",
        "corporate_action_snapshot_record_hashes",
        "security_ids",
        "coverage_start",
        "coverage_end",
        "source_bindings",
        "authenticated_top_level_payloads_only",
        "per_row_bar_payloads_independently_authenticated",
        "point_in_time_contract",
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


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _timestamp(value: Any, name: str) -> datetime:
    try:
        resolved = datetime.fromisoformat(canonical_timestamp(value))
        if resolved.tzinfo is None or resolved.utcoffset() is None:
            raise ValueError
        return resolved.astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _text(value: Any, name: str, maximum: int = 1000) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{name} must be canonical nonempty text")
    return value


def _sha256(value: Any, name: str) -> str:
    resolved = _text(value, name, 64)
    if len(resolved) != 64 or resolved != resolved.lower():
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    try:
        bytes.fromhex(resolved)
    except ValueError as error:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest") from error
    return resolved


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("PIT TRAIN source-bundle append made no progress")
        offset += written


def _lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


def _regular_file(descriptor: int, name: str, maximum: int | None = None) -> os.stat_result:
    details = os.fstat(descriptor)
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_nlink != 1
        or (maximum is not None and details.st_size > maximum)
    ):
        raise LedgerIntegrityError(f"{name} is unsafe")
    return details


@contextmanager
def _exclusive_locks(paths: Sequence[Path]) -> Iterator[None]:
    descriptors: list[int] = []
    try:
        for path in sorted(set(paths), key=lambda item: str(item.resolve())):
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                _regular_file(descriptor, "PIT TRAIN source-bundle dependency lock")
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


@dataclass(frozen=True)
class PITTrainSourceBundleInputs:
    """Immutable engine inputs assembled from one verified synthetic bundle."""

    bundle_id: str
    bundle_record_hash: str
    bar_snapshot_id: str
    calendar_snapshot_id: str
    partition_manifest_id: str
    corporate_action_snapshot_ids: tuple[str, ...]
    source_content_evidence_ids: tuple[str, ...]
    bars: tuple[MarketBar, ...]
    corporate_actions: tuple[CorporateAction, ...]
    terminal_outcomes: tuple[TerminalOutcome, ...]
    universe_events: tuple[UniverseEvent, ...]
    engine_symbol_policy: str
    partition_role: str = "TRAIN"
    synthetic_fixture: bool = True
    coverage_completeness_proven: bool = False
    semantic_claim_validated: bool = False
    qualified: bool = False
    train_admitted: bool = False
    validation_admitted: bool = False
    validation_access_authorized: bool = False
    test_admitted: bool = False
    test_access_authorized: bool = False
    performance_claim_allowed: bool = False
    candidate_freeze_allowed: bool = False
    promotion_allowed: bool = False
    broker_submission_enabled: bool = False
    live_trading_enabled: bool = False

    def __post_init__(self) -> None:
        _text(self.bundle_id, "bundle_id", 80)
        _sha256(self.bundle_record_hash, "bundle_record_hash")
        if self.partition_role != "TRAIN" or not self.bars:
            raise ValueError("source bundle may materialize TRAIN bars only")
        if self.engine_symbol_policy != "PERMANENT_SECURITY_ID":
            raise ValueError("source bundle requires permanent-security-ID engine symbols")
        if self.synthetic_fixture is not True or any(
            getattr(self, name) is not False
            for name in (
                "coverage_completeness_proven",
                "semantic_claim_validated",
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
        ):
            raise ValueError("synthetic source bundle cannot assert authority")


class PITTrainSourceBundleLedger:
    """Bind the active synthetic TRAIN PIT ledgers to authenticated source bytes."""

    def __init__(
        self,
        path: str | Path,
        *,
        authenticated_sources: AuthenticatedSourceContentLedger,
        security_master: PointInTimeSecurityMasterLedger,
        session_partitions: PITSessionPartitionLedger,
        daily_bars: PITDailyBarLedger,
        corporate_actions: PITCorporateActionLedger,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self.authenticated_sources = authenticated_sources
        self.security_master = security_master
        self.session_partitions = session_partitions
        self.daily_bars = daily_bars
        self.corporate_actions = corporate_actions
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._validated_records: set[tuple[str, tuple[tuple[str, int, str], ...]]] = set()
        if (
            daily_bars.calendar_ledger.path.resolve()
            != session_partitions.path.resolve()
        ):
            raise ValueError("daily-bar ledger must use the supplied session ledger")
        if (
            daily_bars.security_master_ledger.path.resolve()
            != security_master.path.resolve()
        ):
            raise ValueError("daily-bar ledger must use the supplied security master")
        if (
            corporate_actions.security_master.path.resolve()
            != security_master.path.resolve()
        ):
            raise ValueError("corporate-action ledger must use the supplied security master")

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            details = _regular_file(
                descriptor,
                "PIT TRAIN source-bundle ledger",
                MAX_LEDGER_BYTES,
            )
            chunks: list[bytes] = []
            received = 0
            while received < details.st_size:
                chunk = os.read(
                    descriptor,
                    min(1024 * 1024, details.st_size - received),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
        finally:
            os.close(descriptor)
        raw = b"".join(chunks)
        if received != details.st_size or (raw and not raw.endswith(b"\n")):
            raise LedgerIntegrityError("PIT TRAIN source-bundle ledger is incomplete")
        result: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            try:
                item = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise LedgerIntegrityError(
                    f"invalid PIT TRAIN source-bundle JSON at line {line_number}"
                ) from error
            if not isinstance(item, dict):
                raise LedgerIntegrityError("PIT TRAIN source-bundle record is not an object")
            result.append(item)
        return result

    def append_bundle(
        self,
        *,
        bar_snapshot_id: str,
        corporate_action_snapshot_ids: Sequence[str],
        content_evidence_ids: Sequence[str],
        synthetic_fixture: bool,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        if synthetic_fixture is not True:
            raise ValueError(
                "this source-bundle path accepts deterministic synthetic fixtures only"
            )
        if isinstance(corporate_action_snapshot_ids, (str, bytes)) or not isinstance(
            corporate_action_snapshot_ids, Sequence
        ):
            raise ValueError("corporate_action_snapshot_ids must be a sequence")
        if isinstance(content_evidence_ids, (str, bytes)) or not isinstance(
            content_evidence_ids, Sequence
        ):
            raise ValueError("content_evidence_ids must be a sequence")
        action_ids = tuple(
            sorted(
                _text(item, "snapshot_id", 80)
                for item in corporate_action_snapshot_ids
            )
        )
        evidence_ids = tuple(
            sorted(
                _text(item, "content_evidence_id", 80)
                for item in content_evidence_ids
            )
        )
        if not action_ids or len(action_ids) != len(set(action_ids)):
            raise ValueError("corporate-action snapshot ids must be nonempty and unique")
        if not evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("content evidence ids must be nonempty and unique")
        with _exclusive_locks(self._dependency_locks()):
            dependencies = self._verified_dependencies()
            appended = _timestamp(self._clock(), "append clock")
            if appended > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
                raise ValueError("append clock cannot be materially in the future")
            context = self._resolve(
                bar_snapshot_id=_text(bar_snapshot_id, "bar_snapshot_id", 80),
                corporate_action_snapshot_ids=action_ids,
                content_evidence_ids=evidence_ids,
                require_current=True,
                dependencies=dependencies,
            )
            self._require_sources_recorded_by(
                context["source_bindings"],
                dependencies["sources"],
                appended,
            )
            body: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "policy_version": POLICY_VERSION,
                "bundle_id": "",
                "record_type": "PIT_TRAIN_SOURCE_BUNDLE",
                "status": STATUS,
                "partition_role": "TRAIN",
                "bar_snapshot_id": context["bar"]["bar_snapshot_id"],
                "bar_snapshot_record_hash": context["bar"]["record_hash"],
                "calendar_snapshot_id": context["calendar"]["calendar_snapshot_id"],
                "calendar_snapshot_record_hash": context["calendar"]["record_hash"],
                "partition_manifest_id": context["manifest"]["partition_manifest_id"],
                "partition_manifest_record_hash": context["manifest"]["record_hash"],
                "security_master_event_count": len(context["master_prefix"]),
                "security_master_tip_record_hash": context["master_prefix"][-1]["record_hash"],
                "corporate_action_snapshot_ids": [
                    item["snapshot_id"] for item in context["actions"]
                ],
                "corporate_action_snapshot_record_hashes": [
                    item["record_hash"] for item in context["actions"]
                ],
                "security_ids": context["bar"]["security_ids"],
                "coverage_start": context["bar"]["coverage_start"],
                "coverage_end": context["bar"]["coverage_end"],
                "source_bindings": context["source_bindings"],
                "authenticated_top_level_payloads_only": True,
                "per_row_bar_payloads_independently_authenticated": False,
                "point_in_time_contract": (
                    "effective_at/reported_at/available_at/retrieved_at/recorded_at"
                ),
                "synthetic_fixture": True,
                **{name: False for name in _FALSE_AUTHORITIES},
                "appended_at": appended.isoformat(timespec="microseconds"),
            }
            identity = {
                key: value
                for key, value in body.items()
                if key not in {"bundle_id", "appended_at"}
            }
            body["bundle_id"] = "PTSB-" + _hash(identity)[:32].upper()
            records = self._verify_records(dependencies)
            existing = next(
                (item for item in records if item["bundle_id"] == body["bundle_id"]),
                None,
            )
            if existing is not None:
                logical = {
                    key: value
                    for key, value in existing.items()
                    if key not in {"previous_hash", "record_hash", "appended_at"}
                }
                proposed = {key: value for key, value in body.items() if key != "appended_at"}
                if allow_existing and logical == proposed:
                    return existing
                raise LedgerIntegrityError("PIT TRAIN source bundle already exists")
            material = {
                **body,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _hash(material)}
            self._append_unlocked(record)
            return record

    def verify(self) -> list[dict[str, Any]]:
        return self._verify_records(self._verified_dependencies())

    def _verify_records(
        self,
        dependencies: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> list[dict[str, Any]]:
        records = self.records()
        dependency_signature = tuple(
            (
                name,
                len(values),
                values[-1]["record_hash"] if values else GENESIS_HASH,
            )
            for name, values in sorted(dependencies.items())
        )
        self._validated_records = {
            key for key in self._validated_records if key[1] == dependency_signature
        }
        previous = GENESIS_HASH
        seen: set[str] = set()
        previous_appended: datetime | None = None
        for index, record in enumerate(records, start=1):
            try:
                material = {key: value for key, value in record.items() if key != "record_hash"}
                appended = _timestamp(record["appended_at"], "appended_at")
                identity = {
                    key: value
                    for key, value in record.items()
                    if key
                    not in {"bundle_id", "appended_at", "previous_hash", "record_hash"}
                }
                expected_id = "PTSB-" + _hash(identity)[:32].upper()
                boundary = (
                    set(record) == _RECORD_FIELDS
                    and record["schema_version"] == SCHEMA_VERSION
                    and record["policy_version"] == POLICY_VERSION
                    and record["record_type"] == "PIT_TRAIN_SOURCE_BUNDLE"
                    and record["status"] == STATUS
                    and record["partition_role"] == "TRAIN"
                    and record["bundle_id"] == expected_id
                    and expected_id not in seen
                    and record["previous_hash"] == previous
                    and record["record_hash"] == _hash(material)
                    and (previous_appended is None or appended >= previous_appended)
                    and appended <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                    and record["synthetic_fixture"] is True
                    and record["authenticated_top_level_payloads_only"] is True
                    and record["per_row_bar_payloads_independently_authenticated"] is False
                    and record["point_in_time_contract"]
                    == "effective_at/reported_at/available_at/retrieved_at/recorded_at"
                    and all(record[name] is False for name in _FALSE_AUTHORITIES)
                )
                if not boundary:
                    raise ValueError("immutable boundary failed")
                cache_key = (record["record_hash"], dependency_signature)
                if cache_key not in self._validated_records:
                    self._validate_historical(
                        record,
                        dependencies=dependencies,
                    )
                    self._validated_records.add(cache_key)
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"PIT TRAIN source-bundle record {index} is invalid"
                ) from error
            seen.add(expected_id)
            previous = record["record_hash"]
            previous_appended = appended
        return records

    def materialize_research_inputs(self, bundle_id: str) -> PITTrainSourceBundleInputs:
        with _exclusive_locks(self._dependency_locks()):
            dependencies = self._verified_dependencies()
            records = self._verify_records(dependencies)
            record = next((item for item in records if item["bundle_id"] == bundle_id), None)
            if record is None:
                raise ValueError("bundle_id is not present in the verified ledger")
            context = self._resolve(
                bar_snapshot_id=record["bar_snapshot_id"],
                corporate_action_snapshot_ids=tuple(record["corporate_action_snapshot_ids"]),
                content_evidence_ids=tuple(
                    sorted(
                        {
                            item["content_evidence_id"]
                            for item in record["source_bindings"]
                        }
                    )
                ),
                require_current=True,
                dependencies=dependencies,
            )
            if (
                record["bar_snapshot_record_hash"] != context["bar"]["record_hash"]
                or record["source_bindings"] != context["source_bindings"]
            ):
                raise LedgerIntegrityError("source bundle dependencies changed")
            bars = self.daily_bars.materialize_research_inputs(record["bar_snapshot_id"])
            actions: list[CorporateAction] = []
            outcomes: list[TerminalOutcome] = []
            for snapshot in context["actions"]:
                inputs = self.corporate_actions.materialize_research_inputs(
                    snapshot["snapshot_id"]
                )
                actions.extend(
                    replace(item, symbol=snapshot["security_id"])
                    for item in inputs.corporate_actions
                )
                outcomes.extend(
                    replace(item, symbol=snapshot["security_id"])
                    for item in inputs.terminal_outcomes
                )
        actions.sort(key=lambda item: (item.effective_at, item.symbol, item.action_type))
        outcomes.sort(key=lambda item: (item.effective_at, item.symbol, item.terminal_type))
        return PITTrainSourceBundleInputs(
            bundle_id=record["bundle_id"],
            bundle_record_hash=record["record_hash"],
            bar_snapshot_id=record["bar_snapshot_id"],
            calendar_snapshot_id=record["calendar_snapshot_id"],
            partition_manifest_id=record["partition_manifest_id"],
            corporate_action_snapshot_ids=tuple(record["corporate_action_snapshot_ids"]),
            source_content_evidence_ids=tuple(
                sorted(
                    {
                        item["content_evidence_id"]
                        for item in record["source_bindings"]
                    }
                )
            ),
            bars=bars.bars,
            corporate_actions=tuple(actions),
            terminal_outcomes=tuple(outcomes),
            universe_events=bars.universe_events,
            engine_symbol_policy=bars.engine_symbol_policy,
        )

    def _resolve(
        self,
        *,
        bar_snapshot_id: str,
        corporate_action_snapshot_ids: tuple[str, ...],
        content_evidence_ids: tuple[str, ...],
        require_current: bool,
        dependencies: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        verified = dependencies or self._verified_dependencies()
        source_records = verified["sources"]
        master_records = verified["master"]
        calendar_records = verified["calendar"]
        bar_records = verified["bars"]
        action_records = verified["actions"]
        bars_by_id = {item["bar_snapshot_id"]: item for item in bar_records}
        bar = bars_by_id.get(bar_snapshot_id)
        if bar is None:
            raise ValueError("bar_snapshot_id is not present in the verified ledger")
        superseded_bar_ids = {
            item.get("supersedes_bar_snapshot_id")
            for item in bar_records
            if item.get("supersedes_bar_snapshot_id") is not None
        }
        if require_current and bar_snapshot_id in superseded_bar_ids:
            raise ValueError("source bundle requires the active daily-bar snapshot")
        master_count = bar["security_master_event_count"]
        if type(master_count) is not int or not 1 <= master_count <= len(master_records):
            raise ValueError("daily-bar security-master prefix is unavailable")
        master_prefix = master_records[:master_count]
        if master_prefix[-1]["record_hash"] != bar["security_master_tip_record_hash"]:
            raise ValueError("daily-bar security-master prefix changed")
        if require_current and master_count != len(master_records):
            raise ValueError("source bundle requires the current security-master tip")
        calendars_by_id = {
            item["calendar_snapshot_id"]: item
            for item in calendar_records
            if item.get("record_type") == "PIT_SESSION_CALENDAR_SNAPSHOT"
        }
        manifests_by_id = {
            item["partition_manifest_id"]: item
            for item in calendar_records
            if item.get("record_type") == "PIT_CHRONOLOGICAL_PARTITION_MANIFEST"
        }
        calendar = calendars_by_id.get(bar["calendar_snapshot_id"])
        manifest = manifests_by_id.get(bar["partition_manifest_id"])
        if (
            calendar is None
            or manifest is None
            or calendar["record_hash"] != bar["calendar_snapshot_record_hash"]
            or manifest["record_hash"] != bar["partition_manifest_record_hash"]
            or manifest["calendar_snapshot_id"] != calendar["calendar_snapshot_id"]
        ):
            raise ValueError("daily-bar calendar and partition evidence is inconsistent")
        superseded_calendar_ids = {
            item.get("supersedes_calendar_snapshot_id")
            for item in calendars_by_id.values()
            if item.get("supersedes_calendar_snapshot_id") is not None
        }
        if require_current and calendar["calendar_snapshot_id"] in superseded_calendar_ids:
            raise ValueError("source bundle requires the active XNYS calendar")
        # A verified session ledger permits exactly one immutable manifest per
        # calendar.  Make that dependency invariant explicit at this boundary:
        # a changed partition must arrive on a superseding calendar, which the
        # current-calendar check above then rejects for new materialization.
        manifests_for_calendar = [
            item
            for item in manifests_by_id.values()
            if item["calendar_snapshot_id"] == calendar["calendar_snapshot_id"]
        ]
        if len(manifests_for_calendar) != 1 or manifests_for_calendar[0] != manifest:
            raise ValueError("source bundle requires the calendar's sole manifest")
        self._require_train_coverage(bar, manifest)
        if (
            bar.get("coverage_shape") != "PER_SECURITY_PIT_INTERVALS"
            or bar.get("engine_symbol_policy") != "PERMANENT_SECURITY_ID"
        ):
            raise ValueError(
                "source bundle requires interval coverage with permanent security IDs"
            )
        actions_by_id = {item["snapshot_id"]: item for item in action_records}
        actions = [actions_by_id.get(snapshot_id) for snapshot_id in corporate_action_snapshot_ids]
        if any(item is None for item in actions):
            raise ValueError("corporate-action snapshot is not present in the verified ledger")
        parsed_actions = sorted(
            (
                (
                    item["security_id"],
                    _timestamp(item["covers_from_at"], "covers_from_at"),
                    _timestamp(item["through_at"], "through_at"),
                    item,
                )
                for item in actions
            ),
            key=lambda item: (
                item[0],
                item[1],
                item[2],
                item[3]["snapshot_id"],
            ),
        )
        resolved_actions = [item[3] for item in parsed_actions]
        actions_by_security: dict[
            str, list[tuple[datetime, datetime, Mapping[str, Any]]]
        ] = {}
        for security_id, starts, through, action in parsed_actions:
            actions_by_security.setdefault(security_id, []).append(
                (starts, through, action)
            )
        if sorted(actions_by_security) != sorted(bar["security_ids"]):
            raise ValueError("corporate-action coverage must exactly match daily-bar securities")
        interval_rows = bar["coverage_intervals"]
        intervals = {item["security_id"]: item for item in interval_rows}
        if (
            len(intervals) != len(interval_rows)
            or len(intervals) != len(bar["security_ids"])
            or sorted(intervals) != sorted(bar["security_ids"])
        ):
            raise ValueError("daily-bar security coverage intervals are inconsistent")
        calendar_sessions = calendar["sessions"]
        session_positions = {
            item["session_date"]: index
            for index, item in enumerate(calendar_sessions)
        }
        parsed_sessions = [
            (
                _timestamp(item["open_at"], "session open"),
                _timestamp(item["close_at"], "session close"),
            )
            for item in calendar_sessions
        ]
        superseded_action_ids = {
            item.get("supersedes_snapshot_id")
            for item in action_records
            if item.get("supersedes_snapshot_id") is not None
        }
        for security_id, snapshots in actions_by_security.items():
            interval = intervals[security_id]
            start_position = session_positions.get(interval["coverage_start"])
            end_position = session_positions.get(interval["coverage_end"])
            if (
                start_position is None
                or end_position is None
                or start_position > end_position
            ):
                raise ValueError(
                    f"daily-bar coverage has no calendar sessions for {security_id}"
                )
            for starts, through, action in snapshots:
                if (
                    action["security_master_record_count"] != master_count
                    or action["security_master_record_hash"]
                    != master_prefix[-1]["record_hash"]
                ):
                    raise ValueError(
                        "corporate-action snapshot pins different security-master evidence"
                    )
                if require_current and action["snapshot_id"] in superseded_action_ids:
                    raise ValueError(
                        "source bundle requires active corporate-action snapshots"
                    )
            for prior, current in zip(snapshots, snapshots[1:]):
                if current[0] <= prior[1]:
                    raise ValueError(
                        f"corporate-action coverage overlaps for {security_id}"
                    )
                if current[0] - prior[1] > timedelta(microseconds=1):
                    raise ValueError(
                        f"corporate-action coverage contains a gap for {security_id}"
                    )
            coverage_open, _ = parsed_sessions[start_position]
            _, coverage_close = parsed_sessions[end_position]
            if snapshots[0][0] > coverage_open or snapshots[-1][1] < coverage_close:
                raise ValueError(
                    "corporate-action coverage does not span every replay session "
                    f"for {security_id}"
                )
            if snapshots[0][0] < coverage_open or snapshots[-1][1] > coverage_close:
                raise ValueError(
                    "corporate-action coverage cannot extend beyond the security's "
                    f"TRAIN replay interval for {security_id}"
                )
            segment_position = 0
            for session_open, session_close in parsed_sessions[
                start_position : end_position + 1
            ]:
                while (
                    segment_position < len(snapshots)
                    and snapshots[segment_position][1] < session_open
                ):
                    segment_position += 1
                if (
                    segment_position == len(snapshots)
                    or snapshots[segment_position][0] > session_open
                    or snapshots[segment_position][1] < session_close
                ):
                    raise ValueError(
                        "corporate-action coverage does not span every replay session "
                        f"for {security_id}"
                    )
        sources_by_id = {
            item["content_evidence_id"]: item for item in source_records
        }
        selected_sources = [sources_by_id.get(evidence_id) for evidence_id in content_evidence_ids]
        if any(item is None for item in selected_sources):
            raise ValueError(
                "content evidence id is not present in the authenticated-source ledger"
            )
        selected_by_source: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for item in selected_sources:
            selected_by_source.setdefault(
                (item["source_uri"], item["source_input_sha256"]),
                [],
            ).append(item)
        required = self._required_source_records(
            master_prefix=master_prefix,
            calendar=calendar,
            bar=bar,
            actions=resolved_actions,
        )
        bindings: list[dict[str, str]] = []
        used_ids: set[str] = set()
        for role, source_record_id, uri, digest in required:
            matches = selected_by_source.get((uri, digest), [])
            if len(matches) != 1:
                raise ValueError(
                    "each required source record needs exactly one selected "
                    "authenticated-byte binding"
                )
            evidence = matches[0]
            evidence_id = evidence["content_evidence_id"]
            used_ids.add(evidence_id)
            bindings.append(
                {
                    "role": role,
                    "source_record_id": source_record_id,
                    "content_evidence_id": evidence_id,
                    "source_uri": uri,
                    "source_sha256": digest,
                }
            )
        if used_ids != set(content_evidence_ids):
            raise ValueError("source bundle cannot contain unused authenticated-byte evidence")
        bindings.sort(key=lambda item: (item["role"], item["source_record_id"]))
        return {
            "bar": bar,
            "calendar": calendar,
            "manifest": manifest,
            "master_prefix": master_prefix,
            "actions": resolved_actions,
            "source_bindings": bindings,
        }

    @staticmethod
    def _required_source_records(
        *,
        master_prefix: Sequence[Mapping[str, Any]],
        calendar: Mapping[str, Any],
        bar: Mapping[str, Any],
        actions: Sequence[Mapping[str, Any]],
    ) -> list[tuple[str, str, str, str]]:
        required: list[tuple[str, str, str, str]] = [
            (
                "SECURITY_MASTER_EVENT",
                item["event_id"],
                item["source_uri"],
                item["source_input_sha256"],
            )
            for item in master_prefix
        ]
        required.extend(
            [
                (
                    "XNYS_CALENDAR_SNAPSHOT",
                    calendar["calendar_snapshot_id"],
                    calendar["source_uri"],
                    calendar["source_payload_sha256"],
                ),
                (
                    "DAILY_BAR_SNAPSHOT",
                    bar["bar_snapshot_id"],
                    bar["source_uri"],
                    bar["source_payload_sha256"],
                ),
            ]
        )
        required.extend(
            (
                "CORPORATE_ACTION_SNAPSHOT",
                item["snapshot_id"],
                item["source_uri"],
                item["source_payload_sha256"],
            )
            for item in actions
        )
        return required

    def _validate_historical(
        self,
        record: Mapping[str, Any],
        *,
        dependencies: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> None:
        if record["source_bindings"] != sorted(
            record["source_bindings"],
            key=lambda item: (item["role"], item["source_record_id"]),
        ) or any(
            set(binding)
            != {
                "role",
                "source_record_id",
                "content_evidence_id",
                "source_uri",
                "source_sha256",
            }
            or binding["role"] not in SOURCE_ROLES
            for binding in record["source_bindings"]
        ):
            raise ValueError("source bindings are invalid or non-canonical")
        context = self._resolve(
            bar_snapshot_id=record["bar_snapshot_id"],
            corporate_action_snapshot_ids=tuple(
                record["corporate_action_snapshot_ids"]
            ),
            content_evidence_ids=tuple(
                sorted(
                    {
                        item["content_evidence_id"]
                        for item in record["source_bindings"]
                    }
                )
            ),
            require_current=False,
            dependencies=dependencies,
        )
        expected = {
            "bar_snapshot_record_hash": context["bar"]["record_hash"],
            "calendar_snapshot_id": context["calendar"]["calendar_snapshot_id"],
            "calendar_snapshot_record_hash": context["calendar"]["record_hash"],
            "partition_manifest_id": context["manifest"]["partition_manifest_id"],
            "partition_manifest_record_hash": context["manifest"]["record_hash"],
            "security_master_event_count": len(context["master_prefix"]),
            "security_master_tip_record_hash": context["master_prefix"][-1][
                "record_hash"
            ],
            "corporate_action_snapshot_ids": [
                item["snapshot_id"] for item in context["actions"]
            ],
            "corporate_action_snapshot_record_hashes": [
                item["record_hash"] for item in context["actions"]
            ],
            "security_ids": context["bar"]["security_ids"],
            "coverage_start": context["bar"]["coverage_start"],
            "coverage_end": context["bar"]["coverage_end"],
            "source_bindings": context["source_bindings"],
        }
        if any(record[key] != value for key, value in expected.items()):
            raise ValueError("pinned cross-ledger dependency is inconsistent")
        self._require_sources_recorded_by(
            record["source_bindings"],
            dependencies["sources"],
            _timestamp(record["appended_at"], "appended_at"),
        )

    def _verified_dependencies(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "sources": self.authenticated_sources.verify(),
            "master": self.security_master.verify(),
            "calendar": self.session_partitions.verify(),
            "bars": self.daily_bars.verify(),
            "actions": self.corporate_actions.verify(),
        }

    @staticmethod
    def _require_train_coverage(
        bar: Mapping[str, Any],
        manifest: Mapping[str, Any],
    ) -> None:
        train = next(
            (
                item
                for item in manifest["partitions"]
                if item.get("role") == "TRAIN"
            ),
            None,
        )
        interval_rows = bar.get("coverage_intervals")
        if (
            train is None
            or bar["partition_role"] != "TRAIN"
            or bar["coverage_start"] < train["start_session_date"]
            or bar["coverage_end"] > train["end_session_date"]
            or not isinstance(interval_rows, list)
            or not interval_rows
            or any(
                item.get("coverage_start", "") < train["start_session_date"]
                or item.get("coverage_end", "") > train["end_session_date"]
                for item in interval_rows
            )
        ):
            raise ValueError(
                "source bundle bars must lie wholly inside the pinned TRAIN partition"
            )

    @staticmethod
    def _require_sources_recorded_by(
        bindings: Sequence[Mapping[str, Any]],
        source_records: Sequence[Mapping[str, Any]],
        cutoff: datetime,
    ) -> None:
        sources_by_id = {
            item["content_evidence_id"]: item for item in source_records
        }
        if any(
            _timestamp(
                sources_by_id[binding["content_evidence_id"]]["recorded_at"],
                "source recorded_at",
            )
            > cutoff
            for binding in bindings
        ):
            raise ValueError("source bytes were authenticated after the bundle append")

    def _dependency_locks(self) -> list[Path]:
        return [
            _lock_path(self.path),
            _lock_path(self.authenticated_sources.path),
            _lock_path(self.security_master.path),
            _lock_path(self.session_partitions.path),
            _lock_path(self.daily_bars.path),
            _lock_path(self.corporate_actions.path),
        ]

    def _append_unlocked(self, record: Mapping[str, Any]) -> None:
        payload = (_canonical_json(record) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            details = _regular_file(descriptor, "PIT TRAIN source-bundle ledger")
            if details.st_size + len(payload) > MAX_LEDGER_BYTES:
                raise LedgerIntegrityError("PIT TRAIN source-bundle ledger exceeds its size limit")
            _write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
