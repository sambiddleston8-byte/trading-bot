from __future__ import annotations

"""Content-addressed paper-broker captures; retained bytes are not broker truth."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Mapping

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "paper-broker-capture-admission-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
MAX_CAPTURE_BYTES = 2 * 1024 * 1024
MAX_LEDGER_BYTES = 64 * 1024 * 1024
CAPTURE_KIND = "ALPACA_PAPER_V2_ACCOUNT"
CAPTURE_METHOD = "OPERATOR_MANUAL_CAPTURE"
CAPTURE_ATTESTOR = "LOCAL_OPERATOR_UNVERIFIED"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
FIXED_FIELDS = {
    "capture_bytes_retained": True,
    "payload_hash_verified": True,
    "transport_authenticated": False,
    "broker_signature_present": False,
    "broker_reconciliation_complete": False,
    "settlement_breakdown_available": False,
    "semantic_claim_validated": False,
    "credentials_accessed": False,
    "network_request_performed": False,
    "order_submitted": False,
    "paper_order_submission_allowed": False,
    "live_trading_enabled": False,
    "recommendation_provided": False,
    "human_review_eligible": False,
}
RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "capture_evidence_id",
        "record_type",
        "status",
        "capture_kind",
        "capture_method",
        "capture_attested_by",
        "broker",
        "broker_environment",
        "account_reference_sha256",
        "observed_at",
        "captured_at",
        "recorded_at",
        "payload_sha256",
        "byte_length",
        "blob_relative_path",
        "previous_hash",
        "record_hash",
    }
    | set(FIXED_FIELDS)
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Unable to complete paper-broker capture append")
        offset += count


def _no_follow() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def _check_regular_descriptor(descriptor: int, *, mode: int, name: str) -> None:
    details = os.fstat(descriptor)
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_IMODE(details.st_mode) != mode
        or details.st_nlink != 1
    ):
        raise LedgerIntegrityError(f"{name} is not a private regular file.")


def _read_private_file(
    path: Path, *, mode: int, name: str, maximum_bytes: int
) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | _no_follow())
    try:
        _check_regular_descriptor(descriptor, mode=mode, name=name)
        if os.fstat(descriptor).st_size > maximum_bytes:
            raise LedgerIntegrityError(f"{name} exceeds its safe size limit.")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _private_directory(path: Path) -> None:
    if not path.exists():
        parent = path.parent
        parent_details = parent.lstat()
        if (
            parent.is_symlink()
            or not stat.S_ISDIR(parent_details.st_mode)
            or parent_details.st_uid != os.geteuid()
            or stat.S_IMODE(parent_details.st_mode) & 0o022
        ):
            raise LedgerIntegrityError(
                "Paper-broker capture parent directory is unsafe."
            )
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    details = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
    ):
        raise LedgerIntegrityError("Paper-broker capture directory is unsafe.")
    if stat.S_IMODE(details.st_mode) != 0o700:
        raise LedgerIntegrityError("Paper-broker capture directory must be owner-only.")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _no_follow()
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _timestamp(value: str | datetime) -> datetime:
    resolved = datetime.fromisoformat(canonical_timestamp(value))
    if resolved.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return resolved


def _required(value: Any, name: str, maximum: int = 300) -> str:
    resolved = str(value or "").strip()
    if not resolved or len(resolved) > maximum:
        raise ValueError(f"{name} is required and must not exceed {maximum} characters")
    return resolved


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if SHA256_PATTERN.fullmatch(resolved) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _payload_object(payload: bytes) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("capture payload must not contain duplicate JSON keys")
            result[key] = value
        return result

    def reject_non_finite(value: str) -> None:
        raise ValueError(f"capture payload contains non-standard JSON constant {value}")

    try:
        parsed = json.loads(
            payload,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_finite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("capture payload must be a UTF-8 JSON object") from error
    if not isinstance(parsed, dict):
        raise ValueError("capture payload must be a UTF-8 JSON object")
    return parsed


def _account_hash(payload: bytes) -> str:
    parsed = _payload_object(payload)
    account_id = parsed.get("id")
    if (
        not isinstance(account_id, str)
        or not account_id
        or account_id != account_id.strip()
        or len(account_id) > 500
    ):
        raise ValueError("captured account id must be an exact non-empty JSON string")
    return hashlib.sha256(account_id.encode("utf-8")).hexdigest()


def _capture_id(
    payload_hash: str,
    account_hash: str,
    observed_at: str,
    captured_at: str,
    method: str,
    attested_by: str,
) -> str:
    material = [
        CAPTURE_KIND,
        payload_hash,
        account_hash,
        observed_at,
        captured_at,
        method,
        attested_by,
        POLICY_VERSION,
    ]
    return "PPCE-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class PaperBrokerCaptureLedger:
    """Admit exact operator-supplied paper-account bytes without claiming authenticity."""

    def __init__(self, path: str | Path, blob_directory: str | Path) -> None:
        self.path = Path(path)
        self.blob_directory = Path(blob_directory)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = _read_private_file(
                self.path,
                mode=0o600,
                name="Paper-broker capture ledger",
                maximum_bytes=MAX_LEDGER_BYTES,
            )
        except OSError as error:
            raise LedgerIntegrityError("Paper-broker capture ledger is unsafe.") from error
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Paper-broker capture has an incomplete final line.")
        records: list[dict[str, Any]] = []
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise LedgerIntegrityError("Paper-broker capture ledger is not UTF-8.") from error
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                raise LedgerIntegrityError(
                    f"Blank paper-broker capture at line {line_number}."
                )
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise LedgerIntegrityError(
                    f"Invalid paper-broker capture JSON at line {line_number}."
                ) from error
            if not isinstance(record, dict):
                raise LedgerIntegrityError(
                    f"Paper-broker capture {line_number} is not an object."
                )
            records.append(record)
        return records

    def admit(
        self,
        *,
        payload: bytes,
        capture_kind: str,
        broker: str,
        broker_environment: str,
        account_reference_sha256: str,
        observed_at: str | datetime,
        captured_at: str | datetime,
        recorded_at: str | datetime,
        capture_method: str,
        capture_attested_by: str,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_CAPTURE_BYTES:
            raise ValueError("payload must contain between 1 byte and 2 MiB")
        if capture_kind != CAPTURE_KIND:
            raise ValueError("capture_kind is not permitted")
        if broker != "Alpaca" or broker_environment != "PAPER":
            raise ValueError("Only the Alpaca PAPER broker boundary is permitted")
        if capture_method != CAPTURE_METHOD:
            raise ValueError("capture_method is not permitted")
        if capture_attested_by != CAPTURE_ATTESTOR:
            raise ValueError("capture_attested_by must use the non-identifying local attestor")
        attested_by = CAPTURE_ATTESTOR
        account_hash = _sha256(account_reference_sha256, "account_reference_sha256")
        if _account_hash(payload) != account_hash:
            raise ValueError("Captured account id does not match account_reference_sha256")
        observed = _timestamp(observed_at)
        captured = _timestamp(captured_at)
        recorded = _timestamp(recorded_at)
        if observed > captured or captured > recorded:
            raise ValueError("observed_at, captured_at and recorded_at must be chronological")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        payload_hash = hashlib.sha256(payload).hexdigest()
        capture_id = _capture_id(
            payload_hash,
            account_hash,
            observed.isoformat(),
            captured.isoformat(),
            CAPTURE_METHOD,
            attested_by,
        )
        result = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "capture_evidence_id": capture_id,
            "record_type": "PAPER_BROKER_CAPTURE_ADMISSION",
            "status": "CAPTURE_BYTES_RETAINED_UNAUTHENTICATED",
            "capture_kind": CAPTURE_KIND,
            "capture_method": CAPTURE_METHOD,
            "capture_attested_by": attested_by,
            "broker": "Alpaca",
            "broker_environment": "PAPER",
            "account_reference_sha256": account_hash,
            "observed_at": observed.isoformat(),
            "captured_at": captured.isoformat(),
            "recorded_at": recorded.isoformat(),
            "payload_sha256": payload_hash,
            "byte_length": len(payload),
            "blob_relative_path": f"sha256/{payload_hash[:2]}/{payload_hash}.blob",
            **FIXED_FIELDS,
        }
        return self._append(result, payload=payload, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        previous_observed: dict[tuple[str, str], datetime] = {}
        root = self.blob_directory.resolve()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Paper-broker capture {index} was modified.")
            try:
                if set(record) != RECORD_FIELDS:
                    raise ValueError("record field alphabet is invalid")
                payload_hash = _sha256(record.get("payload_sha256"), "payload_sha256")
                account_hash = _sha256(
                    record.get("account_reference_sha256"), "account_reference_sha256"
                )
                observed = _timestamp(record.get("observed_at"))
                captured = _timestamp(record.get("captured_at"))
                recorded = _timestamp(record.get("recorded_at"))
                attested_by = _required(record.get("capture_attested_by"), "capture_attested_by")
                relative = Path(_required(record.get("blob_relative_path"), "blob_relative_path"))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("blob path is unsafe")
                blob_path = self.blob_directory / relative
                resolved_blob = blob_path.resolve()
                if resolved_blob != root / relative:
                    raise ValueError("blob escaped its content root")
                payload = _read_private_file(
                    blob_path,
                    mode=0o400,
                    name="Paper-broker capture blob",
                    maximum_bytes=MAX_CAPTURE_BYTES,
                )
            except (LedgerIntegrityError, OSError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Paper-broker capture {index} has invalid retained evidence."
                ) from error
            expected_path = f"sha256/{payload_hash[:2]}/{payload_hash}.blob"
            expected_id = _capture_id(
                payload_hash,
                account_hash,
                observed.isoformat(),
                captured.isoformat(),
                str(record.get("capture_method") or ""),
                attested_by,
            )
            key = (account_hash, str(record.get("capture_kind") or ""))
            boundary = (
                record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("capture_evidence_id") == expected_id
                and expected_id not in seen_ids
                and record.get("record_type") == "PAPER_BROKER_CAPTURE_ADMISSION"
                and record.get("status") == "CAPTURE_BYTES_RETAINED_UNAUTHENTICATED"
                and record.get("capture_kind") == CAPTURE_KIND
                and record.get("capture_method") == CAPTURE_METHOD
                and record.get("capture_attested_by") == CAPTURE_ATTESTOR
                and record.get("broker") == "Alpaca"
                and record.get("broker_environment") == "PAPER"
                and observed <= captured <= recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("blob_relative_path") == expected_path
                and hashlib.sha256(payload).hexdigest() == payload_hash
                and _account_hash(payload) == account_hash
                and type(record.get("byte_length")) is int
                and record.get("byte_length") == len(payload)
                and all(
                    record.get(field) is value for field, value in FIXED_FIELDS.items()
                )
                and (key not in previous_observed or observed > previous_observed[key])
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Paper-broker capture {index} violates its admission boundary."
                )
            seen_ids.add(expected_id)
            previous_observed[key] = observed
            previous_hash = record["record_hash"]
        return records

    def read_verified(self, capture_evidence_id: str) -> tuple[dict[str, Any], bytes]:
        identifier = _required(capture_evidence_id, "capture_evidence_id")
        record = next(
            (item for item in self.verify() if item["capture_evidence_id"] == identifier),
            None,
        )
        if record is None:
            raise ValueError("Paper-broker capture was not found")
        payload = _read_private_file(
            self.blob_directory / record["blob_relative_path"],
            mode=0o400,
            name="Paper-broker capture blob",
            maximum_bytes=MAX_CAPTURE_BYTES,
        )
        if hashlib.sha256(payload).hexdigest() != record["payload_sha256"]:
            raise LedgerIntegrityError("Paper-broker capture bytes changed after verification.")
        return record, payload

    def _append(
        self, result: dict[str, Any], *, payload: bytes, allow_existing: bool
    ) -> dict[str, Any]:
        _private_directory(self.path.parent)
        _private_directory(self.blob_directory)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        try:
            descriptor = os.open(
                lock_path, os.O_CREAT | os.O_RDWR | _no_follow(), 0o600
            )
        except OSError as error:
            raise LedgerIntegrityError("Paper-broker capture lock is unsafe.") from error
        try:
            _check_regular_descriptor(
                descriptor, mode=0o600, name="Paper-broker capture lock"
            )
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            same_observation = next(
                (
                    item
                    for item in records
                    if item["account_reference_sha256"]
                    == result["account_reference_sha256"]
                    and item["capture_kind"] == result["capture_kind"]
                    and item["observed_at"] == result["observed_at"]
                ),
                None,
            )
            if same_observation is not None:
                if (
                    allow_existing
                    and same_observation["capture_evidence_id"]
                    == result["capture_evidence_id"]
                ):
                    return same_observation
                raise LedgerIntegrityError(
                    "Paper-broker capture conflicts at the same observation time."
                )
            prior = next(
                (
                    item
                    for item in reversed(records)
                    if item["account_reference_sha256"]
                    == result["account_reference_sha256"]
                    and item["capture_kind"] == result["capture_kind"]
                ),
                None,
            )
            if prior is not None and _timestamp(result["observed_at"]) <= _timestamp(
                prior["observed_at"]
            ):
                raise ValueError("observed_at must move forward for each paper account")
            blob_path = self.blob_directory / result["blob_relative_path"]
            _private_directory(self.blob_directory / "sha256")
            _private_directory(blob_path.parent)
            if blob_path.exists():
                try:
                    existing = _read_private_file(
                        blob_path,
                        mode=0o400,
                        name="Paper-broker capture blob",
                        maximum_bytes=MAX_CAPTURE_BYTES,
                    )
                except OSError as error:
                    raise LedgerIntegrityError(
                        "Paper-broker capture blob is unsafe."
                    ) from error
                if existing != payload:
                    raise LedgerIntegrityError("Paper-broker capture blob conflicts with its hash.")
            else:
                temporary, temporary_name = tempfile.mkstemp(
                    prefix=".capture-", suffix=".tmp", dir=blob_path.parent
                )
                try:
                    os.fchmod(temporary, 0o400)
                    _write_all(temporary, payload)
                    os.fsync(temporary)
                    os.close(temporary)
                    temporary = -1
                    try:
                        os.link(temporary_name, blob_path, follow_symlinks=False)
                    except FileExistsError:
                        existing = _read_private_file(
                            blob_path,
                            mode=0o400,
                            name="Paper-broker capture blob",
                            maximum_bytes=MAX_CAPTURE_BYTES,
                        )
                        if existing != payload:
                            raise LedgerIntegrityError(
                                "Paper-broker capture blob conflicts with its hash."
                            )
                    _fsync_directory(blob_path.parent)
                finally:
                    if temporary >= 0:
                        os.close(temporary)
                    Path(temporary_name).unlink(missing_ok=True)
                _fsync_directory(blob_path.parent)
                details = blob_path.stat()
                if details.st_nlink != 1:
                    raise LedgerIntegrityError("Paper-broker capture blob has extra hardlinks.")
            material = {
                **result,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | _no_follow(),
                0o600,
            )
            try:
                _check_regular_descriptor(
                    target, mode=0o600, name="Paper-broker capture ledger"
                )
                _write_all(target, (_canonical_json(record) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            _fsync_directory(self.path.parent)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
