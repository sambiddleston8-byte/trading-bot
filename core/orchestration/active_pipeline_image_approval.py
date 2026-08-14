from __future__ import annotations

"""Immutable approval of one pinned image for isolated active-pipeline simulation.

Recording an approval is inert bookkeeping: this module never builds, pulls,
pushes, runs or inspects a container image, and it grants no promotion,
deployment, broker, order or live-trading authority.
"""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "active-pipeline-simulation-image-approval-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
REQUIRED_ENVIRONMENT = "CONTAINER_ISOLATED_NO_NETWORK"
REQUIRED_ENTRYPOINT = "/runner/experiment"
MAXIMUM_IMAGE_REFERENCE_LENGTH = 300
_COMPONENT = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
IMAGE_REFERENCE_PATTERN = re.compile(
    rf"^{_COMPONENT}(?:/{_COMPONENT})+@sha256:[0-9a-f]{{64}}$"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
PINNED_ARTIFACT_FIELDS = (
    "dependency_lock_sha256",
    "runner_entrypoint_source_sha256",
    "dockerfile_sha256",
    "build_provenance_sha256",
    "security_review_evidence_sha256",
)
IDENTITY_FIELDS = (
    "image_reference",
    "image_digest_sha256",
    "git_revision",
    *PINNED_ARTIFACT_FIELDS,
    "entrypoint",
    "execution_environment",
    "built_at",
    "built_by",
    "reviewed_at",
    "reviewed_by",
    "approved_at",
)
FIXED_FALSE = (
    "network_allowed",
    "provider_api_allowed",
    "broker_connection_allowed",
    "promotion_approved",
    "deployment_performed",
    "order_submitted",
    "live_trading_enabled",
)


def _required(value: Any, name: str, maximum: int = 100) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").strip().lower()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _image_reference(value: Any) -> str:
    resolved = str(value or "").strip()
    if len(resolved) > MAXIMUM_IMAGE_REFERENCE_LENGTH:
        raise ValueError(
            f"image_reference exceeds {MAXIMUM_IMAGE_REFERENCE_LENGTH} characters"
        )
    if not IMAGE_REFERENCE_PATTERN.fullmatch(resolved):
        raise ValueError(
            "image_reference must pin repository/name@sha256:<64 hexadecimal characters>"
        )
    return resolved


def _identity(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact pinned material, rejecting anything unverifiable."""

    reference = _image_reference(values.get("image_reference"))
    revision = str(values.get("git_revision") or "").strip().lower()
    if not GIT_REVISION_PATTERN.fullmatch(revision):
        raise ValueError("git_revision must be a full hexadecimal commit identifier")
    entrypoint = str(values.get("entrypoint") or "").strip()
    if entrypoint != REQUIRED_ENTRYPOINT:
        raise ValueError(f"entrypoint must be exactly {REQUIRED_ENTRYPOINT}")
    environment = _required(values.get("execution_environment"), "execution_environment", 80).upper()
    if environment != REQUIRED_ENVIRONMENT:
        raise ValueError(f"execution_environment must be {REQUIRED_ENVIRONMENT}")
    artifacts = {field: _sha256(values.get(field), field) for field in PINNED_ARTIFACT_FIELDS}
    if len(set(artifacts.values())) != len(artifacts):
        raise ValueError("each pinned artifact hash must be a distinct SHA-256 digest")
    built_by = _required(values.get("built_by"), "built_by")
    reviewed_by = _required(values.get("reviewed_by"), "reviewed_by")
    if built_by.casefold() == reviewed_by.casefold():
        raise ValueError("the builder cannot be the security reviewer of the same image")
    built = _timestamp(values.get("built_at"))
    reviewed = _timestamp(values.get("reviewed_at"))
    approved = _timestamp(values.get("approved_at"))
    if not built <= reviewed <= approved:
        raise ValueError("build, security review and approval must be chronological")
    if approved > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
        raise ValueError("approval timestamps cannot be in the future")
    return {
        "image_reference": reference,
        "image_digest_sha256": reference.rsplit(":", 1)[-1],
        "git_revision": revision,
        **artifacts,
        "entrypoint": entrypoint,
        "execution_environment": environment,
        "built_at": built.isoformat(),
        "built_by": built_by,
        "reviewed_at": reviewed.isoformat(),
        "reviewed_by": reviewed_by,
        "approved_at": approved.isoformat(),
    }


def _approval_id(identity: Mapping[str, Any]) -> str:
    material = [{field: identity[field] for field in IDENTITY_FIELDS}, POLICY_VERSION]
    return "IMGAPP-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class ActivePipelineImageApprovalLedger:
    """Append-only approvals of pinned simulation images; recording runs nothing."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Image-approval ledger has an incomplete final line.")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank image-approval line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at image-approval line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Image-approval line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def approve(
        self,
        *,
        image_reference: str,
        git_revision: str,
        dependency_lock_sha256: str,
        runner_entrypoint_source_sha256: str,
        dockerfile_sha256: str,
        build_provenance_sha256: str,
        security_review_evidence_sha256: str,
        built_by: str,
        built_at: str | datetime,
        reviewed_by: str,
        reviewed_at: str | datetime,
        entrypoint: str = REQUIRED_ENTRYPOINT,
        execution_environment: str = REQUIRED_ENVIRONMENT,
        approved_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        approved = _timestamp(approved_at or datetime.now(timezone.utc))
        now = datetime.now(timezone.utc)
        if not now - MAX_CLOCK_SKEW <= approved <= now + MAX_CLOCK_SKEW:
            raise ValueError("approved_at must match the actual append time")
        identity = _identity(
            {
                "image_reference": image_reference,
                "git_revision": git_revision,
                "dependency_lock_sha256": dependency_lock_sha256,
                "runner_entrypoint_source_sha256": runner_entrypoint_source_sha256,
                "dockerfile_sha256": dockerfile_sha256,
                "build_provenance_sha256": build_provenance_sha256,
                "security_review_evidence_sha256": security_review_evidence_sha256,
                "entrypoint": entrypoint,
                "execution_environment": execution_environment,
                "built_at": built_at,
                "built_by": built_by,
                "reviewed_at": reviewed_at,
                "reviewed_by": reviewed_by,
                "approved_at": approved,
            }
        )
        result = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "image_approval_id": _approval_id(identity),
            "record_type": "ACTIVE_PIPELINE_SIMULATION_IMAGE_APPROVAL",
            "status": "APPROVED_FOR_LOCAL_ISOLATED_SIMULATION_ONLY",
            "simulation_only": True,
            **identity,
            **{field: False for field in FIXED_FALSE},
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        seen_images: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Image-approval record {index} has been modified.")
            try:
                identity = _identity(record)
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Image-approval record {index} has invalid values."
                ) from error
            expected_id = _approval_id(identity)
            boundary = (
                record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("image_approval_id") == expected_id
                and expected_id not in seen_ids
                and identity["image_reference"] not in seen_images
                and identity["image_digest_sha256"] not in seen_images
                and record.get("record_type") == "ACTIVE_PIPELINE_SIMULATION_IMAGE_APPROVAL"
                and record.get("status") == "APPROVED_FOR_LOCAL_ISOLATED_SIMULATION_ONLY"
                and record.get("simulation_only") is True
                and all(record.get(field) == value for field, value in identity.items())
                and all(record.get(field) is False for field in FIXED_FALSE)
            )
            if not boundary:
                raise LedgerIntegrityError(f"Image-approval record {index} violates its boundary.")
            seen_ids.add(expected_id)
            seen_images.add(identity["image_reference"])
            seen_images.add(identity["image_digest_sha256"])
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (
                    item for item in records
                    if item["image_approval_id"] == result["image_approval_id"]
                ),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in result.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError("This image approval already exists.")
            if any(
                item["image_reference"] == result["image_reference"]
                or item["image_digest_sha256"] == result["image_digest_sha256"]
                for item in records
            ):
                raise LedgerIntegrityError(
                    "This image already has its single immutable approval."
                )
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
