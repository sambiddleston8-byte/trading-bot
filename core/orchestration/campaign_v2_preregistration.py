from __future__ import annotations

"""Approved, no-data-access control chain for conservative Campaign v2.

The ledger binds the exact human-approved proposal to the closed Campaign v1
chain, a linked research-exemption extension, and a future-window quarantine
preregistration.  It cannot fetch, open, stage, admit, or evaluate data.
"""

from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

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
    HistoricalQuarantinePreregistrationLedger,
    _canonical_bounded_json_mapping,
    _git_worktree_clean,
    _normalise_evaluation_protocol,
    _strategy_source_identity,
)
from core.research.conservative_baseline_campaign_v2_proposal import (
    CAMPAIGN_POLICY_VERSION,
    proposal_package,
)
from core.research.conservative_baseline_campaign_v2_revision_proposal import (
    APPROVAL_STATUS as REVISION_APPROVAL_STATUS,
    CALENDAR_CONFLICT_IDENTIFIED_ON,
    CAMPAIGN_POLICY_VERSION as REVISION_CAMPAIGN_POLICY_VERSION,
    proposal_package as revision_proposal_package,
)

if TYPE_CHECKING:
    from core.research.massive_research_exempt_replay import ResearchExemptionLedger


SCHEMA_VERSION = "2.0"
POLICY_VERSION = "massive-future-window-preregistration-v2"
PROPOSAL_SHA256 = (
    "ff4ca9a0919e43044337e609140197bb5869681e47e226720b12df64075cc82b"
)
V1_CLOSURE_RECORD_SHA256 = (
    "bc245c30a4dc744f30ccd2bba9381a213dc18d0fe867434ad1c225bd7c0d44d5"
)
V1_PREREGISTRATION_RECORD_SHA256 = (
    "d20899e47335d6fa4ee2a5773fcd1cf12f1b921018a775a4525e7bc166a4dc6d"
)
V1_ENTITLEMENT_METADATA_SHA256 = (
    "f806498b856226d8a990b2c6796f9489b9dc0f4f9073df384339e019d6f6d0b7"
)
APPROVAL_TEXT = f"I approve Campaign v2 proposal {PROPOSAL_SHA256}"
CONTROL_LEDGER_RELATIVE_PATH = Path(
    "data/research/massive_campaign_v2/preregistration.jsonl"
)
QUARANTINE_ROOT_RELATIVE_PATH = Path("data/research/massive_campaign_v2/raw")
V1_QUARANTINE_ROOT_RELATIVE_PATH = Path("data/research/massive_quarantine/raw")
KNOWN_ADMITTED_STORE_RELATIVE_PATHS = (
    Path("data/research/massive_research_exempt_admitted"),
)
EVENT_SEQUENCE = (
    "CAMPAIGN_V2_PROPOSAL_APPROVED",
    "CAMPAIGN_V2_RESEARCH_EXEMPTION_EXTENSION_REGISTERED",
    "CAMPAIGN_V2_QUARANTINE_PREREGISTERED",
)
SUPERSESSION_EVENT = "CAMPAIGN_V2_CONTROLS_SUPERSEDED_CALENDAR_CONFLICT"
CONTROL_EVENT_SEQUENCE = (*EVENT_SEQUENCE, SUPERSESSION_EVENT)
ORIGINAL_CONTROLS_RETIRED = True
V1_OPENED_ACQUISITION_START = date(2025, 8, 1)
MAX_LEDGER_BYTES = 4 * 1024 * 1024
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
FIXED_FALSE = (
    "provider_bytes_accessed",
    "data_calls_allowed",
    "capture_activation_issued",
    "provider_payload_semantics_qualified",
    "historical_availability_qualified",
    "source_bytes_authenticated",
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


def _digest(value: Any, name: str) -> str:
    resolved = _required(value, name, 64).lower()
    if SHA256_PATTERN.fullmatch(resolved) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _timestamp(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _record_hash(value: Mapping[str, Any]) -> str:
    return _hash(value)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Campaign v2 control append made no progress")
        offset += count


def _no_follow() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    details = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
    ):
        raise LedgerIntegrityError("Campaign v2 directory is unsafe")
    os.chmod(path, 0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise LedgerIntegrityError("Campaign v2 directory must be owner-only")


def _fixed_false() -> dict[str, bool]:
    return {name: False for name in FIXED_FALSE}


def _plan_id(payload: Mapping[str, Any]) -> str:
    material = {key: value for key, value in payload.items() if key != "preregistration_id"}
    return "HQP2-" + _hash([material, POLICY_VERSION])[:32].upper()


def _require_before_untouched_window(
    registered_at: datetime, proposal: Mapping[str, Any]
) -> date:
    untouched_start = date.fromisoformat(proposal["splits"][-1]["start"])
    if registered_at.date() >= untouched_start:
        raise ValueError(
            "Campaign v2 must be preregistered before its untouched window begins"
        )
    return untouched_start


def _verified_revision_window(proposal: Mapping[str, Any]) -> None:
    """Independently enforce completed, contiguous, v1-disjoint revision dates."""

    try:
        acquisition_start = date.fromisoformat(
            _required(proposal.get("acquisition_start"), "acquisition_start", 10)
        )
        acquisition_end = date.fromisoformat(
            _required(proposal.get("acquisition_end"), "acquisition_end", 10)
        )
        conflict_date = date.fromisoformat(CALENDAR_CONFLICT_IDENTIFIED_ON)
        splits = proposal.get("splits")
        if (
            not isinstance(splits, list)
            or any(not isinstance(row, dict) for row in splits)
            or [row.get("role") for row in splits]
            != [
                "TRAIN",
                "VALIDATION",
                "UNTOUCHED_TEST",
            ]
        ):
            raise ValueError("split roles")
        expected = acquisition_start
        for row in splits:
            split_start = date.fromisoformat(
                _required(row.get("start"), "split start", 10)
            )
            split_end = date.fromisoformat(
                _required(row.get("end"), "split end", 10)
            )
            if split_start != expected or split_end < split_start:
                raise ValueError("split continuity")
            expected = split_end + timedelta(days=1)
    except (TypeError, ValueError) as error:
        raise LedgerIntegrityError(
            "Campaign v2 revision dates are invalid"
        ) from error
    if (
        acquisition_start > acquisition_end
        or expected != acquisition_end + timedelta(days=1)
        or acquisition_end >= conflict_date
        or acquisition_end >= V1_OPENED_ACQUISITION_START
    ):
        raise LedgerIntegrityError(
            "Campaign v2 revision must be completed and disjoint from opened v1 data"
        )


def _verified_v1_parents(
    *,
    v1_exemption_ledger: ResearchExemptionLedger,
    v1_preregistration_ledger: HistoricalQuarantinePreregistrationLedger,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    rows = v1_exemption_ledger.records()
    if (
        len(rows) != 5
        or tuple(row["event_type"] for row in rows)
        != (
            "RESEARCH_EXEMPTION_REGISTERED",
            "TRAIN_VALIDATION_RESEARCH_ADMITTED",
            "UNTOUCHED_TEST_CONSUMPTION_STARTED",
            "UNTOUCHED_TEST_RESULT_RECORDED",
            "CAMPAIGN_V1_CLOSED_PROTOCOL_NONCONFORMANT",
        )
    ):
        raise LedgerIntegrityError("Campaign v1 must have one verified closed chain")
    exemption = rows[0]
    closure = rows[-1]
    closure_payload = closure["payload"]
    if (
        closure_payload.get("campaign_status") != "CLOSED_PROTOCOL_NONCONFORMANT"
        or closure_payload.get("campaign_v1_archived") is not True
        or closure_payload.get("campaign_v1_evaluation_allowed") is not False
        or closure_payload.get("campaign_v1_untouched_reusable") is not False
        or closure_payload.get("research_exemption_active_for_original_scope") is not True
        or closure_payload.get("research_exemption_scope_extension_granted") is not False
        or closure_payload.get("exemption_record_hash") != exemption["record_hash"]
        or closure_payload.get("closure_reason_codes")
        != [
            "PREREGISTERED_PURGE_AND_EMBARGO_OMITTED",
            "SPY_DIAGNOSTIC_TIMING_MISALIGNED_AND_NOT_REGISTERED_COMPLETE_RETURN",
        ]
        or closure["record_hash"] != V1_CLOSURE_RECORD_SHA256
    ):
        raise LedgerIntegrityError("Campaign v1 closure does not authorize a v2 parent")
    exemption_payload = exemption["payload"]
    parent_id = _required(
        exemption_payload.get("preregistration_id"), "v1 preregistration_id", 100
    )
    parent_plan = v1_preregistration_ledger.require(parent_id)
    if (
        parent_plan["record_hash"] != exemption_payload.get("preregistration_record_hash")
        or parent_plan["record_hash"] != V1_PREREGISTRATION_RECORD_SHA256
        or parent_plan["entitlement_metadata_sha256"]
        != V1_ENTITLEMENT_METADATA_SHA256
        or parent_plan["target_basket"] != ["AAPL", "MSFT", "SPY"]
    ):
        raise LedgerIntegrityError("Campaign v1 preregistration parent is inconsistent")
    return closure, exemption, parent_plan


class CampaignV2ControlLedger:
    """Immutable v2 control chain with an optional terminal supersession."""

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
        self._worktree_clean_resolver = worktree_clean_resolver or _git_worktree_clean

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
                raise LedgerIntegrityError("Campaign v2 control ledger is unsafe")
            raw = b""
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                raw += chunk
        finally:
            os.close(descriptor)
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Campaign v2 control ledger has an incomplete line")
        rows: list[dict[str, Any]] = []
        previous = GENESIS_HASH
        prior_time: datetime | None = None
        for line_number, line in enumerate(raw.splitlines(), 1):
            try:
                row = json.loads(line)
                recorded = _timestamp(row["recorded_at"], "recorded_at")
                material = {key: value for key, value in row.items() if key != "record_hash"}
                if (
                    set(row) != _ENVELOPE_FIELDS
                    or row["schema_version"] != SCHEMA_VERSION
                    or row["policy_version"] != POLICY_VERSION
                    or line_number > len(CONTROL_EVENT_SEQUENCE)
                    or row["event_type"] != CONTROL_EVENT_SEQUENCE[line_number - 1]
                    or not isinstance(row["payload"], dict)
                    or row["previous_hash"] != previous
                    or row["record_hash"] != _record_hash(material)
                    or recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                    or (prior_time is not None and recorded < prior_time)
                ):
                    raise ValueError("boundary mismatch")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise LedgerIntegrityError(
                    f"Campaign v2 control ledger line {line_number} is invalid"
                ) from error
            rows.append(row)
            previous = row["record_hash"]
            prior_time = recorded
        self._verify_semantics(rows)
        return rows

    def _verify_semantics(self, rows: Sequence[Mapping[str, Any]]) -> None:
        proposal = proposal_package()
        if proposal["proposal_sha256"] != PROPOSAL_SHA256:
            raise LedgerIntegrityError("Campaign v2 proposal identity changed")
        if not rows:
            return
        approval = rows[0]["payload"]
        if (
            approval.get("proposal_sha256") != PROPOSAL_SHA256
            or approval.get("approval_text") != APPROVAL_TEXT
            or approval.get("campaign_policy_version") != CAMPAIGN_POLICY_VERSION
            or approval.get("approval_scope")
            != "CONTROL_PACKAGE_ONLY_NO_DATA_CALL_NO_EVALUATION"
            or approval.get("authorization_basis")
            != "EXACT_USER_APPROVAL_IN_CODEX_TASK"
            or not approval.get("authorized_by")
            or SHA256_PATTERN.fullmatch(
                str(approval.get("proposal_module_sha256", ""))
            )
            is None
            or GIT_REVISION_PATTERN.fullmatch(str(approval.get("git_revision", "")))
            is None
            or any(approval.get(name) is not False for name in FIXED_FALSE)
        ):
            raise LedgerIntegrityError("Campaign v2 approval payload is invalid")
        if len(rows) < 2:
            return
        extension = rows[1]["payload"]
        expected_extension = proposal["research_exemption_extension"]
        if (
            extension.get("approval_record_hash") != rows[0]["record_hash"]
            or extension.get("proposal_sha256") != PROPOSAL_SHA256
            or extension.get("status") != "AUTHORIZED_NOT_APPLIED_TO_ANY_CAPTURE"
            or extension.get("target_basket") != expected_extension["target_basket"]
            or extension.get("scope_start") != expected_extension["scope_start"]
            or extension.get("scope_end") != expected_extension["scope_end"]
            or extension.get("parent_exemption_id")
            != expected_extension["parent_exemption_id"]
            or extension.get("parent_exemption_record_hash")
            != expected_extension["parent_exemption_record_hash"]
            or extension.get("v1_closure_record_hash")
            != V1_CLOSURE_RECORD_SHA256
            or extension.get("assumptions_to_be_reasserted_per_completed_capture_slice")
            != expected_extension[
                "assumptions_to_be_reasserted_per_completed_capture_slice"
            ]
            or extension.get("parent_assumption_sha256")
            != expected_extension["parent_assumption_sha256"]
            or extension.get("assumption_application_policy")
            != expected_extension["assumption_application_policy"]
            or extension.get("limitations") != expected_extension["limitations"]
            or extension.get("extension_authorized") is not True
            or extension.get("applied_capture_slices") != []
            or extension.get("future_assumptions_asserted") is not False
            or any(extension.get(name) is not False for name in FIXED_FALSE)
        ):
            raise LedgerIntegrityError("Campaign v2 exemption extension is invalid")
        if len(rows) < 3:
            return
        plan = rows[2]["payload"]
        parameter_json = _canonical_bounded_json_mapping(
            proposal["strategy_parameters"], "parameter_space"
        )
        evaluation = _normalise_evaluation_protocol(proposal["evaluation_protocol"])
        try:
            access_not_before = _timestamp(
                plan.get("data_access_not_before"), "data_access_not_before"
            )
            untouched_start = date.fromisoformat(
                _required(plan.get("untouched_test_start"), "untouched_test_start", 10)
            )
        except ValueError as error:
            raise LedgerIntegrityError("Campaign v2 preregistration dates are invalid") from error
        if (
            plan.get("proposal_sha256") != PROPOSAL_SHA256
            or plan.get("approval_record_hash") != rows[0]["record_hash"]
            or plan.get("exemption_extension_record_hash") != rows[1]["record_hash"]
            or plan.get("campaign_policy_version") != CAMPAIGN_POLICY_VERSION
            or plan.get("record_type")
            != "CAMPAIGN_V2_FUTURE_WINDOW_QUARANTINE_PREREGISTRATION"
            or plan.get("status")
            != "PREREGISTERED_APPROVED_CONTROLS_NO_DATA_ACCESS"
            or plan.get("git_worktree_clean") is not True
            or plan.get("v1_closure_record_hash") != V1_CLOSURE_RECORD_SHA256
            or plan.get("v1_preregistration_record_hash")
            != V1_PREREGISTRATION_RECORD_SHA256
            or plan.get("v1_entitlement_metadata_sha256")
            != V1_ENTITLEMENT_METADATA_SHA256
            or plan.get("entitlement_metadata_sha256")
            != V1_ENTITLEMENT_METADATA_SHA256
            or _hash(plan.get("entitlement_metadata"))
            != V1_ENTITLEMENT_METADATA_SHA256
            or plan.get("target_basket") != proposal["target_basket"]
            or plan.get("acquisition_start") != proposal["acquisition_start"]
            or plan.get("acquisition_end") != proposal["acquisition_end"]
            or plan.get("splits") != proposal["splits"]
            or plan.get("expected_request_count")
            != proposal["expected_capture_counts"]["TOTAL"]
            or plan.get("parameter_space_canonical_json") != parameter_json
            or plan.get("parameter_space_sha256")
            != hashlib.sha256(parameter_json.encode("utf-8")).hexdigest()
            or plan.get("evaluation_protocol") != evaluation
            or plan.get("evaluation_protocol_sha256") != _hash(evaluation)
            or plan.get("maximum_chunk_days") != 31
            or plan.get("provider_id") != PROVIDER_ID
            or plan.get("provider_dataset_id") != PROVIDER_DATASET_ID
            or plan.get("endpoint_template") != ENDPOINT_TEMPLATE
            or plan.get("strategy_entrypoint")
            != (
                "core.research.conservative_baseline_strategy:"
                "ConservativeBaselineStrategy"
            )
            or plan.get("strategy_source_path")
            != "core/research/conservative_baseline_strategy.py"
            or plan.get("strategy_version") != proposal["strategy_version"]
            or plan.get("quarantine_only") is not True
            or plan.get("capture_chain_binding_required") is not True
            or plan.get("entitlement_revalidation_required_before_data_call")
            is not True
            or plan.get("request_slice_must_be_complete_and_historical_at_fetch_time")
            is not True
            or plan.get("future_dated_provider_request_allowed") is not False
            or plan.get("purge_observations") != 1
            or plan.get("embargo_observations") != 1
            or plan.get("maximum_untouched_test_evaluations") != 1
            or untouched_start
            != date.fromisoformat(proposal["splits"][-1]["start"])
            or access_not_before.date() >= untouched_start
            or plan.get("preregistered_before_untouched_window") is not True
            or plan.get("quarantine_root_relative_path")
            != QUARANTINE_ROOT_RELATIVE_PATH.as_posix()
            or plan.get("control_ledger_relative_path")
            != CONTROL_LEDGER_RELATIVE_PATH.as_posix()
            or plan.get("preregistration_id") != _plan_id(plan)
            or SHA256_PATTERN.fullmatch(str(plan.get("strategy_source_sha256", "")))
            is None
            or GIT_REVISION_PATTERN.fullmatch(str(plan.get("git_revision", ""))) is None
            or any(plan.get(name) is not False for name in FIXED_FALSE)
        ):
            raise LedgerIntegrityError("Campaign v2 preregistration is invalid")
        if len(rows) < 4:
            return
        supersession = rows[3]["payload"]
        revision = revision_proposal_package()
        _verified_revision_window(revision)
        if (
            supersession.get("status")
            != "SUPERSEDED_CALENDAR_CONFLICT_NO_DATA_ACCESSED"
            or supersession.get("superseded_proposal_sha256") != PROPOSAL_SHA256
            or supersession.get("superseded_preregistration_record_hash")
            != rows[2]["record_hash"]
            or supersession.get("superseded_preregistration_id")
            != rows[2]["payload"]["preregistration_id"]
            or supersession.get("replacement_proposal_sha256")
            != revision["proposal_sha256"]
            or supersession.get("replacement_campaign_policy_version")
            != REVISION_CAMPAIGN_POLICY_VERSION
            or supersession.get("replacement_approval_status")
            != "PENDING_EXPLICIT_HUMAN_APPROVAL"
            or REVISION_APPROVAL_STATUS != "PENDING_EXPLICIT_HUMAN_APPROVAL"
            or supersession.get("calendar_conflict_identified_on")
            != CALENDAR_CONFLICT_IDENTIFIED_ON
            or supersession.get("reason_codes")
            != revision["supersession_reason_codes"]
            or supersession.get("replacement_window")
            != {
                "acquisition_start": revision["acquisition_start"],
                "acquisition_end": revision["acquisition_end"],
                "splits": revision["splits"],
            }
            or supersession.get("replacement_test_semantic_role")
            != "SEALED_RETROSPECTIVE_TEST"
            or supersession.get("replacement_preregistration_registered") is not False
            or supersession.get("superseded_quarantine_file_count") != 0
            or supersession.get("provider_request_made_under_superseded_controls")
            is not False
            or supersession.get("replacement_proposal_module_sha256") is None
            or SHA256_PATTERN.fullmatch(
                str(supersession.get("replacement_proposal_module_sha256", ""))
            )
            is None
            or GIT_REVISION_PATTERN.fullmatch(
                str(supersession.get("git_revision", ""))
            )
            is None
            or any(supersession.get(name) is not False for name in FIXED_FALSE)
        ):
            raise LedgerIntegrityError("Campaign v2 supersession is invalid")

    def register_approved_package(
        self,
        *,
        v1_exemption_ledger: ResearchExemptionLedger,
        v1_preregistration_ledger: HistoricalQuarantinePreregistrationLedger,
        authorized_by: str,
        approval_text: str,
    ) -> dict[str, Any]:
        actor = _required(authorized_by, "authorized_by", 150)
        if approval_text != APPROVAL_TEXT:
            raise ValueError("approval_text does not match the approved proposal hash")
        proposal = proposal_package()
        if proposal["proposal_sha256"] != PROPOSAL_SHA256:
            raise LedgerIntegrityError("approved Campaign v2 proposal hash changed")
        registration_now = _timestamp(self._clock(), "registration clock")
        untouched_start = _require_before_untouched_window(
            registration_now, proposal
        )
        closure, exemption, parent_plan = _verified_v1_parents(
            v1_exemption_ledger=v1_exemption_ledger,
            v1_preregistration_ledger=v1_preregistration_ledger,
        )
        existing = self.records()
        if existing:
            if len(existing) == len(CONTROL_EVENT_SEQUENCE):
                raise ValueError("Campaign v2 controls were superseded")
            if existing[0]["payload"].get("authorized_by") != actor:
                raise LedgerIntegrityError("Campaign v2 approval authority differs")
            if len(existing) == len(EVENT_SEQUENCE):
                return self._inactive_plan(existing[-1])
        if self._worktree_clean_resolver(self.repository_root) is not True:
            raise ValueError("Git worktree must be clean before Campaign v2 registration")
        revision = _required(
            self._git_revision_resolver(self.repository_root), "git_revision", 64
        ).lower()
        if GIT_REVISION_PATTERN.fullmatch(revision) is None:
            raise ValueError("git_revision must be a full lowercase Git commit ID")
        proposal_source = (
            self.repository_root
            / "core/research/conservative_baseline_campaign_v2_proposal.py"
        )
        approval_payload = {
            "authorized_by": actor,
            "authorization_basis": "EXACT_USER_APPROVAL_IN_CODEX_TASK",
            "approval_text": APPROVAL_TEXT,
            "approval_scope": "CONTROL_PACKAGE_ONLY_NO_DATA_CALL_NO_EVALUATION",
            "proposal_sha256": PROPOSAL_SHA256,
            "campaign_policy_version": CAMPAIGN_POLICY_VERSION,
            "proposal_module_sha256": hashlib.sha256(
                proposal_source.read_bytes()
            ).hexdigest(),
            "git_revision": revision,
            **_fixed_false(),
        }
        approval = self._append_expected(EVENT_SEQUENCE[0], approval_payload)
        extension_template = proposal["research_exemption_extension"]
        extension_payload = {
            "status": "AUTHORIZED_NOT_APPLIED_TO_ANY_CAPTURE",
            "approval_record_hash": approval["record_hash"],
            "proposal_sha256": PROPOSAL_SHA256,
            "v1_closure_record_hash": closure["record_hash"],
            "parent_exemption_id": exemption["payload"]["exemption_id"],
            "parent_exemption_record_hash": exemption["record_hash"],
            "target_basket": extension_template["target_basket"],
            "scope_start": extension_template["scope_start"],
            "scope_end": extension_template["scope_end"],
            "assumptions_to_be_reasserted_per_completed_capture_slice": (
                extension_template[
                    "assumptions_to_be_reasserted_per_completed_capture_slice"
                ]
            ),
            "parent_assumption_sha256": extension_template[
                "parent_assumption_sha256"
            ],
            "assumption_application_policy": extension_template[
                "assumption_application_policy"
            ],
            "limitations": extension_template["limitations"],
            "extension_authorized": True,
            "applied_capture_slices": [],
            "future_assumptions_asserted": False,
            **_fixed_false(),
        }
        extension = self._append_expected(EVENT_SEQUENCE[1], extension_payload)
        source_path, source_hash = _strategy_source_identity(
            self.repository_root, "core/research/conservative_baseline_strategy.py"
        )
        parameter_json = _canonical_bounded_json_mapping(
            proposal["strategy_parameters"], "parameter_space"
        )
        evaluation = _normalise_evaluation_protocol(proposal["evaluation_protocol"])
        now = registration_now
        actual_now = datetime.now(timezone.utc)
        if not actual_now - MAX_CLOCK_SKEW <= now <= actual_now + MAX_CLOCK_SKEW:
            raise ValueError("registration clock must match actual append time")
        plan_payload: dict[str, Any] = {
            "record_type": "CAMPAIGN_V2_FUTURE_WINDOW_QUARANTINE_PREREGISTRATION",
            "status": "PREREGISTERED_APPROVED_CONTROLS_NO_DATA_ACCESS",
            "proposal_sha256": PROPOSAL_SHA256,
            "approval_record_hash": approval["record_hash"],
            "exemption_extension_record_hash": extension["record_hash"],
            "v1_closure_record_hash": closure["record_hash"],
            "v1_preregistration_record_hash": parent_plan["record_hash"],
            "v1_entitlement_metadata_sha256": parent_plan[
                "entitlement_metadata_sha256"
            ],
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
            "purge_observations": evaluation["purge_observations"],
            "embargo_observations": evaluation["embargo_observations"],
            "maximum_untouched_test_evaluations": evaluation[
                "maximum_untouched_test_evaluations"
            ],
            "untouched_test_start": untouched_start.isoformat(),
            "preregistered_before_untouched_window": True,
            "entitlement_metadata": parent_plan["entitlement_metadata"],
            "entitlement_metadata_sha256": parent_plan[
                "entitlement_metadata_sha256"
            ],
            "entitlement_revalidation_required_before_data_call": True,
            "unadjusted_daily_bars_only": True,
            "sort_order": "ASC",
            "request_record_limit": 120,
            "maximum_chunk_days": 31,
            "request_slice_must_be_complete_and_historical_at_fetch_time": True,
            "future_dated_provider_request_allowed": False,
            "quarantine_only": True,
            "capture_chain_binding_required": True,
            "quarantine_root_relative_path": (
                QUARANTINE_ROOT_RELATIVE_PATH.as_posix()
            ),
            "control_ledger_relative_path": CONTROL_LEDGER_RELATIVE_PATH.as_posix(),
            "data_access_not_before": now.isoformat(timespec="microseconds"),
            **_fixed_false(),
        }
        plan_payload["preregistration_id"] = _plan_id(plan_payload)
        plan = self._append_expected(EVENT_SEQUENCE[2], plan_payload)
        return self._inactive_plan(plan)

    @staticmethod
    def _inactive_plan(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            **record["payload"],
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "registered_at": record["recorded_at"],
            "record_hash": record["record_hash"],
        }

    def require(self, preregistration_id: str) -> dict[str, Any]:
        identifier = _required(preregistration_id, "preregistration_id", 100)
        records = self.records()
        if ORIGINAL_CONTROLS_RETIRED or len(records) == len(CONTROL_EVENT_SEQUENCE):
            raise ValueError("Campaign v2 controls were superseded")
        if len(records) != len(EVENT_SEQUENCE):
            raise ValueError("Campaign v2 control package is incomplete")
        record = records[-1]
        if record["payload"]["preregistration_id"] != identifier:
            raise ValueError("verified Campaign v2 preregistration was not found")
        return {
            **record["payload"],
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "registered_at": record["recorded_at"],
            "record_hash": record["record_hash"],
        }

    def supersede_calendar_conflict(self) -> dict[str, Any]:
        """Terminally disable the approved future-window package without data access."""

        records = self.records()
        if len(records) == len(CONTROL_EVENT_SEQUENCE):
            return records[-1]
        if len(records) != len(EVENT_SEQUENCE):
            raise LedgerIntegrityError(
                "Campaign v2 must have the complete approved chain before supersession"
            )
        unresolved_quarantine_root = (
            self.repository_root / QUARANTINE_ROOT_RELATIVE_PATH
        )
        quarantine_root = unresolved_quarantine_root.resolve()
        if quarantine_root.exists() and (
            unresolved_quarantine_root.is_symlink()
            or not quarantine_root.is_dir()
            or any(quarantine_root.iterdir())
        ):
            raise LedgerIntegrityError(
                "Campaign v2 quarantine must be an empty directory before supersession"
            )
        if self._worktree_clean_resolver(self.repository_root) is not True:
            raise ValueError("Git worktree must be clean before Campaign v2 supersession")
        revision_id = _required(
            self._git_revision_resolver(self.repository_root), "git_revision", 64
        ).lower()
        if GIT_REVISION_PATTERN.fullmatch(revision_id) is None:
            raise ValueError("git_revision must be a full lowercase Git commit ID")
        replacement = revision_proposal_package()
        _verified_revision_window(replacement)
        if REVISION_APPROVAL_STATUS != "PENDING_EXPLICIT_HUMAN_APPROVAL":
            raise LedgerIntegrityError(
                "Campaign v2 revision must remain pending explicit approval"
            )
        replacement_source = (
            self.repository_root
            / "core/research/conservative_baseline_campaign_v2_revision_proposal.py"
        )
        payload = {
            "status": "SUPERSEDED_CALENDAR_CONFLICT_NO_DATA_ACCESSED",
            "superseded_proposal_sha256": PROPOSAL_SHA256,
            "superseded_preregistration_record_hash": records[2]["record_hash"],
            "superseded_preregistration_id": records[2]["payload"][
                "preregistration_id"
            ],
            "calendar_conflict_identified_on": CALENDAR_CONFLICT_IDENTIFIED_ON,
            "reason_codes": replacement["supersession_reason_codes"],
            "replacement_proposal_sha256": replacement["proposal_sha256"],
            "replacement_campaign_policy_version": REVISION_CAMPAIGN_POLICY_VERSION,
            "replacement_approval_status": "PENDING_EXPLICIT_HUMAN_APPROVAL",
            "replacement_proposal_module_sha256": hashlib.sha256(
                replacement_source.read_bytes()
            ).hexdigest(),
            "replacement_window": {
                "acquisition_start": replacement["acquisition_start"],
                "acquisition_end": replacement["acquisition_end"],
                "splits": replacement["splits"],
            },
            "replacement_test_semantic_role": "SEALED_RETROSPECTIVE_TEST",
            "replacement_preregistration_registered": False,
            "superseded_quarantine_file_count": 0,
            "provider_request_made_under_superseded_controls": False,
            "git_revision": revision_id,
            **_fixed_false(),
        }
        return self._append_expected(SUPERSESSION_EVENT, payload)

    def _append_expected(
        self, event_type: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        expected_index = CONTROL_EVENT_SEQUENCE.index(event_type)
        _private_directory(self.path.parent)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock = os.open(lock_path, os.O_CREAT | os.O_RDWR | _no_follow(), 0o600)
        try:
            os.fchmod(lock, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            existing = self.records()
            if len(existing) > expected_index:
                record = existing[expected_index]
                if record["event_type"] != event_type or record["payload"] != dict(payload):
                    raise LedgerIntegrityError(
                        f"existing {event_type} differs from the approved package"
                    )
                return record
            if len(existing) != expected_index:
                raise LedgerIntegrityError("Campaign v2 control events are out of order")
            recorded = _timestamp(self._clock(), "recording clock")
            now = datetime.now(timezone.utc)
            if not now - MAX_CLOCK_SKEW <= recorded <= now + MAX_CLOCK_SKEW:
                raise ValueError("recording clock must match actual append time")
            material = {
                "schema_version": SCHEMA_VERSION,
                "policy_version": POLICY_VERSION,
                "event_type": event_type,
                "recorded_at": recorded.isoformat(timespec="microseconds"),
                "payload": json.loads(_canonical_json(dict(payload))),
                "previous_hash": (
                    existing[-1]["record_hash"] if existing else GENESIS_HASH
                ),
            }
            complete = {**material, "record_hash": _record_hash(material)}
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


def initialize_campaign_v2_quarantine_root(
    repository_root: str | Path,
    *,
    admitted_store_roots: Sequence[str | Path],
) -> Path:
    """Create only the empty owner-only v2 raw root after proving isolation."""

    root = Path(repository_root).resolve()
    target = (root / QUARANTINE_ROOT_RELATIVE_PATH).resolve()
    control_ledger = (root / CONTROL_LEDGER_RELATIVE_PATH).resolve()
    v1 = (root / V1_QUARANTINE_ROOT_RELATIVE_PATH).resolve()
    if (
        target == control_ledger
        or target in control_ledger.parents
        or control_ledger in target.parents
    ):
        raise LedgerIntegrityError("Campaign v2 control ledger overlaps quarantine")
    if target == v1 or target in v1.parents or v1 in target.parents:
        raise LedgerIntegrityError("Campaign v2 and v1 quarantine roots overlap")
    if not admitted_store_roots:
        raise ValueError("at least one admitted store root must be declared")
    declared_admitted = [Path(value).resolve() for value in admitted_store_roots]
    known_admitted = [
        (root / relative).resolve()
        for relative in KNOWN_ADMITTED_STORE_RELATIVE_PATHS
    ]
    for admitted in (*known_admitted, *declared_admitted):
        if target == admitted or target in admitted.parents or admitted in target.parents:
            raise LedgerIntegrityError("Campaign v2 quarantine and admitted roots overlap")
    _private_directory(target)
    if any(target.iterdir()):
        raise LedgerIntegrityError("Campaign v2 quarantine root must start empty")
    return target
