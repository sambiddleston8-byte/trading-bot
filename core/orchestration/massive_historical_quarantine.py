from __future__ import annotations

"""Owner-only content-addressed quarantine for raw Massive responses.

Quarantine bytes are retained and re-hashed but are deliberately not written to
the authenticated source-content store and cannot issue replay or synthetic
pilot attestations.  No historical availability or provider payload semantics
are inferred here.
"""

from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from core.data_sources.provider_access import safe_access_dict
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.historical_quarantine_preregistration import (
    ENDPOINT_TEMPLATE,
    HistoricalQuarantinePreregistrationLedger,
)
from core.orchestration.massive_historical_adapter import (
    MassiveHistoricalAdapter,
    MassiveHistoricalSampleClient,
    MassiveHistoricalSource,
    MassiveHistoricalStagingBatch,
    REQUEST_QUERY_CANONICAL,
)


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "massive-raw-historical-quarantine-v1"
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024
MAX_LEDGER_BYTES = 64 * 1024 * 1024
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_QUARANTINED_PAYLOAD_AUTHORITY = object()
_BOUND_STAGING_AUTHORITY = object()
FIXED_TRUE = (
    "raw_response_bytes_retained",
    "payload_hash_verified",
    "quarantine_only",
)
FIXED_FALSE = (
    "transport_independently_authenticated",
    "account_entitlement_authenticated",
    "provider_payload_semantics_qualified",
    "historical_availability_qualified",
    "observation_cutoffs_validated",
    "source_bytes_authenticated",
    "content_evidence_id_issued",
    "dataset_admission_eligible",
    "dataset_admitted",
    "replay_data_attestation_issued",
    "synthetic_pilot_attestation_issued",
    "engine_input_ready",
    "parameter_search_executed",
    "untouched_test_opened",
    "guardrailed_replay_executed",
    "performance_calculated",
    "performance_claim_allowed",
    "broker_connection_allowed",
    "orders_submitted",
    "live_trading_enabled",
)
RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "quarantine_capture_id",
        "record_type",
        "status",
        "recorded_at",
        "preregistration_id",
        "preregistration_record_hash",
        "entitlement_metadata_sha256",
        "provider_id",
        "provider_dataset_id",
        "symbol",
        "split_role",
        "request_start",
        "request_end",
        "request_uri",
        "request_query_canonical",
        "requested_at",
        "retrieved_at",
        "response_status_code",
        "response_headers_sha256",
        "media_type",
        "provider_access",
        "payload_sha256",
        "byte_length",
        "blob_relative_path",
        "previous_hash",
        "record_hash",
        *FIXED_TRUE,
        *FIXED_FALSE,
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
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Massive quarantine append made no progress")
        offset += count


def _no_follow() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def _required(value: Any, name: str, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    resolved = value.strip()
    if not resolved or resolved != value or len(resolved) > maximum:
        raise ValueError(f"{name} must be nonempty canonical text")
    if any(ord(character) < 32 for character in resolved):
        raise ValueError(f"{name} must not contain control characters")
    return resolved


def _sha256(value: Any, name: str) -> str:
    resolved = _required(value, name, 64).lower()
    if SHA256_PATTERN.fullmatch(resolved) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _timestamp(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _date(value: Any, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date")
    try:
        resolved = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO date") from error
    if resolved.isoformat() != value:
        raise ValueError(f"{name} must be a canonical ISO date")
    return resolved


def _check_regular_descriptor(descriptor: int, *, mode: int, name: str) -> None:
    details = os.fstat(descriptor)
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_IMODE(details.st_mode) != mode
        or details.st_nlink != 1
    ):
        raise LedgerIntegrityError(f"{name} is not a private regular file")


def _private_directory(path: Path) -> None:
    if not path.exists():
        parent = path.parent
        details = parent.lstat()
        if (
            parent.is_symlink()
            or not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) & 0o022
        ):
            raise LedgerIntegrityError("Massive quarantine parent directory is unsafe")
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    details = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise LedgerIntegrityError("Massive quarantine directory must be owner-only")


def _read_private_file(path: Path, *, mode: int, name: str, maximum_bytes: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | _no_follow())
    try:
        _check_regular_descriptor(descriptor, mode=mode, name=name)
        if os.fstat(descriptor).st_size > maximum_bytes:
            raise LedgerIntegrityError(f"{name} exceeds its safe size limit")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _no_follow(),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _capture_id(record: Mapping[str, Any]) -> str:
    material = {
        key: record[key]
        for key in (
            "preregistration_id",
            "symbol",
            "split_role",
            "request_start",
            "request_end",
            "request_uri",
            "request_query_canonical",
            "requested_at",
            "retrieved_at",
            "payload_sha256",
        )
    }
    return "QHR-" + hashlib.sha256(
        _canonical_json([material, POLICY_VERSION]).encode("utf-8")
    ).hexdigest()[:32].upper()


def planned_request_slices(
    record: Mapping[str, Any],
) -> tuple[tuple[str, str, str, str], ...]:
    """Return split-contained deterministic 31-day request slices for one plan."""

    start = _date(record.get("acquisition_start"), "acquisition_start")
    end = _date(record.get("acquisition_end"), "acquisition_end")
    chunk_days = record.get("maximum_chunk_days")
    if chunk_days != 31 or end < start:
        raise ValueError("verified preregistration requires 31-day ordered chunks")
    basket = record.get("target_basket")
    if not isinstance(basket, list) or not basket:
        raise ValueError("verified preregistration requires a target basket")
    splits = record.get("splits")
    if not isinstance(splits, list) or not splits:
        raise ValueError("verified preregistration requires chronological splits")
    date_ranges: list[tuple[str, str, str]] = []
    for split in splits:
        if not isinstance(split, Mapping):
            raise ValueError("verified preregistration split is invalid")
        role = _required(split.get("role"), "split role", 30)
        cursor = _date(split.get("start"), f"{role} start")
        split_end = _date(split.get("end"), f"{role} end")
        while cursor <= split_end:
            through = min(split_end, cursor + timedelta(days=chunk_days - 1))
            date_ranges.append((role, cursor.isoformat(), through.isoformat()))
            cursor = through + timedelta(days=1)
    return tuple(
        (symbol, role, request_start, request_end)
        for symbol in basket
        for role, request_start, request_end in date_ranges
    )


@dataclass(frozen=True, slots=True)
class QuarantinedHistoricalPayload:
    """Verified raw bytes that carry no admission or engine authority."""

    record: Mapping[str, Any]
    payload_bytes: bytes
    quarantine_only: bool = True
    replay_data_attestation_issued: bool = False
    synthetic_pilot_attestation_issued: bool = False
    engine_input_ready: bool = False
    _authority: object = dataclass_field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._authority is not _QUARANTINED_PAYLOAD_AUTHORITY:
            raise PermissionError(
                "QuarantinedHistoricalPayload must be issued by the verified store"
            )
        object.__setattr__(self, "_authority", None)
        if (
            self.quarantine_only is not True
            or self.replay_data_attestation_issued is not False
            or self.synthetic_pilot_attestation_issued is not False
            or self.engine_input_ready is not False
        ):
            raise ValueError("quarantined payload authority flags were altered")


@dataclass(frozen=True, slots=True)
class QuarantineBoundHistoricalStagingBatch:
    """Non-admitted staging whose capture identity is fixed by the store."""

    quarantine_capture_id: str
    quarantine_record_hash: str
    preregistration_id: str
    symbol: str
    split_role: str
    request_start: str
    request_end: str
    retrieved_at: str
    source_payload_sha256: str
    staging: MassiveHistoricalStagingBatch
    capture_binding_sha256: str
    quarantine_only: bool = True
    dataset_admitted: bool = False
    engine_input_ready: bool = False
    replay_executed: bool = False
    broker_connection_allowed: bool = False
    orders_submitted: bool = False
    live_trading_enabled: bool = False
    _authority: object = dataclass_field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._authority is not _BOUND_STAGING_AUTHORITY:
            raise PermissionError(
                "QuarantineBoundHistoricalStagingBatch must be issued by the verified store"
            )
        object.__setattr__(self, "_authority", None)
        if not isinstance(self.staging, MassiveHistoricalStagingBatch):
            raise ValueError("staging must be a MassiveHistoricalStagingBatch")
        if (
            self.quarantine_only is not True
            or self.dataset_admitted is not False
            or self.engine_input_ready is not False
            or self.replay_executed is not False
            or self.broker_connection_allowed is not False
            or self.orders_submitted is not False
            or self.live_trading_enabled is not False
            or self.staging.engine_input_ready is not False
            or self.staging.replay_executed is not False
        ):
            raise ValueError("capture-bound staging authority flags were altered")

    def as_dict(self) -> dict[str, Any]:
        return {
            "quarantine_capture_id": self.quarantine_capture_id,
            "quarantine_record_hash": self.quarantine_record_hash,
            "preregistration_id": self.preregistration_id,
            "symbol": self.symbol,
            "split_role": self.split_role,
            "request_start": self.request_start,
            "request_end": self.request_end,
            "retrieved_at": self.retrieved_at,
            "source_payload_sha256": self.source_payload_sha256,
            "staging_sha256": self.staging.staging_sha256,
            "capture_binding_sha256": self.capture_binding_sha256,
            "quarantine_capture_bound": True,
            "provider_payload_semantics_qualified": False,
            "source_bytes_authenticated": False,
            "observation_selection_validated": False,
            "role_coverage_validated": False,
            "quarantine_only": True,
            "dataset_admitted": False,
            "engine_input_ready": False,
            "replay_executed": False,
            "broker_connection_allowed": False,
            "orders_submitted": False,
            "live_trading_enabled": False,
        }


class MassiveHistoricalQuarantineStore:
    """Retain and re-verify exact provider responses outside admission storage."""

    def __init__(
        self,
        root: str | Path,
        *,
        preregistration_ledger: HistoricalQuarantinePreregistrationLedger,
        admitted_store_roots: Sequence[str | Path],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(root)
        self.ledger_path = self.root / "captures.jsonl"
        self.blob_directory = self.root / "blobs"
        self.preregistration_ledger = preregistration_ledger
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        quarantine = self.root.resolve()
        if not admitted_store_roots:
            raise ValueError("at least one admitted store root must be declared")
        for value in admitted_store_roots:
            admitted = Path(value).resolve()
            if (
                quarantine == admitted
                or quarantine in admitted.parents
                or admitted in quarantine.parents
            ):
                raise ValueError("quarantine and admitted store roots must be disjoint")

    def records(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        raw = _read_private_file(
            self.ledger_path,
            mode=0o600,
            name="Massive quarantine ledger",
            maximum_bytes=MAX_LEDGER_BYTES,
        )
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Massive quarantine ledger has an incomplete final line")
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            try:
                record = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise LedgerIntegrityError(
                    f"invalid Massive quarantine JSON at line {line_number}"
                ) from error
            if not isinstance(record, dict):
                raise LedgerIntegrityError("Massive quarantine line is not an object")
            records.append(record)
        return records

    def capture_response(
        self,
        *,
        preregistration_id: str,
        symbol: str,
        request_start: str,
        request_end: str,
        request_uri: str,
        request_query_canonical: str,
        requested_at: str | datetime,
        retrieved_at: str | datetime,
        response_status_code: int,
        response_headers_sha256: str,
        payload: bytes,
        provider_access: Mapping[str, Any],
        media_type: str = "application/json",
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        plan = self.preregistration_ledger.require(preregistration_id)
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_PAYLOAD_BYTES:
            raise ValueError("quarantine payload must contain between 1 byte and 5 MiB")
        ticker = _required(symbol, "symbol", 32)
        if ticker not in plan["target_basket"]:
            raise ValueError("symbol is outside the preregistered basket")
        start = _date(request_start, "request_start")
        end = _date(request_end, "request_end")
        if (
            end < start
            or (end - start).days >= plan["maximum_chunk_days"]
            or start < _date(plan["acquisition_start"], "acquisition_start")
            or end > _date(plan["acquisition_end"], "acquisition_end")
        ):
            raise ValueError("request dates violate the preregistered acquisition window")
        slice_roles = {
            (planned_symbol, planned_start, planned_end): role
            for planned_symbol, role, planned_start, planned_end in planned_request_slices(
                plan
            )
        }
        split_role = slice_roles.get((ticker, start.isoformat(), end.isoformat()))
        if split_role is None:
            raise ValueError("request dates are not an exact preregistered request slice")
        expected_uri = ENDPOINT_TEMPLATE.format(
            ticker=ticker,
            **{"from": start.isoformat(), "to": end.isoformat()},
        )
        uri = _required(request_uri, "request_uri", 2_000)
        parsed = urlsplit(uri)
        if (
            uri != expected_uri
            or parsed.hostname != "api.massive.com"
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("request_uri does not match the credential-free preregistration")
        if request_query_canonical != REQUEST_QUERY_CANONICAL:
            raise ValueError("request query does not match the preregistered request policy")
        requested = _timestamp(requested_at, "requested_at")
        retrieved = _timestamp(retrieved_at, "retrieved_at")
        recorded = _timestamp(self._clock(), "recording clock")
        access_not_before = _timestamp(
            plan["data_access_not_before"], "data_access_not_before"
        )
        if requested < access_not_before or retrieved < requested or recorded < retrieved:
            raise ValueError("request, retrieval and recording chronology is invalid")
        now = datetime.now(timezone.utc)
        if not now - MAX_CLOCK_SKEW <= recorded <= now + MAX_CLOCK_SKEW:
            raise ValueError("recording clock must match actual capture time")
        if media_type != "application/json":
            raise ValueError("Massive quarantine media_type must be application/json")
        if response_status_code != 200 or isinstance(response_status_code, bool):
            raise ValueError("Massive quarantine requires an HTTP 200 response")
        headers_hash = _sha256(
            response_headers_sha256, "response_headers_sha256"
        )
        access = safe_access_dict(provider_access)
        if (
            access is None
            or access["provider"] != "Massive historical sample"
            or access["attempts"] != 1
            or access["retry_count"] != 0
        ):
            raise ValueError("provider access metadata violates the one-request boundary")
        payload_hash = hashlib.sha256(payload).hexdigest()
        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "record_type": "MASSIVE_RAW_RESPONSE_QUARANTINE_CAPTURE",
            "status": "RAW_BYTES_RETAINED_NOT_ADMITTED",
            "recorded_at": recorded.isoformat(timespec="microseconds"),
            "preregistration_id": plan["preregistration_id"],
            "preregistration_record_hash": plan["record_hash"],
            "entitlement_metadata_sha256": plan["entitlement_metadata_sha256"],
            "provider_id": plan["provider_id"],
            "provider_dataset_id": plan["provider_dataset_id"],
            "symbol": ticker,
            "split_role": split_role,
            "request_start": start.isoformat(),
            "request_end": end.isoformat(),
            "request_uri": uri,
            "request_query_canonical": REQUEST_QUERY_CANONICAL,
            "requested_at": requested.isoformat(timespec="microseconds"),
            "retrieved_at": retrieved.isoformat(timespec="microseconds"),
            "response_status_code": 200,
            "response_headers_sha256": headers_hash,
            "media_type": media_type,
            "provider_access": access,
            "payload_sha256": payload_hash,
            "byte_length": len(payload),
            "blob_relative_path": f"sha256/{payload_hash[:2]}/{payload_hash}.blob",
            **{name: True for name in FIXED_TRUE},
            **{name: False for name in FIXED_FALSE},
        }
        record["quarantine_capture_id"] = _capture_id(record)
        return self._append(record, payload=payload, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        _private_directory(self.root)
        records = self.records()
        previous = GENESIS_HASH
        seen_ids: set[str] = set()
        seen_slices: set[tuple[str, str, str, str]] = set()
        referenced_blobs: set[Path] = set()
        blob_root = self.blob_directory.resolve()
        for index, record in enumerate(records, start=1):
            try:
                if set(record) != RECORD_FIELDS:
                    raise ValueError("missing or unsupported fields")
                plan = self.preregistration_ledger.require(record["preregistration_id"])
                start = _date(record["request_start"], "request_start")
                end = _date(record["request_end"], "request_end")
                requested = _timestamp(record["requested_at"], "requested_at")
                retrieved = _timestamp(record["retrieved_at"], "retrieved_at")
                recorded = _timestamp(record["recorded_at"], "recorded_at")
                payload_hash = _sha256(record["payload_sha256"], "payload_sha256")
                relative = Path(_required(record["blob_relative_path"], "blob_relative_path"))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("unsafe blob path")
                blob_path = self.blob_directory / relative
                if blob_path.is_symlink() or not blob_path.is_file():
                    raise ValueError("blob is missing or unsafe")
                if blob_path.resolve() != blob_root / relative:
                    raise ValueError("blob escaped the quarantine root")
                payload = _read_private_file(
                    blob_path,
                    mode=0o400,
                    name="Massive quarantine blob",
                    maximum_bytes=MAX_PAYLOAD_BYTES,
                )
                access = safe_access_dict(record["provider_access"])
                slice_key = (
                    record["preregistration_id"],
                    record["symbol"],
                    record["request_start"],
                    record["request_end"],
                )
                material = {
                    key: value for key, value in record.items() if key != "record_hash"
                }
                expected_uri = ENDPOINT_TEMPLATE.format(
                    ticker=record["symbol"],
                    **{"from": start.isoformat(), "to": end.isoformat()},
                )
                boundary = (
                    record["schema_version"] == SCHEMA_VERSION
                    and record["policy_version"] == POLICY_VERSION
                    and record["record_type"] == "MASSIVE_RAW_RESPONSE_QUARANTINE_CAPTURE"
                    and record["status"] == "RAW_BYTES_RETAINED_NOT_ADMITTED"
                    and record["quarantine_capture_id"] == _capture_id(record)
                    and record["quarantine_capture_id"] not in seen_ids
                    and slice_key not in seen_slices
                    and record["preregistration_record_hash"] == plan["record_hash"]
                    and record["entitlement_metadata_sha256"]
                    == plan["entitlement_metadata_sha256"]
                    and record["provider_id"] == plan["provider_id"]
                    and record["provider_dataset_id"] == plan["provider_dataset_id"]
                    and record["symbol"] in plan["target_basket"]
                    and (
                        record["symbol"],
                        record["split_role"],
                        record["request_start"],
                        record["request_end"],
                    )
                    in planned_request_slices(plan)
                    and 0 <= (end - start).days < plan["maximum_chunk_days"]
                    and start >= _date(plan["acquisition_start"], "acquisition_start")
                    and end <= _date(plan["acquisition_end"], "acquisition_end")
                    and record["request_uri"] == expected_uri
                    and record["request_query_canonical"] == REQUEST_QUERY_CANONICAL
                    and requested
                    >= _timestamp(plan["data_access_not_before"], "data_access_not_before")
                    and requested <= retrieved <= recorded
                    and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                    and record["media_type"] == "application/json"
                    and record["response_status_code"] == 200
                    and record["response_headers_sha256"]
                    == _sha256(
                        record["response_headers_sha256"],
                        "response_headers_sha256",
                    )
                    and access == record["provider_access"]
                    and access is not None
                    and access["provider"] == "Massive historical sample"
                    and access["attempts"] == 1
                    and access["retry_count"] == 0
                    and record["payload_sha256"] == payload_hash
                    and hashlib.sha256(payload).hexdigest() == payload_hash
                    and record["byte_length"] == len(payload)
                    and record["blob_relative_path"]
                    == f"sha256/{payload_hash[:2]}/{payload_hash}.blob"
                    and all(record[name] is True for name in FIXED_TRUE)
                    and all(record[name] is False for name in FIXED_FALSE)
                    and record["previous_hash"] == previous
                    and record["record_hash"] == _record_hash(material)
                )
            except (KeyError, OSError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Massive quarantine capture {index} is invalid"
                ) from error
            if not boundary:
                raise LedgerIntegrityError(
                    f"Massive quarantine capture {index} violates its boundary"
                )
            seen_ids.add(record["quarantine_capture_id"])
            seen_slices.add(slice_key)
            referenced_blobs.add(blob_path.resolve())
            previous = record["record_hash"]
        if self.blob_directory.exists():
            _private_directory(self.blob_directory)
            discovered_blobs: set[Path] = set()
            for item in self.blob_directory.rglob("*"):
                if item.is_symlink():
                    raise LedgerIntegrityError("Massive quarantine blob tree contains a symlink")
                details = item.lstat()
                if item.is_dir():
                    if stat.S_IMODE(details.st_mode) != 0o700:
                        raise LedgerIntegrityError(
                            "Massive quarantine blob directory is not owner-only"
                        )
                elif item.is_file():
                    if item.suffix != ".blob":
                        raise LedgerIntegrityError(
                            "Massive quarantine blob tree contains an unexpected file"
                        )
                    discovered_blobs.add(item.resolve())
                else:
                    raise LedgerIntegrityError(
                        "Massive quarantine blob tree contains an unsafe entry"
                    )
            if discovered_blobs != referenced_blobs:
                raise LedgerIntegrityError(
                    "Massive quarantine contains missing or unreferenced blobs"
                )
        return records

    def read_verified(self, quarantine_capture_id: str) -> QuarantinedHistoricalPayload:
        identifier = _required(quarantine_capture_id, "quarantine_capture_id", 100)
        record = next(
            (item for item in self.verify() if item["quarantine_capture_id"] == identifier),
            None,
        )
        if record is None:
            raise ValueError("quarantined historical capture was not found")
        if record["split_role"] == "UNTOUCHED_TEST":
            raise ValueError("untouched-test quarantine bytes cannot be opened")
        payload = _read_private_file(
            self.blob_directory / record["blob_relative_path"],
            mode=0o400,
            name="Massive quarantine blob",
            maximum_bytes=MAX_PAYLOAD_BYTES,
        )
        return QuarantinedHistoricalPayload(
            record=MappingProxyType(dict(record)),
            payload_bytes=payload,
            _authority=_QUARANTINED_PAYLOAD_AUTHORITY,
        )

    def stage_verified_capture(
        self,
        quarantine_capture_id: str,
        *,
        decision_at: str | datetime,
    ) -> QuarantineBoundHistoricalStagingBatch:
        """Normalize one non-test capture with all source metadata store-bound."""

        captured = self.read_verified(quarantine_capture_id)
        record = captured.record
        if record["split_role"] not in {"TRAIN", "VALIDATION"}:
            raise ValueError("only train or validation captures may be staged")
        staging = MassiveHistoricalAdapter().normalize(
            source=MassiveHistoricalSource(
                transport_format="JSON",
                retrieved_at=record["retrieved_at"],
                payload_bytes=captured.payload_bytes,
            ),
            decision_at=decision_at,
        )
        start = _date(record["request_start"], "request_start")
        end = _date(record["request_end"], "request_end")
        if (
            staging.retrieved_at != record["retrieved_at"]
            or staging.source_payload_sha256 != record["payload_sha256"]
        ):
            raise LedgerIntegrityError(
                "normalized staging no longer matches its verified capture"
            )
        for observation in staging.observations:
            try:
                ticker = observation["payload"]["ticker"]
                window = datetime.fromisoformat(
                    observation["payload"]["bar_window_start_at"]
                ).astimezone(timezone.utc)
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    "normalized staging lacks capture-bound identity"
                ) from error
            if (
                ticker != record["symbol"]
                or not start <= window.date() <= end
                or observation["retrieved_at"] != record["retrieved_at"]
                or observation["source_payload_sha256"] != record["payload_sha256"]
            ):
                raise LedgerIntegrityError(
                    "normalized staging violates its symbol, slice or retrieval binding"
                )
        binding_material = {
            "quarantine_capture_id": record["quarantine_capture_id"],
            "quarantine_record_hash": record["record_hash"],
            "preregistration_id": record["preregistration_id"],
            "symbol": record["symbol"],
            "split_role": record["split_role"],
            "request_start": record["request_start"],
            "request_end": record["request_end"],
            "retrieved_at": record["retrieved_at"],
            "source_payload_sha256": record["payload_sha256"],
            "staging_sha256": staging.staging_sha256,
        }
        binding_hash = hashlib.sha256(
            _canonical_json(binding_material).encode("utf-8")
        ).hexdigest()
        return QuarantineBoundHistoricalStagingBatch(
            quarantine_capture_id=record["quarantine_capture_id"],
            quarantine_record_hash=record["record_hash"],
            preregistration_id=record["preregistration_id"],
            symbol=record["symbol"],
            split_role=record["split_role"],
            request_start=record["request_start"],
            request_end=record["request_end"],
            retrieved_at=record["retrieved_at"],
            source_payload_sha256=record["payload_sha256"],
            staging=staging,
            capture_binding_sha256=binding_hash,
            _authority=_BOUND_STAGING_AUTHORITY,
        )

    def status(self, preregistration_id: str) -> dict[str, Any]:
        plan = self.preregistration_ledger.require(preregistration_id)
        expected = planned_request_slices(plan)
        captured = {
            (
                item["symbol"],
                item["split_role"],
                item["request_start"],
                item["request_end"],
            )
            for item in self.verify()
            if item["preregistration_id"] == preregistration_id
        }
        missing = [item for item in expected if item not in captured]
        return {
            "preregistration_id": preregistration_id,
            "expected_request_count": len(expected),
            "captured_request_count": len(expected) - len(missing),
            "missing_request_count": len(missing),
            "complete": not missing,
            "quarantine_only": True,
            "dataset_admitted": False,
            "engine_input_ready": False,
            "replay_executed": False,
            "broker_connection_allowed": False,
            "orders_submitted": False,
            "live_trading_enabled": False,
        }

    def _append(
        self,
        record: dict[str, Any],
        *,
        payload: bytes,
        allow_existing: bool,
    ) -> dict[str, Any]:
        _private_directory(self.root)
        _private_directory(self.blob_directory)
        lock_path = self.root / "captures.lock"
        lock = os.open(lock_path, os.O_CREAT | os.O_RDWR | _no_follow(), 0o600)
        try:
            _check_regular_descriptor(lock, mode=0o600, name="Massive quarantine lock")
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = self.verify()
            slice_key = (
                record["preregistration_id"],
                record["symbol"],
                record["request_start"],
                record["request_end"],
            )
            existing = next(
                (
                    item
                    for item in records
                    if (
                        item["preregistration_id"],
                        item["symbol"],
                        item["request_start"],
                        item["request_end"],
                    )
                    == slice_key
                ),
                None,
            )
            if existing is not None:
                if allow_existing and existing["payload_sha256"] == record["payload_sha256"]:
                    return existing
                raise LedgerIntegrityError("quarantine request slice is already captured")
            relative = Path(record["blob_relative_path"])
            _private_directory(self.blob_directory / "sha256")
            shard = self.blob_directory / relative.parent
            _private_directory(shard)
            blob = self.blob_directory / relative
            if blob.exists():
                existing_payload = _read_private_file(
                    blob,
                    mode=0o400,
                    name="Massive quarantine blob",
                    maximum_bytes=MAX_PAYLOAD_BYTES,
                )
                if existing_payload != payload:
                    raise LedgerIntegrityError("content-addressed quarantine blob conflicts")
            else:
                try:
                    target = os.open(
                        blob,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _no_follow(),
                        0o400,
                    )
                    try:
                        _check_regular_descriptor(
                            target,
                            mode=0o400,
                            name="Massive quarantine blob",
                        )
                        _write_all(target, payload)
                        os.fsync(target)
                    finally:
                        os.close(target)
                except BaseException:
                    try:
                        blob.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise
                _fsync_directory(shard)
            material = {
                **record,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            complete = {**material, "record_hash": _record_hash(material)}
            target = os.open(
                self.ledger_path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | _no_follow(),
                0o600,
            )
            try:
                _check_regular_descriptor(target, mode=0o600, name="Massive quarantine ledger")
                _write_all(target, (_canonical_json(complete) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            _fsync_directory(self.root)
            return complete
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)


class MassiveHistoricalQuarantineFetcher:
    """Execute only missing preregistered slices into quarantine."""

    def __init__(
        self,
        *,
        store: MassiveHistoricalQuarantineStore,
        client: MassiveHistoricalSampleClient,
    ) -> None:
        self.store = store
        self.client = client

    def fetch(self, preregistration_id: str) -> dict[str, Any]:
        plan = self.store.preregistration_ledger.require(preregistration_id)
        if plan["entitlement_metadata"]["asserted_request_limit_per_minute"] != 5:
            raise ValueError("preregistered request limit does not match the fetch client")
        captured = {
            (
                item["symbol"],
                item["split_role"],
                item["request_start"],
                item["request_end"],
            )
            for item in self.store.verify()
            if item["preregistration_id"] == preregistration_id
        }
        for symbol, split_role, request_start, request_end in planned_request_slices(plan):
            if (symbol, split_role, request_start, request_end) in captured:
                continue
            fetched = self.client.fetch_daily_bars(
                symbol=symbol,
                start=request_start,
                end=request_end,
            )
            self.store.capture_response(
                preregistration_id=preregistration_id,
                symbol=symbol,
                request_start=request_start,
                request_end=request_end,
                request_uri=fetched.request_uri,
                request_query_canonical=fetched.request_query_canonical,
                requested_at=fetched.requested_at,
                retrieved_at=fetched.retrieved_at,
                response_status_code=fetched.response_status_code,
                response_headers_sha256=fetched.response_headers_sha256,
                payload=fetched.payload_bytes,
                provider_access=fetched.provider_access,
                media_type=fetched.media_type,
            )
        return self.store.status(preregistration_id)
