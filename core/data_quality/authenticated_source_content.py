from __future__ import annotations

"""Immutable metadata plus content-addressed source bytes verified on every read."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "content-addressed-authenticated-source-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MEDIA_TYPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
MAX_SOURCE_BYTES = 50 * 1024 * 1024


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete authenticated-source write")
        written += count


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _https_uri(value: Any) -> str:
    resolved = _required(value, "source_uri")
    parsed = urlsplit(resolved)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("source_uri must be a credential-free HTTPS URL")
    return resolved


def _content_id(source_hash: str, source_uri: str, retrieved_at: str) -> str:
    material = [source_hash, source_uri, retrieved_at, POLICY_VERSION]
    return "SRC-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class AuthenticatedSourceContentLedger:
    """Append-only source records whose immutable blob bytes are re-hashed on verify."""

    def __init__(self, path: str | Path, blob_directory: str | Path) -> None:
        self.path = Path(path)
        self.blob_directory = Path(blob_directory)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Authenticated-source ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank authenticated-source line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at authenticated-source line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Authenticated-source line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def ingest(
        self,
        *,
        source_uri: str,
        payload: bytes,
        media_type: str,
        publicly_available_at: str | datetime,
        retrieved_at: str | datetime,
        source_locator: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_SOURCE_BYTES:
            raise ValueError("payload must contain between 1 byte and 50 MiB")
        uri = _https_uri(source_uri)
        media = _required(media_type, "media_type").lower()
        if not MEDIA_TYPE_PATTERN.fullmatch(media):
            raise ValueError("media_type is invalid")
        public = _timestamp(publicly_available_at)
        retrieved = _timestamp(retrieved_at)
        recorded = _timestamp(recorded_at or datetime.now(timezone.utc))
        if public > retrieved or retrieved > recorded:
            raise ValueError("public availability, retrieval and recording must be chronological")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        source_hash = hashlib.sha256(payload).hexdigest()
        content_id = _content_id(source_hash, uri, retrieved.isoformat())
        result = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "content_evidence_id": content_id,
            "record_type": "AUTHENTICATED_CONTENT_ADDRESSED_SOURCE",
            "status": "SOURCE_BYTES_HASH_VERIFIED",
            "recorded_at": recorded.isoformat(),
            "source_uri": uri,
            "publicly_available_at": public.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "media_type": media,
            "byte_length": len(payload),
            "source_input_sha256": source_hash,
            "source_locator": _required(source_locator, "source_locator"),
            "blob_relative_path": f"sha256/{source_hash[:2]}/{source_hash}.blob",
            "source_bytes_authenticated": True,
            "semantic_claim_validated": False,
            "recommendation_provided": False,
            "broker_submission_enabled": False,
            "live_trading_enabled": False,
        }
        return self._append(result, payload=payload, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        records = self.records()
        root = self.blob_directory.resolve()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Authenticated-source record {index} was modified.")
            try:
                source_hash = str(record.get("source_input_sha256") or "").lower()
                if not SHA256_PATTERN.fullmatch(source_hash):
                    raise ValueError("invalid hash")
                uri = _https_uri(record.get("source_uri"))
                public = _timestamp(record.get("publicly_available_at"))
                retrieved = _timestamp(record.get("retrieved_at"))
                recorded = _timestamp(record.get("recorded_at"))
                relative = Path(_required(record.get("blob_relative_path"), "blob path"))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("unsafe blob path")
                blob_path = self.blob_directory / relative
                if blob_path.is_symlink() or not blob_path.is_file():
                    raise ValueError("blob is missing or unsafe")
                resolved_blob = blob_path.resolve()
                if resolved_blob != root / relative:
                    raise ValueError("blob escaped its content root")
                payload = blob_path.read_bytes()
            except (OSError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Authenticated-source record {index} has invalid content evidence."
                ) from error
            expected_id = _content_id(source_hash, uri, retrieved.isoformat())
            expected_path = f"sha256/{source_hash[:2]}/{source_hash}.blob"
            boundary = (
                record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("content_evidence_id") == expected_id
                and expected_id not in seen_ids
                and record.get("record_type") == "AUTHENTICATED_CONTENT_ADDRESSED_SOURCE"
                and record.get("status") == "SOURCE_BYTES_HASH_VERIFIED"
                and record.get("blob_relative_path") == expected_path
                and hashlib.sha256(payload).hexdigest() == source_hash
                and record.get("byte_length") == len(payload)
                and MEDIA_TYPE_PATTERN.fullmatch(str(record.get("media_type") or "")) is not None
                and public <= retrieved <= recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("source_bytes_authenticated") is True
                and all(record.get(field) is False for field in (
                    "semantic_claim_validated", "recommendation_provided",
                    "broker_submission_enabled", "live_trading_enabled",
                ))
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Authenticated-source record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            previous_hash = record["record_hash"]
        return records

    def _append(
        self, result: dict[str, Any], *, payload: bytes, allow_existing: bool
    ) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.blob_directory.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["content_evidence_id"] == result["content_evidence_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in result.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError("Authenticated source content already exists.")
            blob_path = self.blob_directory / result["blob_relative_path"]
            blob_path.parent.mkdir(parents=True, exist_ok=True)
            if blob_path.exists():
                if blob_path.is_symlink() or blob_path.read_bytes() != payload:
                    raise LedgerIntegrityError("Content-addressed blob conflicts with its hash.")
            else:
                target = os.open(blob_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
                try:
                    _write_all(target, payload)
                    os.fsync(target)
                finally:
                    os.close(target)
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
