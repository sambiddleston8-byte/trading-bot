"""Owner-only, byte-bound public-documentation evidence for Campaign v2 R2.

This module persists exact public Massive documentation bytes and a single
immutable evidence record.  Public documentation remains provider-level
evidence only: it cannot bind an account, prove a subscriber plan, authorize a
provider request, admit data, run an evaluation, or enable broker activity.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Mapping, Sequence

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.campaign_v2_revision_2_preregistration import (
    CampaignV2Revision2PreregistrationLedger,
)
from core.orchestration.massive_entitlement_evidence import (
    PUBLIC_DOCUMENT_CONTRACTS,
    build_public_documentation_evidence,
)


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "massive-campaign-v2-r2-public-documentation-v1"
EVENT_TYPE = "CAMPAIGN_V2_REVISION_2_PUBLIC_DOCUMENTATION_BOUND"
PREREGISTRATION_ID = "HQP2R2-4E0CA212575663AA7010D87EA36B58E5"
PREREGISTRATION_RECORD_SHA256 = (
    "48554e1ebdce8d9641362d1d9843c27bb8d07de2413db72c4b074166fcc70743"
)
PROPOSAL_SHA256 = (
    "cafbaef235d8379e29b17d057ac87a77c452260680afd20bf8c7e4fd24671654"
)
CONTROL_LEDGER_RELATIVE_PATH = Path(
    "data/research/massive_campaign_v2_revision_2/public_documentation/evidence.jsonl"
)
BLOB_ROOT_RELATIVE_PATH = Path(
    "data/research/massive_campaign_v2_revision_2/public_documentation/blobs"
)
MAX_LEDGER_BYTES = 4 * 1024 * 1024
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
FIXED_FALSE = (
    "authenticated_account_evidence_collected",
    "account_identity_proven",
    "account_plan_proven",
    "current_account_entitlement_proven",
    "api_key_request_allowed",
    "credential_material_recorded",
    "capture_activation_issued",
    "data_calls_allowed",
    "provider_market_data_bytes_accessed",
    "dataset_admitted",
    "evaluation_allowed",
    "broker_connection_allowed",
    "orders_submitted",
    "live_trading_enabled",
)
_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "event_type",
        "recorded_at",
        "evidence_id",
        "preregistration_id",
        "preregistration_record_sha256",
        "proposal_sha256",
        "public_documentation_evidence",
        "blob_references",
        *FIXED_FALSE,
        "previous_hash",
        "record_hash",
    }
)
_BLOB_REFERENCE_FIELDS = frozenset(
    {"document_id", "payload_sha256", "byte_length", "blob_relative_path"}
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _timestamp(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _no_follow() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Public-documentation append made no progress")
        offset += count


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    details = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid():
        raise LedgerIntegrityError("Public-documentation directory is unsafe")
    os.chmod(path, 0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise LedgerIntegrityError("Public-documentation directory must be owner-only")


def _assert_private_directory(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError as error:
        raise LedgerIntegrityError(
            "Public-documentation directory is missing or unsafe"
        ) from error
    if (
        path.is_symlink()
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise LedgerIntegrityError(
            "Public-documentation directory is missing or unsafe"
        )


def _evidence_id(evidence_sha256: str) -> str:
    return "CV2R2DOC-" + _hash(
        [
            PREREGISTRATION_ID,
            PREREGISTRATION_RECORD_SHA256,
            evidence_sha256,
            POLICY_VERSION,
        ]
    )[:32].upper()


class CampaignV2Revision2PublicDocumentationLedger:
    """Persist and reverify exactly one inert public-documentation bundle."""

    def __init__(
        self,
        path: str | Path,
        blob_root: str | Path,
        *,
        repository_root: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
        parent_resolver: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self.path = Path(path)
        self.blob_root = Path(blob_root)
        self.repository_root = Path(repository_root or Path.cwd()).resolve()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._parent_resolver = parent_resolver or self._default_parent_resolver

    @classmethod
    def at_repository_root(
        cls, repository_root: str | Path
    ) -> "CampaignV2Revision2PublicDocumentationLedger":
        root = Path(repository_root).resolve()
        return cls(
            root / CONTROL_LEDGER_RELATIVE_PATH,
            root / BLOB_ROOT_RELATIVE_PATH,
            repository_root=root,
        )

    def _default_parent_resolver(self) -> Mapping[str, Any]:
        ledger = CampaignV2Revision2PreregistrationLedger(
            self.repository_root
            / "data/research/massive_campaign_v2_revision_2/preregistration.jsonl",
            repository_root=self.repository_root,
        )
        return ledger.require(PREREGISTRATION_ID)

    def _verified_parent(self) -> Mapping[str, Any]:
        parent = self._parent_resolver()
        if (
            parent.get("preregistration_id") != PREREGISTRATION_ID
            or parent.get("record_hash") != PREREGISTRATION_RECORD_SHA256
            or parent.get("proposal_sha256") != PROPOSAL_SHA256
            or parent.get("public_documentation_evidence_collected") is not False
            or parent.get("data_calls_allowed") is not False
            or parent.get("evaluation_allowed") is not False
            or parent.get("orders_submitted") is not False
        ):
            raise LedgerIntegrityError(
                "Public-documentation evidence parent preregistration is invalid"
            )
        return parent

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        _assert_private_directory(self.path.parent)
        descriptor = os.open(self.path, os.O_RDONLY | _no_follow())
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or stat.S_IMODE(details.st_mode) != 0o600
                or details.st_uid != os.geteuid()
                or details.st_nlink != 1
                or details.st_size > MAX_LEDGER_BYTES
            ):
                raise LedgerIntegrityError("Public-documentation ledger is unsafe")
            raw = b""
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                raw += chunk
        finally:
            os.close(descriptor)
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Public-documentation ledger has an incomplete final line"
            )
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), 1):
            try:
                row = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise LedgerIntegrityError(
                    f"Public-documentation line {line_number} is invalid"
                ) from error
            if not isinstance(row, dict):
                raise LedgerIntegrityError(
                    f"Public-documentation line {line_number} is not an object"
                )
            rows.append(row)
        self._verify(rows)
        return rows

    def _verified_blob(self, reference: Mapping[str, Any]) -> bytes:
        if set(reference) != _BLOB_REFERENCE_FIELDS:
            raise LedgerIntegrityError("Public-documentation blob reference changed")
        payload_sha256 = str(reference.get("payload_sha256", ""))
        if SHA256_PATTERN.fullmatch(payload_sha256) is None:
            raise LedgerIntegrityError("Public-documentation blob hash is invalid")
        expected_relative = Path("sha256") / payload_sha256[:2] / f"{payload_sha256}.blob"
        if reference.get("blob_relative_path") != expected_relative.as_posix():
            raise LedgerIntegrityError("Public-documentation blob path changed")
        path = self.blob_root / expected_relative
        try:
            _assert_private_directory(self.blob_root)
            _assert_private_directory(path.parent.parent)
            _assert_private_directory(path.parent)
            details = path.lstat()
            root = self.blob_root.resolve()
            if (
                path.is_symlink()
                or not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.geteuid()
                or details.st_nlink != 1
                or stat.S_IMODE(details.st_mode) != 0o400
                or path.resolve() != root / expected_relative
            ):
                raise ValueError("unsafe blob")
            payload = path.read_bytes()
        except (OSError, ValueError) as error:
            raise LedgerIntegrityError(
                "Public-documentation blob is missing or unsafe"
            ) from error
        if (
            len(payload) != reference.get("byte_length")
            or hashlib.sha256(payload).hexdigest() != payload_sha256
        ):
            raise LedgerIntegrityError("Public-documentation blob bytes changed")
        return payload

    def _verify(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if len(rows) > 1:
            raise LedgerIntegrityError(
                "Campaign v2 revision-2 permits one documentation evidence record"
            )
        if not rows:
            return
        self._verified_parent()
        row = rows[0]
        try:
            evidence = row.get("public_documentation_evidence")
            references = row.get("blob_references")
            if not isinstance(evidence, dict) or not isinstance(references, list):
                raise ValueError("evidence shape")
            recorded = _timestamp(row.get("recorded_at"), "recorded_at")
            assessed = _timestamp(evidence.get("assessed_at"), "assessed_at")
            documents_by_id = {
                item["document_id"]: item for item in evidence.get("documents", [])
            }
            inputs = []
            seen: set[str] = set()
            for reference in references:
                document_id = reference.get("document_id")
                if document_id in seen or document_id not in documents_by_id:
                    raise ValueError("duplicate or unsupported document")
                seen.add(document_id)
                document = documents_by_id[document_id]
                payload = self._verified_blob(reference)
                inputs.append(
                    {
                        "document_id": document_id,
                        "uri": document["uri"],
                        "retrieved_at": document["retrieved_at"],
                        "payload_bytes": payload,
                    }
                )
            rebuilt = build_public_documentation_evidence(inputs, assessed_at=assessed)
            material = {key: value for key, value in row.items() if key != "record_hash"}
            valid = (
                set(row) == _RECORD_FIELDS
                and row.get("schema_version") == SCHEMA_VERSION
                and row.get("policy_version") == POLICY_VERSION
                and row.get("event_type") == EVENT_TYPE
                and row.get("previous_hash") == GENESIS_HASH
                and row.get("record_hash") == _hash(material)
                and row.get("preregistration_id") == PREREGISTRATION_ID
                and row.get("preregistration_record_sha256")
                == PREREGISTRATION_RECORD_SHA256
                and row.get("proposal_sha256") == PROPOSAL_SHA256
                and evidence == rebuilt
                and len(references) == len(PUBLIC_DOCUMENT_CONTRACTS)
                and seen == set(PUBLIC_DOCUMENT_CONTRACTS)
                and assessed <= recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and row.get("evidence_id")
                == _evidence_id(evidence["evidence_bundle_sha256"])
                and all(row.get(field) is False for field in FIXED_FALSE)
            )
        except (KeyError, TypeError, ValueError) as error:
            raise LedgerIntegrityError(
                "Public-documentation evidence record is invalid"
            ) from error
        if not valid:
            raise LedgerIntegrityError(
                "Public-documentation evidence violates its inert boundary"
            )

    def register(
        self,
        documents: Sequence[Mapping[str, Any]],
        *,
        assessed_at: Any,
    ) -> dict[str, Any]:
        self._verified_parent()
        evidence = build_public_documentation_evidence(
            documents,
            assessed_at=assessed_at,
        )
        recorded = _timestamp(self._clock(), "recording clock")
        actual_now = datetime.now(timezone.utc)
        assessed = _timestamp(evidence["assessed_at"], "assessed_at")
        if not actual_now - MAX_CLOCK_SKEW <= recorded <= actual_now + MAX_CLOCK_SKEW:
            raise ValueError("recording clock must match actual append time")
        if assessed > recorded:
            raise ValueError("assessment must not occur after recording")
        evidence_documents = {
            item["document_id"]: item for item in evidence["documents"]
        }
        payloads = {
            str(item["document_id"]): item["payload_bytes"] for item in documents
        }
        if set(payloads) != set(PUBLIC_DOCUMENT_CONTRACTS):
            raise ValueError("all exact public documentation payloads are required")
        references = []
        for document_id in sorted(payloads):
            payload = payloads[document_id]
            payload_sha256 = evidence_documents[document_id]["payload_sha256"]
            references.append(
                {
                    "document_id": document_id,
                    "payload_sha256": payload_sha256,
                    "byte_length": len(payload),
                    "blob_relative_path": (
                        Path("sha256")
                        / payload_sha256[:2]
                        / f"{payload_sha256}.blob"
                    ).as_posix(),
                }
            )
        result = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "event_type": EVENT_TYPE,
            "recorded_at": recorded.isoformat(timespec="microseconds"),
            "evidence_id": _evidence_id(evidence["evidence_bundle_sha256"]),
            "preregistration_id": PREREGISTRATION_ID,
            "preregistration_record_sha256": PREREGISTRATION_RECORD_SHA256,
            "proposal_sha256": PROPOSAL_SHA256,
            "public_documentation_evidence": evidence,
            "blob_references": references,
            **{field: False for field in FIXED_FALSE},
            "previous_hash": GENESIS_HASH,
        }
        result["record_hash"] = _hash(result)
        return self._append(result, payloads)

    def _append(
        self, result: dict[str, Any], payloads: Mapping[str, bytes]
    ) -> dict[str, Any]:
        _private_directory(self.path.parent)
        _private_directory(self.blob_root)
        _private_directory(self.blob_root / "sha256")
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock = os.open(lock_path, os.O_CREAT | os.O_RDWR | _no_follow(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            os.fchmod(lock, 0o600)
            existing = self.records()
            if existing:
                if (
                    existing[0]["evidence_id"] == result["evidence_id"]
                    and existing[0]["public_documentation_evidence"]
                    == result["public_documentation_evidence"]
                ):
                    return existing[0]
                raise LedgerIntegrityError(
                    "A different public-documentation bundle is already registered"
                )
            for reference in result["blob_references"]:
                payload = payloads[reference["document_id"]]
                path = self.blob_root / reference["blob_relative_path"]
                _private_directory(path.parent)
                if path.exists():
                    if path.is_symlink() or path.read_bytes() != payload:
                        raise LedgerIntegrityError(
                            "Public-documentation blob conflicts with its hash"
                        )
                    os.chmod(path, 0o400)
                    continue
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _no_follow(),
                    0o400,
                )
                try:
                    _write_all(descriptor, payload)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _no_follow(),
                0o600,
            )
            try:
                _write_all(
                    descriptor,
                    (_canonical_json(result) + "\n").encode("utf-8"),
                )
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return self.records()[0]
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)

    def require(self, evidence_id: str) -> dict[str, Any]:
        records = self.records()
        if len(records) != 1 or records[0].get("evidence_id") != evidence_id:
            raise LedgerIntegrityError(
                "Required Campaign v2 revision-2 public evidence is unavailable"
            )
        return records[0]
