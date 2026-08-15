from __future__ import annotations

"""Approved, inert preregistration for Campaign v2 revision-1.

The single-record immutable chain binds the exact human approval, completed
historical splits, strategy, assumptions and quarantine boundary before any
provider-byte access.  It cannot activate capture, admit data or run a replay.
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
from core.orchestration.historical_quarantine_preregistration import (
    ENDPOINT_TEMPLATE,
    PROVIDER_DATASET_ID,
    PROVIDER_ID,
    _canonical_bounded_json_mapping,
    _git_worktree_clean,
    _normalise_evaluation_protocol,
    _strategy_source_identity,
)
from core.research.conservative_baseline_campaign_v2_revision_proposal import (
    CAMPAIGN_POLICY_VERSION,
    SUPERSEDED_PROPOSAL_SHA256,
    proposal_package,
)


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "massive-completed-history-preregistration-v2-revision-1"
PROPOSAL_SHA256 = (
    "7c43094e64f324d6987b67a25d03626eb4defe4096ae1b135e1c0319b60fc0d5"
)
APPROVAL_TEXT = f"I approve Campaign v2 revision-1 proposal {PROPOSAL_SHA256}"
SUPERSESSION_RECORD_SHA256 = (
    "3f8f1c3e9ddc85f3e34ed484d7162b565525d6eb73488bb1c99c73ab01f845e5"
)
SUPERSEDED_PREREGISTRATION_RECORD_SHA256 = (
    "cc626aa7c4aedab63bce51cae5dd9e90a1f5d81f00bd46a49fb956cfe4dcf210"
)
V1_ENTITLEMENT_METADATA_SHA256 = (
    "f806498b856226d8a990b2c6796f9489b9dc0f4f9073df384339e019d6f6d0b7"
)
CONTROL_LEDGER_RELATIVE_PATH = Path(
    "data/research/massive_campaign_v2_revision_1/preregistration.jsonl"
)
QUARANTINE_ROOT_RELATIVE_PATH = Path(
    "data/research/massive_campaign_v2_revision_1/raw"
)
SUPERSEDED_QUARANTINE_ROOT_RELATIVE_PATH = Path(
    "data/research/massive_campaign_v2/raw"
)
KNOWN_ADMITTED_STORE_RELATIVE_PATHS = (
    Path("data/research/massive_research_exempt_admitted"),
)
EVENT_TYPE = "CAMPAIGN_V2_REVISION_1_APPROVED_AND_PREREGISTERED"
MAX_LEDGER_BYTES = 4 * 1024 * 1024
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
FIXED_FALSE = (
    "provider_bytes_accessed",
    "data_calls_allowed",
    "capture_activation_issued",
    "entitlement_revalidated",
    "provider_payload_semantics_qualified",
    "historical_availability_qualified",
    "source_bytes_authenticated",
    "research_exemption_applied",
    "dataset_admitted",
    "train_validation_opened",
    "untouched_test_opened",
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
        "authorized_by",
        "authorization_basis",
        "approval_text",
        "approval_scope",
        "proposal_sha256",
        "proposal_module_sha256",
        "superseded_proposal_sha256",
        "supersession_record_hash",
        "superseded_preregistration_record_hash",
        "campaign_policy_version",
        "git_revision",
        "git_worktree_clean",
        "provider_id",
        "provider_dataset_id",
        "endpoint_template",
        "target_basket",
        "acquisition_start",
        "acquisition_end",
        "splits",
        "all_partition_ends_on_or_before_registration_date",
        "current_real_world_date_at_registration",
        "retrospective_test_semantic_role",
        "preregistration_timing_truth",
        "expected_request_count",
        "strategy_entrypoint",
        "strategy_source_path",
        "strategy_version",
        "strategy_source_sha256",
        "parameter_space_canonical_json",
        "parameter_space_sha256",
        "evaluation_protocol",
        "evaluation_protocol_sha256",
        "research_exemption_parent_record_hash",
        "research_exemption_limitations",
        "entitlement_reference_sha256",
        "entitlement_revalidation_required_before_data_call",
        "quarantine_only",
        "capture_chain_binding_required",
        "quarantine_root_relative_path",
        "control_ledger_relative_path",
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
            raise OSError("Campaign v2 revision append made no progress")
        offset += count


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    details = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
    ):
        raise LedgerIntegrityError("Campaign v2 revision directory is unsafe")
    os.chmod(path, 0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise LedgerIntegrityError(
            "Campaign v2 revision directory must be owner-only"
        )


def _fixed_false() -> dict[str, bool]:
    return {name: False for name in FIXED_FALSE}


def _verified_proposal(*, current_real_world_date: date) -> dict[str, Any]:
    """Verify the proposal and reject every future-dated partition."""

    proposal = proposal_package()
    if (
        proposal.get("proposal_sha256") != PROPOSAL_SHA256
        or proposal.get("approval_status") != "PENDING_EXPLICIT_HUMAN_APPROVAL"
        or proposal.get("campaign_policy_version") != CAMPAIGN_POLICY_VERSION
        or proposal.get("supersedes_proposal_sha256")
        != SUPERSEDED_PROPOSAL_SHA256
        or proposal.get("data_calls_allowed") is not False
        or proposal.get("evaluation_allowed") is not False
        or proposal.get("performance_claim_allowed") is not False
        or proposal.get("broker_connection_allowed") is not False
        or proposal.get("orders_submitted") is not False
        or proposal.get("live_trading_enabled") is not False
    ):
        raise LedgerIntegrityError("Campaign v2 revision proposal identity is invalid")
    splits = proposal.get("splits")
    if (
        not isinstance(splits, list)
        or any(not isinstance(item, dict) for item in splits)
        or [item.get("role") for item in splits]
        != ["TRAIN", "VALIDATION", "UNTOUCHED_TEST"]
    ):
        raise LedgerIntegrityError("Campaign v2 revision split roles are invalid")
    try:
        acquisition_start = date.fromisoformat(proposal["acquisition_start"])
        acquisition_end = date.fromisoformat(proposal["acquisition_end"])
        expected = acquisition_start
        for item in splits:
            split_start = date.fromisoformat(item["start"])
            split_end = date.fromisoformat(item["end"])
            if (
                split_start != expected
                or split_end < split_start
                or split_end > current_real_world_date
            ):
                raise ValueError("future, empty or noncontiguous split")
            expected = split_end + timedelta(days=1)
    except (KeyError, TypeError, ValueError) as error:
        raise LedgerIntegrityError(
            "Campaign v2 revision contains an invalid historical date"
        ) from error
    if (
        acquisition_end > current_real_world_date
        or acquisition_end < acquisition_start
        or expected != acquisition_end + timedelta(days=1)
    ):
        raise LedgerIntegrityError(
            "Campaign v2 revision must end on or before the current real-world date"
        )
    classification = proposal.get("test_evidence_classification")
    limitations = proposal.get("research_exemption_extension", {}).get(
        "limitations"
    )
    if (
        classification
        != {
            "schema_role": "UNTOUCHED_TEST",
            "semantic_role": "SEALED_RETROSPECTIVE_TEST",
            "genuinely_future_at_preregistration": False,
            "single_open_only": True,
            "promotion_or_track_record_authority": False,
            "claim_outcomes_were_unknown_to_researchers": False,
        }
        or not isinstance(limitations, list)
        or "RETROSPECTIVE_AT_PREREGISTRATION_NOT_GENUINELY_FUTURE_UNTOUCHED"
        not in limitations
        or "NO_PROMOTION_OR_TRACK_RECORD_AUTHORITY" not in limitations
        or proposal.get("evaluation_protocol", {}).get("purge_observations") != 1
        or proposal.get("evaluation_protocol", {}).get("embargo_observations") != 1
        or proposal.get("evaluation_protocol", {}).get(
            "maximum_untouched_test_evaluations"
        )
        != 1
        or proposal.get("execution_policy", {})
        .get("simulation_controls", {})
        .get("maximum_position_fraction")
        != "0.25"
    ):
        raise LedgerIntegrityError(
            "Campaign v2 revision evidence or risk semantics changed"
        )
    return proposal


def _preregistration_id(payload: Mapping[str, Any]) -> str:
    material = {
        key: value for key, value in payload.items() if key != "preregistration_id"
    }
    return "HQP2R1-" + _hash([material, POLICY_VERSION])[:32].upper()


class CampaignV2RevisionPreregistrationLedger:
    """Append and verify one exact approved, no-access revision preregistration."""

    def __init__(
        self,
        path: str | Path,
        *,
        repository_root: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
        git_revision_resolver: Callable[[Path], str] | None = None,
        worktree_clean_resolver: Callable[[Path], bool] | None = None,
    ) -> None:
        self.path = Path(path)
        self.repository_root = Path(repository_root or Path.cwd()).resolve()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._git_revision_resolver = git_revision_resolver or current_git_revision
        self._worktree_clean_resolver = (
            worktree_clean_resolver or _git_worktree_clean
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
                or details.st_nlink != 1
                or details.st_size > MAX_LEDGER_BYTES
            ):
                raise LedgerIntegrityError(
                    "Campaign v2 revision preregistration ledger is unsafe"
                )
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
                "Campaign v2 revision preregistration has an incomplete line"
            )
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), 1):
            try:
                row = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise LedgerIntegrityError(
                    f"Campaign v2 revision line {line_number} is invalid"
                ) from error
            if not isinstance(row, dict):
                raise LedgerIntegrityError(
                    f"Campaign v2 revision line {line_number} is not an object"
                )
            rows.append(row)
        self._verify(rows)
        return rows

    def _verify(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if len(rows) > 1:
            raise LedgerIntegrityError(
                "Campaign v2 revision permits only one preregistration"
            )
        if not rows:
            return
        row = rows[0]
        try:
            recorded = _timestamp(row.get("recorded_at"), "recorded_at")
            material = {key: value for key, value in row.items() if key != "record_hash"}
            payload = row.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("payload")
            registration_date = date.fromisoformat(
                _required(
                    payload.get("current_real_world_date_at_registration"),
                    "current_real_world_date_at_registration",
                    10,
                )
            )
            proposal = _verified_proposal(
                current_real_world_date=registration_date
            )
            parameter_json = _canonical_bounded_json_mapping(
                proposal["strategy_parameters"], "strategy_parameters"
            )
            evaluation = _normalise_evaluation_protocol(
                proposal["evaluation_protocol"]
            )
            expected_research = proposal["research_exemption_extension"]
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
                == "CAMPAIGN_V2_REVISION_1_COMPLETED_HISTORY_PREREGISTRATION"
                and payload.get("status")
                == "APPROVED_PREREGISTERED_BEFORE_PROVIDER_BYTE_ACCESS"
                and payload.get("approval_text") == APPROVAL_TEXT
                and payload.get("authorization_basis")
                == "EXACT_USER_APPROVAL_IN_CODEX_TASK"
                and bool(payload.get("authorized_by"))
                and payload.get("approval_scope")
                == "REVISION_CONTROL_PACKAGE_ONLY_NO_DATA_CALL_NO_EVALUATION"
                and payload.get("proposal_sha256") == PROPOSAL_SHA256
                and payload.get("superseded_proposal_sha256")
                == SUPERSEDED_PROPOSAL_SHA256
                and payload.get("supersession_record_hash")
                == SUPERSESSION_RECORD_SHA256
                and payload.get("superseded_preregistration_record_hash")
                == SUPERSEDED_PREREGISTRATION_RECORD_SHA256
                and payload.get("campaign_policy_version")
                == CAMPAIGN_POLICY_VERSION
                and payload.get("target_basket") == proposal["target_basket"]
                and payload.get("acquisition_start")
                == proposal["acquisition_start"]
                and payload.get("acquisition_end") == proposal["acquisition_end"]
                and payload.get("splits") == proposal["splits"]
                and payload.get("all_partition_ends_on_or_before_registration_date")
                is True
                and payload.get("current_real_world_date_at_registration")
                == registration_date.isoformat()
                and registration_date <= date.today()
                and abs((registration_date - recorded.date()).days) <= 1
                and payload.get("retrospective_test_semantic_role")
                == "SEALED_RETROSPECTIVE_TEST"
                and payload.get("preregistration_timing_truth")
                == "PREREGISTERED_BEFORE_PROVIDER_BYTE_ACCESS_NOT_BEFORE_MARKET_DATES"
                and payload.get("expected_request_count")
                == proposal["expected_capture_counts"]["TOTAL"]
                and payload.get("strategy_entrypoint")
                == (
                    "core.research.conservative_baseline_strategy:"
                    "ConservativeBaselineStrategy"
                )
                and payload.get("strategy_source_path")
                == "core/research/conservative_baseline_strategy.py"
                and payload.get("strategy_version") == proposal["strategy_version"]
                and payload.get("parameter_space_canonical_json") == parameter_json
                and payload.get("parameter_space_sha256")
                == hashlib.sha256(parameter_json.encode("utf-8")).hexdigest()
                and payload.get("evaluation_protocol") == evaluation
                and payload.get("evaluation_protocol_sha256") == _hash(evaluation)
                and payload.get("research_exemption_parent_record_hash")
                == expected_research["parent_exemption_record_hash"]
                and payload.get("research_exemption_limitations")
                == expected_research["limitations"]
                and payload.get("entitlement_reference_sha256")
                == V1_ENTITLEMENT_METADATA_SHA256
                and payload.get("entitlement_revalidation_required_before_data_call")
                is True
                and payload.get("provider_id") == PROVIDER_ID
                and payload.get("provider_dataset_id") == PROVIDER_DATASET_ID
                and payload.get("endpoint_template") == ENDPOINT_TEMPLATE
                and payload.get("quarantine_only") is True
                and payload.get("capture_chain_binding_required") is True
                and payload.get("quarantine_root_relative_path")
                == QUARANTINE_ROOT_RELATIVE_PATH.as_posix()
                and payload.get("control_ledger_relative_path")
                == CONTROL_LEDGER_RELATIVE_PATH.as_posix()
                and payload.get("preregistration_id")
                == _preregistration_id(payload)
                and SHA256_PATTERN.fullmatch(
                    str(payload.get("proposal_module_sha256", ""))
                )
                is not None
                and SHA256_PATTERN.fullmatch(
                    str(payload.get("strategy_source_sha256", ""))
                )
                is not None
                and GIT_REVISION_PATTERN.fullmatch(
                    str(payload.get("git_revision", ""))
                )
                is not None
                and payload.get("git_worktree_clean") is True
                and any(payload.get(name) is not False for name in FIXED_FALSE)
                is False
            )
        except (KeyError, TypeError, ValueError) as error:
            raise LedgerIntegrityError(
                "Campaign v2 revision preregistration is invalid"
            ) from error
        if not boundary:
            raise LedgerIntegrityError(
                "Campaign v2 revision preregistration violates its boundary"
            )

    def register_approved_package(
        self, *, authorized_by: str, approval_text: str
    ) -> dict[str, Any]:
        actor = _required(authorized_by, "authorized_by", 150)
        if approval_text != APPROVAL_TEXT:
            raise ValueError("approval_text does not match the revision proposal hash")
        now = _timestamp(self._clock(), "registration clock")
        actual_now = datetime.now(timezone.utc)
        if not actual_now - MAX_CLOCK_SKEW <= now <= actual_now + MAX_CLOCK_SKEW:
            raise ValueError("registration clock must match actual append time")
        current_real_world_date = date.today()
        proposal = _verified_proposal(
            current_real_world_date=current_real_world_date
        )
        existing = self.records()
        if existing:
            if existing[0]["payload"].get("authorized_by") != actor:
                raise LedgerIntegrityError(
                    "Campaign v2 revision approval authority differs"
                )
            return self.require(
                existing[0]["payload"]["preregistration_id"]
            )
        if self._worktree_clean_resolver(self.repository_root) is not True:
            raise ValueError(
                "Git worktree must be clean before Campaign v2 revision registration"
            )
        revision = _required(
            self._git_revision_resolver(self.repository_root), "git_revision", 64
        ).lower()
        if GIT_REVISION_PATTERN.fullmatch(revision) is None:
            raise ValueError("git_revision must be a full lowercase Git commit ID")
        proposal_source = (
            self.repository_root
            / "core/research/conservative_baseline_campaign_v2_revision_proposal.py"
        )
        source_path, source_hash = _strategy_source_identity(
            self.repository_root,
            "core/research/conservative_baseline_strategy.py",
        )
        parameter_json = _canonical_bounded_json_mapping(
            proposal["strategy_parameters"], "strategy_parameters"
        )
        evaluation = _normalise_evaluation_protocol(
            proposal["evaluation_protocol"]
        )
        research = proposal["research_exemption_extension"]
        payload: dict[str, Any] = {
            "record_type": (
                "CAMPAIGN_V2_REVISION_1_COMPLETED_HISTORY_PREREGISTRATION"
            ),
            "status": "APPROVED_PREREGISTERED_BEFORE_PROVIDER_BYTE_ACCESS",
            "authorized_by": actor,
            "authorization_basis": "EXACT_USER_APPROVAL_IN_CODEX_TASK",
            "approval_text": APPROVAL_TEXT,
            "approval_scope": (
                "REVISION_CONTROL_PACKAGE_ONLY_NO_DATA_CALL_NO_EVALUATION"
            ),
            "proposal_sha256": PROPOSAL_SHA256,
            "proposal_module_sha256": hashlib.sha256(
                proposal_source.read_bytes()
            ).hexdigest(),
            "superseded_proposal_sha256": SUPERSEDED_PROPOSAL_SHA256,
            "supersession_record_hash": SUPERSESSION_RECORD_SHA256,
            "superseded_preregistration_record_hash": (
                SUPERSEDED_PREREGISTRATION_RECORD_SHA256
            ),
            "campaign_policy_version": CAMPAIGN_POLICY_VERSION,
            "git_revision": revision,
            "git_worktree_clean": True,
            "provider_id": PROVIDER_ID,
            "provider_dataset_id": PROVIDER_DATASET_ID,
            "endpoint_template": ENDPOINT_TEMPLATE,
            "target_basket": proposal["target_basket"],
            "acquisition_start": proposal["acquisition_start"],
            "acquisition_end": proposal["acquisition_end"],
            "splits": proposal["splits"],
            "all_partition_ends_on_or_before_registration_date": True,
            "current_real_world_date_at_registration": (
                current_real_world_date.isoformat()
            ),
            "retrospective_test_semantic_role": "SEALED_RETROSPECTIVE_TEST",
            "preregistration_timing_truth": (
                "PREREGISTERED_BEFORE_PROVIDER_BYTE_ACCESS_NOT_BEFORE_MARKET_DATES"
            ),
            "expected_request_count": proposal["expected_capture_counts"]["TOTAL"],
            "strategy_entrypoint": (
                "core.research.conservative_baseline_strategy:"
                "ConservativeBaselineStrategy"
            ),
            "strategy_source_path": source_path,
            "strategy_version": proposal["strategy_version"],
            "strategy_source_sha256": source_hash,
            "parameter_space_canonical_json": parameter_json,
            "parameter_space_sha256": hashlib.sha256(
                parameter_json.encode("utf-8")
            ).hexdigest(),
            "evaluation_protocol": evaluation,
            "evaluation_protocol_sha256": _hash(evaluation),
            "research_exemption_parent_record_hash": research[
                "parent_exemption_record_hash"
            ],
            "research_exemption_limitations": research["limitations"],
            "entitlement_reference_sha256": V1_ENTITLEMENT_METADATA_SHA256,
            "entitlement_revalidation_required_before_data_call": True,
            "quarantine_only": True,
            "capture_chain_binding_required": True,
            "quarantine_root_relative_path": (
                QUARANTINE_ROOT_RELATIVE_PATH.as_posix()
            ),
            "control_ledger_relative_path": CONTROL_LEDGER_RELATIVE_PATH.as_posix(),
            **_fixed_false(),
        }
        payload["preregistration_id"] = _preregistration_id(payload)
        complete = self._append(payload, recorded_at=now)
        return self.require(complete["payload"]["preregistration_id"])

    def require(self, preregistration_id: str) -> dict[str, Any]:
        identifier = _required(preregistration_id, "preregistration_id", 100)
        rows = self.records()
        if len(rows) != 1 or rows[0]["payload"]["preregistration_id"] != identifier:
            raise ValueError(
                "verified Campaign v2 revision preregistration was not found"
            )
        return {
            **rows[0]["payload"],
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "registered_at": rows[0]["recorded_at"],
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
            lock_details = os.fstat(lock)
            if (
                not stat.S_ISREG(lock_details.st_mode)
                or stat.S_IMODE(lock_details.st_mode) != 0o600
                or lock_details.st_nlink != 1
            ):
                raise LedgerIntegrityError(
                    "Campaign v2 revision preregistration lock is unsafe"
                )
            fcntl.flock(lock, fcntl.LOCK_EX)
            existing = self.records()
            if existing:
                if existing[0]["payload"] == dict(payload):
                    return existing[0]
                raise LedgerIntegrityError(
                    "Campaign v2 revision preregistration already differs"
                )
            material = {
                "schema_version": SCHEMA_VERSION,
                "policy_version": POLICY_VERSION,
                "event_type": EVENT_TYPE,
                "recorded_at": recorded_at.isoformat(timespec="microseconds"),
                "payload": json.loads(_canonical_json(dict(payload))),
                "previous_hash": GENESIS_HASH,
            }
            complete = {**material, "record_hash": _hash(material)}
            target = os.open(
                self.path,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | _no_follow(),
                0o600,
            )
            try:
                os.fchmod(target, 0o600)
                _write_all(target, (_canonical_json(complete) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            return complete
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)


def initialize_campaign_v2_revision_quarantine_root(
    repository_root: str | Path,
    *,
    admitted_store_roots: Sequence[str | Path],
) -> Path:
    """Create only an empty, private revision root disjoint from all evidence."""

    root = Path(repository_root).resolve()
    unresolved_target = root / QUARANTINE_ROOT_RELATIVE_PATH
    if unresolved_target.is_symlink():
        raise LedgerIntegrityError(
            "Campaign v2 revision quarantine root must not be a symlink"
        )
    target = unresolved_target.resolve()
    control = (root / CONTROL_LEDGER_RELATIVE_PATH).resolve()
    superseded = (root / SUPERSEDED_QUARANTINE_ROOT_RELATIVE_PATH).resolve()
    if (
        target == control
        or target in control.parents
        or control in target.parents
        or target == superseded
        or target in superseded.parents
        or superseded in target.parents
    ):
        raise LedgerIntegrityError(
            "Campaign v2 revision storage boundaries overlap"
        )
    if not admitted_store_roots:
        raise ValueError("at least one admitted store root must be declared")
    admitted = [
        (root / relative).resolve()
        for relative in KNOWN_ADMITTED_STORE_RELATIVE_PATHS
    ]
    admitted.extend(Path(value).resolve() for value in admitted_store_roots)
    for store in admitted:
        if target == store or target in store.parents or store in target.parents:
            raise LedgerIntegrityError(
                "Campaign v2 revision quarantine overlaps admitted storage"
            )
    _private_directory(target)
    if any(target.iterdir()):
        raise LedgerIntegrityError(
            "Campaign v2 revision quarantine root must start empty"
        )
    return target
