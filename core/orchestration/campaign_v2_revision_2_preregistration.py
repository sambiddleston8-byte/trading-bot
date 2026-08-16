"""Immutable, inert approval chain for Campaign v2 revision-2.

This module binds the exact human approval to the completed-history proposal,
the clean implementation revision, and the terminal supersession of revision-1.
It has no credential, provider, admission, evaluation, broker, or order path.
"""

from __future__ import annotations

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
from core.orchestration.historical_quarantine_preregistration import (
    _canonical_bounded_json_mapping,
    _git_worktree_clean,
    _strategy_source_identity,
)
from core.research.conservative_baseline_campaign_v2_revision_2_proposal import (
    AUTHORITATIVE_EVALUATOR,
    CAMPAIGN_POLICY_VERSION,
    PROPOSAL_VALID_UNTIL,
    SUPERSEDED_PROPOSAL_SHA256,
    assert_capture_window_current,
    proposal_package,
    required_approval_text,
)


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "massive-stage-0-preregistration-v2-revision-2"
PROPOSAL_SHA256 = (
    "cafbaef235d8379e29b17d057ac87a77c452260680afd20bf8c7e4fd24671654"
)
APPROVAL_TEXT = f"I approve Campaign v2 revision-2 proposal {PROPOSAL_SHA256}"
REVISION_1_PREREGISTRATION_ID = "HQP2R1-BBF7B46BB66E1CC5BB5344DE35A9D9E2"
REVISION_1_PREREGISTRATION_RECORD_SHA256 = (
    "a44f35f269fd49bbd61b8f49899cd22de6d494edba30078f522438ed9ba0499b"
)
REVISION_1_CAPTURE_PROPOSAL_SHA256 = (
    "ac80cd34ccfd9a620daa7d81812261f97a144402e56e7b3eb20825565266d02a"
)
REVISION_1_CAPTURE_APPROVAL_ID = "CV2R1CAP-3072E69774CF1438898644B7ECFB6495"
REVISION_1_CAPTURE_APPROVAL_RECORD_SHA256 = (
    "42431cf0471f9697c6a77f9d7366d671196d89b898d533e01f98d883382267a1"
)
CONTROL_LEDGER_RELATIVE_PATH = Path(
    "data/research/massive_campaign_v2_revision_2/preregistration.jsonl"
)
QUARANTINE_ROOT_RELATIVE_PATH = Path(
    "data/research/massive_campaign_v2_revision_2/raw"
)
REVISION_1_ROOT_RELATIVE_PATH = Path(
    "data/research/massive_campaign_v2_revision_1"
)
KNOWN_ADMITTED_STORE_RELATIVE_PATHS = (
    Path("data/research/massive_research_exempt_admitted"),
)
EVENT_TYPE = "CAMPAIGN_V2_REVISION_2_APPROVED_AND_PREREGISTERED"
MAX_LEDGER_BYTES = 4 * 1024 * 1024
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
FIXED_FALSE = (
    "api_key_request_allowed",
    "credential_material_recorded",
    "public_documentation_evidence_collected",
    "authenticated_account_evidence_collected",
    "entitlement_revalidated",
    "capture_activation_issued",
    "data_calls_allowed",
    "provider_bytes_accessed",
    "train_validation_opened",
    "untouched_test_opened",
    "research_exemption_applied_to_capture",
    "dataset_admitted",
    "parameter_search_executed",
    "evaluation_allowed",
    "guardrailed_replay_executed",
    "performance_claim_allowed",
    "aws_resource_creation_allowed",
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
        "authorized_by",
        "authorization_basis",
        "approval_text",
        "approval_scope",
        "proposal_sha256",
        "proposal_module_sha256",
        "campaign_policy_version",
        "git_revision",
        "git_worktree_clean",
        "current_real_world_date_at_registration",
        "proposal_valid_until",
        "all_partition_ends_on_or_before_registration_date",
        "superseded_proposal_sha256",
        "superseded_revision_1_preregistration_id",
        "superseded_revision_1_preregistration_record_sha256",
        "superseded_revision_1_capture_proposal_sha256",
        "superseded_revision_1_capture_approval_id",
        "superseded_revision_1_capture_approval_record_sha256",
        "superseded_revision_1_status",
        "supersession_reason_codes",
        "target_basket",
        "acquisition_start",
        "acquisition_end",
        "splits",
        "retrospective_test_semantic_role",
        "strategy_source_path",
        "strategy_source_sha256",
        "strategy_version",
        "parameter_space_canonical_json",
        "parameter_space_sha256",
        "authoritative_evaluator",
        "vectorbt_on_critical_path",
        "evaluation_protocol",
        "evaluation_protocol_sha256",
        "capture_scope",
        "capture_scope_sha256",
        "corporate_action_total_return_policy_sha256",
        "research_exemption_extension_status",
        "research_exemption_parent_record_hash",
        "research_exemption_limitations",
        "quarantine_only",
        "capture_chain_binding_required",
        "quarantine_root_relative_path",
        "control_ledger_relative_path",
        "next_required_gates",
        "preregistration_id",
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
            raise OSError("Campaign v2 revision-2 append made no progress")
        offset += count


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    details = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid():
        raise LedgerIntegrityError("Campaign v2 revision-2 directory is unsafe")
    os.chmod(path, 0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise LedgerIntegrityError("Campaign v2 revision-2 directory must be owner-only")


def _fixed_false() -> dict[str, bool]:
    return {name: False for name in FIXED_FALSE}


def _preregistration_id(payload: Mapping[str, Any]) -> str:
    material = {key: value for key, value in payload.items() if key != "preregistration_id"}
    return "HQP2R2-" + _hash([material, POLICY_VERSION])[:32].upper()


def _verified_proposal(*, current_real_world_date: date) -> dict[str, Any]:
    proposal = proposal_package()
    if (
        proposal.get("proposal_sha256") != PROPOSAL_SHA256
        or required_approval_text() != APPROVAL_TEXT
        or proposal.get("approval_status") != "PENDING_EXPLICIT_HUMAN_APPROVAL"
        or proposal.get("campaign_policy_version") != CAMPAIGN_POLICY_VERSION
        or proposal.get("supersedes_proposal_sha256") != SUPERSEDED_PROPOSAL_SHA256
        or proposal.get("data_calls_allowed") is not False
        or proposal.get("api_key_request_allowed") is not False
        or proposal.get("evaluation_allowed") is not False
        or proposal.get("preregistration_append_allowed") is not False
        or proposal.get("broker_connection_allowed") is not False
        or proposal.get("orders_submitted") is not False
        or proposal.get("live_trading_enabled") is not False
    ):
        raise LedgerIntegrityError("Campaign v2 revision-2 proposal identity is invalid")
    try:
        assert_capture_window_current(as_of=current_real_world_date.isoformat())
        splits = proposal["splits"]
        acquisition_start = date.fromisoformat(proposal["acquisition_start"])
        acquisition_end = date.fromisoformat(proposal["acquisition_end"])
        expected = acquisition_start
        if [item.get("role") for item in splits] != [
            "TRAIN",
            "VALIDATION",
            "UNTOUCHED_TEST",
        ]:
            raise ValueError("split roles")
        for item in splits:
            split_start = date.fromisoformat(item["start"])
            split_end = date.fromisoformat(item["end"])
            if split_start != expected or split_end < split_start or split_end > current_real_world_date:
                raise ValueError("future, empty, or noncontiguous split")
            expected = split_end + timedelta(days=1)
        if acquisition_end > current_real_world_date or expected != acquisition_end + timedelta(days=1):
            raise ValueError("acquisition window")
    except (KeyError, TypeError, ValueError) as error:
        raise LedgerIntegrityError("Campaign v2 revision-2 historical window is invalid") from error
    evaluation = proposal.get("evaluation_protocol", {})
    capture = proposal.get("capture_scope", {})
    total_return = evaluation.get("corporate_action_total_return_policy", {})
    if (
        evaluation.get("authoritative_evaluator") != AUTHORITATIVE_EVALUATOR
        or evaluation.get("vectorbt_on_critical_path") is not False
        or evaluation.get("parameter_screening_allowed") is not False
        or evaluation.get("purge_observations") != 1
        or evaluation.get("embargo_observations") != 1
        or evaluation.get("maximum_untouched_test_evaluations") != 1
        or capture.get("provider_request_allowed") is not False
        or capture.get("train_validation_request_allowed") is not False
        or capture.get("untouched_test_request_allowed") is not False
        or set(capture.get("endpoints", {})) != {"DAILY_BARS", "DIVIDENDS", "STOCK_SPLITS"}
        or capture.get("raw_response_requirements", {}).get("hash_full_payload_bytes_before_parsing") is not True
        or capture.get("raw_response_requirements", {}).get("bind_byte_derived_documentation_evidence") is not True
        or total_return.get("point_in_time_availability_must_be_proven") is not True
        or total_return.get("unresolved_semantics_fail_evaluation") is not True
    ):
        raise LedgerIntegrityError("Campaign v2 revision-2 safety semantics changed")
    return proposal


def _default_parent_resolver(repository_root: Path) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    from core.orchestration.campaign_v2_revision_capture_approval import (
        CONTROL_LEDGER_RELATIVE_PATH as CAPTURE_APPROVAL_PATH,
        CampaignV2RevisionCaptureApprovalLedger,
    )
    from core.orchestration.campaign_v2_revision_preregistration import (
        CONTROL_LEDGER_RELATIVE_PATH as REVISION_1_PREREGISTRATION_PATH,
        CampaignV2RevisionPreregistrationLedger,
    )

    preregistration = CampaignV2RevisionPreregistrationLedger(
        repository_root / REVISION_1_PREREGISTRATION_PATH,
        repository_root=repository_root,
    ).require(REVISION_1_PREREGISTRATION_ID)
    capture = CampaignV2RevisionCaptureApprovalLedger(
        repository_root / CAPTURE_APPROVAL_PATH,
        repository_root=repository_root,
    ).require(REVISION_1_CAPTURE_APPROVAL_ID)
    return preregistration, capture


class CampaignV2Revision2PreregistrationLedger:
    """Append and verify exactly one approved, no-access Revision-2 record."""

    def __init__(
        self,
        path: str | Path,
        *,
        repository_root: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
        git_revision_resolver: Callable[[Path], str] | None = None,
        worktree_clean_resolver: Callable[[Path], bool] | None = None,
        parent_resolver: Callable[[], tuple[Mapping[str, Any], Mapping[str, Any]]] | None = None,
    ) -> None:
        self.path = Path(path)
        self.repository_root = Path(repository_root or Path.cwd()).resolve()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._git_revision_resolver = git_revision_resolver or current_git_revision
        self._worktree_clean_resolver = worktree_clean_resolver or _git_worktree_clean
        self._parent_resolver = parent_resolver or (
            lambda: _default_parent_resolver(self.repository_root)
        )

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
                raise LedgerIntegrityError("Campaign v2 revision-2 ledger is unsafe")
            raw = b""
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                raw += chunk
        finally:
            os.close(descriptor)
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Campaign v2 revision-2 ledger has an incomplete line")
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), 1):
            try:
                row = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise LedgerIntegrityError(f"Campaign v2 revision-2 line {line_number} is invalid") from error
            if not isinstance(row, dict):
                raise LedgerIntegrityError(f"Campaign v2 revision-2 line {line_number} is not an object")
            rows.append(row)
        self._verify(rows)
        return rows

    def _verify(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if len(rows) > 1:
            raise LedgerIntegrityError("Campaign v2 revision-2 permits only one record")
        if not rows:
            return
        row = rows[0]
        try:
            payload = row.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("payload")
            recorded = _timestamp(row.get("recorded_at"), "recorded_at")
            registration_date = date.fromisoformat(
                _required(payload.get("current_real_world_date_at_registration"), "registration date", 10)
            )
            proposal = _verified_proposal(current_real_world_date=registration_date)
            parameter_json = _canonical_bounded_json_mapping(
                proposal["strategy_parameters"], "strategy_parameters"
            )
            evaluation = proposal["evaluation_protocol"]
            capture = proposal["capture_scope"]
            research = proposal["research_exemption_extension"]
            material = {key: value for key, value in row.items() if key != "record_hash"}
            valid = (
                set(row) == _ENVELOPE_FIELDS
                and set(payload) == _PAYLOAD_FIELDS
                and row.get("schema_version") == SCHEMA_VERSION
                and row.get("policy_version") == POLICY_VERSION
                and row.get("event_type") == EVENT_TYPE
                and row.get("previous_hash") == GENESIS_HASH
                and row.get("record_hash") == _hash(material)
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and payload.get("record_type") == "CAMPAIGN_V2_REVISION_2_STAGE_0_PREREGISTRATION"
                and payload.get("status") == "APPROVED_PREREGISTERED_NO_PROVIDER_AUTHORITY"
                and payload.get("authorization_basis") == "EXACT_USER_APPROVAL_IN_CODEX_TASK"
                and _required(payload.get("authorized_by"), "authorized_by", 150)
                == payload.get("authorized_by")
                and payload.get("approval_text") == APPROVAL_TEXT
                and payload.get("approval_scope") == "STAGE_0_CONTROL_PACKAGE_ONLY_NO_KEY_NO_DATA_NO_EVALUATION"
                and payload.get("proposal_sha256") == PROPOSAL_SHA256
                and payload.get("campaign_policy_version") == CAMPAIGN_POLICY_VERSION
                and payload.get("git_worktree_clean") is True
                and payload.get("proposal_valid_until") == PROPOSAL_VALID_UNTIL
                and payload.get("all_partition_ends_on_or_before_registration_date") is True
                and registration_date <= datetime.now(timezone.utc).date()
                and abs((registration_date - recorded.date()).days) <= 1
                and payload.get("superseded_proposal_sha256") == SUPERSEDED_PROPOSAL_SHA256
                and payload.get("superseded_revision_1_preregistration_id") == REVISION_1_PREREGISTRATION_ID
                and payload.get("superseded_revision_1_preregistration_record_sha256") == REVISION_1_PREREGISTRATION_RECORD_SHA256
                and payload.get("superseded_revision_1_capture_proposal_sha256") == REVISION_1_CAPTURE_PROPOSAL_SHA256
                and payload.get("superseded_revision_1_capture_approval_id") == REVISION_1_CAPTURE_APPROVAL_ID
                and payload.get("superseded_revision_1_capture_approval_record_sha256") == REVISION_1_CAPTURE_APPROVAL_RECORD_SHA256
                and payload.get("superseded_revision_1_status") == "TERMINALLY_SUPERSEDED_NEVER_ACTIVATED_NOT_REUSABLE"
                and payload.get("supersession_reason_codes") == proposal["supersession_reason_codes"]
                and payload.get("target_basket") == proposal["target_basket"]
                and payload.get("acquisition_start") == proposal["acquisition_start"]
                and payload.get("acquisition_end") == proposal["acquisition_end"]
                and payload.get("splits") == proposal["splits"]
                and payload.get("retrospective_test_semantic_role") == "SEALED_RETROSPECTIVE_TEST"
                and payload.get("strategy_source_path") == "core/research/conservative_baseline_strategy.py"
                and payload.get("strategy_version") == proposal["strategy_version"]
                and payload.get("parameter_space_canonical_json") == parameter_json
                and payload.get("parameter_space_sha256") == hashlib.sha256(parameter_json.encode("utf-8")).hexdigest()
                and payload.get("authoritative_evaluator") == AUTHORITATIVE_EVALUATOR
                and payload.get("vectorbt_on_critical_path") is False
                and payload.get("evaluation_protocol") == evaluation
                and payload.get("evaluation_protocol_sha256") == _hash(evaluation)
                and payload.get("capture_scope") == capture
                and payload.get("capture_scope_sha256") == _hash(capture)
                and payload.get("corporate_action_total_return_policy_sha256") == _hash(evaluation["corporate_action_total_return_policy"])
                and payload.get("research_exemption_extension_status") == "APPROVED_NOT_APPLIED_TO_ANY_CAPTURE"
                and payload.get("research_exemption_parent_record_hash") == research["parent_exemption_record_hash"]
                and payload.get("research_exemption_limitations") == research["limitations"]
                and payload.get("quarantine_only") is True
                and payload.get("capture_chain_binding_required") is True
                and payload.get("quarantine_root_relative_path") == QUARANTINE_ROOT_RELATIVE_PATH.as_posix()
                and payload.get("control_ledger_relative_path") == CONTROL_LEDGER_RELATIVE_PATH.as_posix()
                and payload.get("next_required_gates") == proposal["gates_before_any_provider_request"][2:]
                and payload.get("preregistration_id") == _preregistration_id(payload)
                and SHA256_PATTERN.fullmatch(str(payload.get("proposal_module_sha256", ""))) is not None
                and SHA256_PATTERN.fullmatch(str(payload.get("strategy_source_sha256", ""))) is not None
                and GIT_REVISION_PATTERN.fullmatch(str(payload.get("git_revision", ""))) is not None
                and all(payload.get(name) is False for name in FIXED_FALSE)
            )
        except (KeyError, TypeError, ValueError) as error:
            raise LedgerIntegrityError("Campaign v2 revision-2 record is invalid") from error
        if not valid:
            raise LedgerIntegrityError("Campaign v2 revision-2 record violates its inert boundary")

    def register_approved_package(self, *, authorized_by: str, approval_text: str) -> dict[str, Any]:
        actor = _required(authorized_by, "authorized_by", 150)
        if approval_text != APPROVAL_TEXT:
            raise ValueError("approval_text does not match the revision-2 proposal hash")
        now = _timestamp(self._clock(), "registration clock")
        actual_now = datetime.now(timezone.utc)
        if not actual_now - MAX_CLOCK_SKEW <= now <= actual_now + MAX_CLOCK_SKEW:
            raise ValueError("registration clock must match actual append time")
        proposal = _verified_proposal(current_real_world_date=now.date())
        existing = self.records()
        if existing:
            if existing[0]["payload"].get("authorized_by") != actor:
                raise LedgerIntegrityError("Campaign v2 revision-2 approval authority differs")
            return self.require(existing[0]["payload"]["preregistration_id"])
        if self._worktree_clean_resolver(self.repository_root) is not True:
            raise ValueError("Git worktree must be clean before revision-2 registration")
        revision = _required(self._git_revision_resolver(self.repository_root), "git_revision", 64).lower()
        if GIT_REVISION_PATTERN.fullmatch(revision) is None:
            raise ValueError("git_revision must be a full lowercase Git commit ID")
        parent_preregistration, parent_capture = self._parent_resolver()
        if (
            parent_preregistration.get("preregistration_id") != REVISION_1_PREREGISTRATION_ID
            or parent_preregistration.get("record_hash") != REVISION_1_PREREGISTRATION_RECORD_SHA256
            or parent_preregistration.get("data_calls_allowed") is not False
            or parent_capture.get("approval_record_id") != REVISION_1_CAPTURE_APPROVAL_ID
            or parent_capture.get("proposal_sha256") != REVISION_1_CAPTURE_PROPOSAL_SHA256
            or parent_capture.get("record_hash") != REVISION_1_CAPTURE_APPROVAL_RECORD_SHA256
            or parent_capture.get("capture_activation_issued") is not False
            or parent_capture.get("provider_bytes_accessed") is not False
        ):
            raise LedgerIntegrityError("Revision-1 parents do not prove an inactive supersession target")
        proposal_path, proposal_hash = _strategy_source_identity(
            self.repository_root,
            "core/research/conservative_baseline_campaign_v2_revision_2_proposal.py",
        )
        strategy_path, strategy_hash = _strategy_source_identity(
            self.repository_root,
            "core/research/conservative_baseline_strategy.py",
        )
        parameter_json = _canonical_bounded_json_mapping(
            proposal["strategy_parameters"], "strategy_parameters"
        )
        evaluation = proposal["evaluation_protocol"]
        capture = proposal["capture_scope"]
        research = proposal["research_exemption_extension"]
        payload: dict[str, Any] = {
            "record_type": "CAMPAIGN_V2_REVISION_2_STAGE_0_PREREGISTRATION",
            "status": "APPROVED_PREREGISTERED_NO_PROVIDER_AUTHORITY",
            "authorized_by": actor,
            "authorization_basis": "EXACT_USER_APPROVAL_IN_CODEX_TASK",
            "approval_text": APPROVAL_TEXT,
            "approval_scope": "STAGE_0_CONTROL_PACKAGE_ONLY_NO_KEY_NO_DATA_NO_EVALUATION",
            "proposal_sha256": PROPOSAL_SHA256,
            "proposal_module_sha256": proposal_hash,
            "campaign_policy_version": CAMPAIGN_POLICY_VERSION,
            "git_revision": revision,
            "git_worktree_clean": True,
            "current_real_world_date_at_registration": now.date().isoformat(),
            "proposal_valid_until": PROPOSAL_VALID_UNTIL,
            "all_partition_ends_on_or_before_registration_date": True,
            "superseded_proposal_sha256": SUPERSEDED_PROPOSAL_SHA256,
            "superseded_revision_1_preregistration_id": REVISION_1_PREREGISTRATION_ID,
            "superseded_revision_1_preregistration_record_sha256": REVISION_1_PREREGISTRATION_RECORD_SHA256,
            "superseded_revision_1_capture_proposal_sha256": REVISION_1_CAPTURE_PROPOSAL_SHA256,
            "superseded_revision_1_capture_approval_id": REVISION_1_CAPTURE_APPROVAL_ID,
            "superseded_revision_1_capture_approval_record_sha256": REVISION_1_CAPTURE_APPROVAL_RECORD_SHA256,
            "superseded_revision_1_status": "TERMINALLY_SUPERSEDED_NEVER_ACTIVATED_NOT_REUSABLE",
            "supersession_reason_codes": proposal["supersession_reason_codes"],
            "target_basket": proposal["target_basket"],
            "acquisition_start": proposal["acquisition_start"],
            "acquisition_end": proposal["acquisition_end"],
            "splits": proposal["splits"],
            "retrospective_test_semantic_role": "SEALED_RETROSPECTIVE_TEST",
            "strategy_source_path": strategy_path,
            "strategy_source_sha256": strategy_hash,
            "strategy_version": proposal["strategy_version"],
            "parameter_space_canonical_json": parameter_json,
            "parameter_space_sha256": hashlib.sha256(parameter_json.encode("utf-8")).hexdigest(),
            "authoritative_evaluator": AUTHORITATIVE_EVALUATOR,
            "vectorbt_on_critical_path": False,
            "evaluation_protocol": evaluation,
            "evaluation_protocol_sha256": _hash(evaluation),
            "capture_scope": capture,
            "capture_scope_sha256": _hash(capture),
            "corporate_action_total_return_policy_sha256": _hash(evaluation["corporate_action_total_return_policy"]),
            "research_exemption_extension_status": "APPROVED_NOT_APPLIED_TO_ANY_CAPTURE",
            "research_exemption_parent_record_hash": research["parent_exemption_record_hash"],
            "research_exemption_limitations": research["limitations"],
            "quarantine_only": True,
            "capture_chain_binding_required": True,
            "quarantine_root_relative_path": QUARANTINE_ROOT_RELATIVE_PATH.as_posix(),
            "control_ledger_relative_path": CONTROL_LEDGER_RELATIVE_PATH.as_posix(),
            "next_required_gates": proposal["gates_before_any_provider_request"][2:],
            **_fixed_false(),
        }
        if proposal_path != "core/research/conservative_baseline_campaign_v2_revision_2_proposal.py":
            raise LedgerIntegrityError("revision-2 proposal source identity changed")
        payload["preregistration_id"] = _preregistration_id(payload)
        complete = self._append(payload, recorded_at=now)
        return self.require(complete["payload"]["preregistration_id"])

    def require(self, preregistration_id: str) -> dict[str, Any]:
        identifier = _required(preregistration_id, "preregistration_id", 100)
        rows = self.records()
        if len(rows) != 1 or rows[0]["payload"]["preregistration_id"] != identifier:
            raise ValueError("verified Campaign v2 revision-2 preregistration was not found")
        return {
            **rows[0]["payload"],
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "registered_at": rows[0]["recorded_at"],
            "record_hash": rows[0]["record_hash"],
        }

    def _append(self, payload: Mapping[str, Any], *, recorded_at: datetime) -> dict[str, Any]:
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
                raise LedgerIntegrityError("Campaign v2 revision-2 lock is unsafe")
            fcntl.flock(lock, fcntl.LOCK_EX)
            existing = self.records()
            if existing:
                if existing[0]["payload"] == dict(payload):
                    return existing[0]
                raise LedgerIntegrityError("Campaign v2 revision-2 preregistration already differs")
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
                _write_all(descriptor, (_canonical_json(complete) + "\n").encode("utf-8"))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return complete
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)


def initialize_campaign_v2_revision_2_quarantine_root(
    repository_root: str | Path,
    *,
    admitted_store_roots: Sequence[str | Path],
) -> Path:
    """Create only an empty private root, disjoint from prior and admitted data."""

    root = Path(repository_root).resolve()
    unresolved_target = root / QUARANTINE_ROOT_RELATIVE_PATH
    if unresolved_target.is_symlink():
        raise LedgerIntegrityError("Campaign v2 revision-2 quarantine root must not be a symlink")
    target = unresolved_target.resolve()
    if root not in target.parents:
        raise LedgerIntegrityError(
            "Campaign v2 revision-2 quarantine root escapes the repository"
        )
    control = (root / CONTROL_LEDGER_RELATIVE_PATH).resolve()
    revision_1 = (root / REVISION_1_ROOT_RELATIVE_PATH).resolve()
    if any(
        target == boundary or target in boundary.parents or boundary in target.parents
        for boundary in (control, revision_1)
    ):
        raise LedgerIntegrityError("Campaign v2 revision-2 storage boundaries overlap")
    if not admitted_store_roots:
        raise ValueError("at least one admitted store root must be declared")
    admitted = [(root / path).resolve() for path in KNOWN_ADMITTED_STORE_RELATIVE_PATHS]
    admitted.extend(
        (path if path.is_absolute() else root / path).resolve()
        for value in admitted_store_roots
        for path in (Path(value),)
    )
    for store in admitted:
        if target == store or target in store.parents or store in target.parents:
            raise LedgerIntegrityError("Campaign v2 revision-2 quarantine overlaps admitted storage")
    _private_directory(target)
    if any(target.iterdir()):
        raise LedgerIntegrityError("Campaign v2 revision-2 quarantine root must start empty")
    return target
