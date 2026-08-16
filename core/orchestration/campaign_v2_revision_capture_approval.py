from __future__ import annotations

"""Immutable approval evidence for the Campaign v2 revision-1 capture proposal.

This ledger records the exact hash-bound human approval.  It deliberately has
no activation, credential, provider, quarantine, replay or broker capability.
Authenticated entitlement evidence and a separate activation record are still
required before any data call can become authorized.
"""

from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Mapping, Sequence

from core.decision_ledger import (
    GENESIS_HASH,
    LedgerIntegrityError,
    canonical_timestamp,
    current_git_revision,
)
from core.orchestration.campaign_v2_revision_preregistration import (
    CONTROL_LEDGER_RELATIVE_PATH as PREREGISTRATION_LEDGER_RELATIVE_PATH,
    CampaignV2RevisionPreregistrationLedger,
)
from core.orchestration.historical_quarantine_preregistration import (
    _git_worktree_clean,
    _strategy_source_identity,
)
from core.research.campaign_v2_revision_capture_activation_proposal import (
    PARENT_PREREGISTRATION_ID,
    PARENT_PREREGISTRATION_RECORD_SHA256,
    activation_proposal,
    required_approval_text,
)


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "massive-capture-proposal-approval-v2-revision-1"
EVENT_TYPE = "CAMPAIGN_V2_REVISION_1_CAPTURE_PROPOSAL_APPROVED"
CONTROL_LEDGER_RELATIVE_PATH = Path(
    "data/research/massive_campaign_v2_revision_1/capture_approval.jsonl"
)
MAX_LEDGER_BYTES = 1024 * 1024
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
FIXED_FALSE = (
    "credential_material_recorded",
    "entitlement_evidence_collected",
    "entitlement_revalidated",
    "capture_activation_issued",
    "data_calls_allowed",
    "provider_bytes_accessed",
    "train_validation_opened",
    "untouched_test_opened",
    "dataset_admitted",
    "parameter_search_executed",
    "evaluation_allowed",
    "guardrailed_replay_executed",
    "performance_claim_allowed",
    "broker_connection_allowed",
    "orders_submitted",
    "live_trading_enabled",
)
_ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "event_type",
        "recorded_at",
        "payload",
        "previous_hash",
        "record_hash",
    }
)
_PAYLOAD_FIELDS = frozenset(
    {
        "record_type",
        "status",
        "approved_by",
        "authorization_basis",
        "approval_text",
        "approval_scope",
        "proposal_sha256",
        "proposal_module_sha256",
        "parent_preregistration_id",
        "parent_preregistration_record_sha256",
        "parent_preregistration_policy_version",
        "git_revision",
        "git_worktree_clean",
        "strategy_source_path",
        "strategy_source_sha256",
        "target_basket",
        "authorized_splits",
        "authorized_request_slices",
        "authorized_request_count",
        "sealed_split",
        "sealed_split_semantic_role",
        "current_real_world_date_at_recording",
        "all_partition_ends_on_or_before_recording_date",
        "entitlement_evidence_status",
        "next_required_boundary",
        "approval_record_id",
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


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required(value: Any, name: str, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    resolved = value.strip()
    if not resolved or resolved != value or len(resolved) > maximum:
        raise ValueError(f"{name} must be nonempty canonical text")
    if any(ord(character) < 32 for character in resolved):
        raise ValueError(f"{name} must not contain control characters")
    return resolved


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
            raise OSError("capture approval append made no progress")
        offset += count


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    details = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
    ):
        raise LedgerIntegrityError("capture approval directory is unsafe")
    os.chmod(path, 0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise LedgerIntegrityError("capture approval directory must be owner-only")


def _approval_record_id(payload: Mapping[str, Any]) -> str:
    material = {key: value for key, value in payload.items() if key != "approval_record_id"}
    return "CV2R1CAP-" + _hash([material, POLICY_VERSION])[:32].upper()


class CampaignV2RevisionCaptureApprovalLedger:
    """One-record owner-only approval chain with every execution authority false."""

    def __init__(
        self,
        path: Path,
        *,
        repository_root: Path,
        clock: Callable[[], datetime] | None = None,
        git_revision_resolver: Callable[[Path], str] = current_git_revision,
        worktree_clean_resolver: Callable[[Path], bool] = _git_worktree_clean,
        parent_resolver: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self.path = path.expanduser().absolute()
        self.repository_root = repository_root.expanduser().resolve()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._git_revision_resolver = git_revision_resolver
        self._worktree_clean_resolver = worktree_clean_resolver
        self._parent_resolver = parent_resolver or self._default_parent

    def _default_parent(self) -> Mapping[str, Any]:
        ledger = CampaignV2RevisionPreregistrationLedger(
            self.repository_root / PREREGISTRATION_LEDGER_RELATIVE_PATH,
            repository_root=self.repository_root,
        )
        return ledger.require(PARENT_PREREGISTRATION_ID)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
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
                raise LedgerIntegrityError("capture approval ledger is unsafe")
            raw = os.read(descriptor, MAX_LEDGER_BYTES + 1)
        finally:
            os.close(descriptor)
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("capture approval ledger has an incomplete line")
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), 1):
            try:
                row = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise LedgerIntegrityError(
                    f"capture approval line {line_number} is invalid"
                ) from error
            if not isinstance(row, dict):
                raise LedgerIntegrityError(
                    f"capture approval line {line_number} is not an object"
                )
            rows.append(row)
        self._verify(rows)
        return rows

    def _verify(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if len(rows) > 1:
            raise LedgerIntegrityError("capture approval permits exactly one record")
        if not rows:
            return
        row = rows[0]
        try:
            recorded = _timestamp(row.get("recorded_at"), "recorded_at")
            payload = row.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("payload")
            recording_date = date.fromisoformat(
                _required(
                    payload.get("current_real_world_date_at_recording"),
                    "current_real_world_date_at_recording",
                    10,
                )
            )
            proposal = activation_proposal(
                current_real_world_date=recording_date
            )
            parent = dict(self._parent_resolver())
            material = {key: value for key, value in row.items() if key != "record_hash"}
            boundary = (
                set(row) == _ENVELOPE_FIELDS
                and set(payload) == _PAYLOAD_FIELDS
                and row.get("schema_version") == SCHEMA_VERSION
                and row.get("policy_version") == POLICY_VERSION
                and row.get("event_type") == EVENT_TYPE
                and row.get("previous_hash") == GENESIS_HASH
                and row.get("record_hash") == _hash(material)
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and payload.get("record_type")
                == "CAMPAIGN_V2_REVISION_1_CAPTURE_PROPOSAL_APPROVAL"
                and payload.get("status")
                == "APPROVED_ENTITLEMENT_AND_ACTIVATION_PENDING"
                and bool(payload.get("approved_by"))
                and payload.get("authorization_basis")
                == "EXACT_USER_APPROVAL_IN_CODEX_TASK"
                and payload.get("approval_text") == required_approval_text()
                and payload.get("approval_scope")
                == "TRAIN_VALIDATION_CAPTURE_PROPOSAL_ONLY_NO_DATA_CALL"
                and payload.get("proposal_sha256") == proposal["proposal_sha256"]
                and payload.get("parent_preregistration_id")
                == PARENT_PREREGISTRATION_ID
                and payload.get("parent_preregistration_record_sha256")
                == PARENT_PREREGISTRATION_RECORD_SHA256
                and parent.get("preregistration_id") == PARENT_PREREGISTRATION_ID
                and parent.get("record_hash")
                == PARENT_PREREGISTRATION_RECORD_SHA256
                and payload.get("parent_preregistration_policy_version")
                == parent.get("policy_version")
                and payload.get("git_worktree_clean") is True
                and GIT_REVISION_PATTERN.fullmatch(
                    str(payload.get("git_revision", ""))
                )
                is not None
                and SHA256_PATTERN.fullmatch(
                    str(payload.get("proposal_module_sha256", ""))
                )
                is not None
                and SHA256_PATTERN.fullmatch(
                    str(payload.get("strategy_source_sha256", ""))
                )
                is not None
                and payload.get("target_basket") == proposal["target_basket"]
                and payload.get("authorized_splits")
                == proposal["authorized_splits"]
                and payload.get("authorized_request_slices")
                == proposal["authorized_request_slices"]
                and payload.get("authorized_request_count") == 27
                and payload.get("sealed_split") == proposal["sealed_split"]
                and payload.get("sealed_split_semantic_role")
                == "SEALED_RETROSPECTIVE_TEST"
                and payload.get("all_partition_ends_on_or_before_recording_date")
                is True
                and recording_date <= date.today()
                and abs((recording_date - recorded.date()).days) <= 1
                and payload.get("entitlement_evidence_status") == "NOT_COLLECTED"
                and payload.get("next_required_boundary")
                == "AUTHENTICATED_ACCOUNT_BOUND_ENTITLEMENT_REVALIDATION"
                and payload.get("approval_record_id")
                == _approval_record_id(payload)
                and all(payload.get(name) is False for name in FIXED_FALSE)
            )
        except (KeyError, TypeError, ValueError) as error:
            raise LedgerIntegrityError("capture approval record is invalid") from error
        if not boundary:
            raise LedgerIntegrityError("capture approval violates its inert boundary")

    def record_approval(
        self, *, approved_by: str, approval_text: str
    ) -> dict[str, Any]:
        actor = _required(approved_by, "approved_by", 150)
        if approval_text != required_approval_text():
            raise ValueError("approval_text does not match the capture proposal hash")
        now = _timestamp(self._clock(), "approval clock")
        actual_now = datetime.now(timezone.utc)
        if not actual_now - MAX_CLOCK_SKEW <= now <= actual_now + MAX_CLOCK_SKEW:
            raise ValueError("approval clock must match actual append time")
        existing = self.records()
        if existing:
            if existing[0]["payload"].get("approved_by") != actor:
                raise LedgerIntegrityError("capture approval authority differs")
            return self.require(existing[0]["payload"]["approval_record_id"])
        if self._worktree_clean_resolver(self.repository_root) is not True:
            raise ValueError("Git worktree must be clean before recording approval")
        revision = _required(
            self._git_revision_resolver(self.repository_root), "git_revision", 64
        ).lower()
        if GIT_REVISION_PATTERN.fullmatch(revision) is None:
            raise ValueError("git_revision must be a full lowercase Git commit ID")
        parent = dict(self._parent_resolver())
        if (
            parent.get("preregistration_id") != PARENT_PREREGISTRATION_ID
            or parent.get("record_hash") != PARENT_PREREGISTRATION_RECORD_SHA256
        ):
            raise LedgerIntegrityError("capture approval parent preregistration differs")
        recording_date = now.date()
        proposal = activation_proposal(current_real_world_date=recording_date)
        proposal_source = (
            self.repository_root
            / "core/research/campaign_v2_revision_capture_activation_proposal.py"
        )
        strategy_path, strategy_hash = _strategy_source_identity(
            self.repository_root,
            "core/research/conservative_baseline_strategy.py",
        )
        payload: dict[str, Any] = {
            "record_type": "CAMPAIGN_V2_REVISION_1_CAPTURE_PROPOSAL_APPROVAL",
            "status": "APPROVED_ENTITLEMENT_AND_ACTIVATION_PENDING",
            "approved_by": actor,
            "authorization_basis": "EXACT_USER_APPROVAL_IN_CODEX_TASK",
            "approval_text": required_approval_text(),
            "approval_scope": "TRAIN_VALIDATION_CAPTURE_PROPOSAL_ONLY_NO_DATA_CALL",
            "proposal_sha256": proposal["proposal_sha256"],
            "proposal_module_sha256": hashlib.sha256(
                proposal_source.read_bytes()
            ).hexdigest(),
            "parent_preregistration_id": PARENT_PREREGISTRATION_ID,
            "parent_preregistration_record_sha256": (
                PARENT_PREREGISTRATION_RECORD_SHA256
            ),
            "parent_preregistration_policy_version": parent["policy_version"],
            "git_revision": revision,
            "git_worktree_clean": True,
            "strategy_source_path": strategy_path,
            "strategy_source_sha256": strategy_hash,
            "target_basket": proposal["target_basket"],
            "authorized_splits": proposal["authorized_splits"],
            "authorized_request_slices": proposal["authorized_request_slices"],
            "authorized_request_count": proposal["authorized_request_count"],
            "sealed_split": proposal["sealed_split"],
            "sealed_split_semantic_role": proposal[
                "sealed_split_semantic_role"
            ],
            "current_real_world_date_at_recording": recording_date.isoformat(),
            "all_partition_ends_on_or_before_recording_date": True,
            "entitlement_evidence_status": "NOT_COLLECTED",
            "next_required_boundary": (
                "AUTHENTICATED_ACCOUNT_BOUND_ENTITLEMENT_REVALIDATION"
            ),
            **{name: False for name in FIXED_FALSE},
        }
        payload["approval_record_id"] = _approval_record_id(payload)
        complete = self._append(payload, recorded_at=now)
        return self.require(complete["payload"]["approval_record_id"])

    def require(self, approval_record_id: str) -> dict[str, Any]:
        identifier = _required(approval_record_id, "approval_record_id", 100)
        rows = self.records()
        if len(rows) != 1 or rows[0]["payload"]["approval_record_id"] != identifier:
            raise ValueError("verified capture approval was not found")
        return {
            **rows[0]["payload"],
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "recorded_at": rows[0]["recorded_at"],
            "record_hash": rows[0]["record_hash"],
        }

    def _append(
        self, payload: Mapping[str, Any], *, recorded_at: datetime
    ) -> dict[str, Any]:
        _private_directory(self.path.parent)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock = os.open(lock_path, os.O_CREAT | os.O_RDWR | _no_follow(), 0o600)
        try:
            os.fchmod(lock, 0o600)
            details = os.fstat(lock)
            if (
                not stat.S_ISREG(details.st_mode)
                or stat.S_IMODE(details.st_mode) != 0o600
                or details.st_uid != os.geteuid()
                or details.st_nlink != 1
            ):
                raise LedgerIntegrityError("capture approval lock is unsafe")
            fcntl.flock(lock, fcntl.LOCK_EX)
            existing = self.records()
            if existing:
                if existing[0]["payload"] == dict(payload):
                    return existing[0]
                raise LedgerIntegrityError("capture approval already differs")
            material = {
                "schema_version": SCHEMA_VERSION,
                "policy_version": POLICY_VERSION,
                "event_type": EVENT_TYPE,
                "recorded_at": recorded_at.isoformat(timespec="microseconds"),
                "payload": json.loads(_canonical_json(dict(payload))),
                "previous_hash": GENESIS_HASH,
            }
            complete = {**material, "record_hash": _hash(material)}
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | _no_follow(),
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
                _write_all(
                    descriptor,
                    (_canonical_json(complete) + "\n").encode("utf-8"),
                )
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            directory = os.open(
                self.path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _no_follow(),
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return complete
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
