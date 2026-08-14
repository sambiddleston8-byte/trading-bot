from __future__ import annotations

"""Inert contract linking each sealed research engine to its replay inputs.

This module reads nothing, writes nothing and executes no replay.  It states,
for every engine key the sealed replay context pins, how that engine obtains its
inputs and which authenticated point-in-time artifact roles it would need, then
compares those needs with the roles the sealed dataset admission ledger can
admit.  Engines whose needs cannot yet be met are reported as uncovered; they
are never treated as satisfied.

Callers must pass an admission record obtained from
``ReplayDatasetAdmissionLedger.verify()``.  This module structurally checks the
identity, version and fixed-boundary fields such a record carries, but it opens
no ledger file and re-verifies no hash chain: chain verification remains the
caller's prerequisite.
"""

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Mapping

from core.orchestration.active_pipeline_replay_context import REQUIRED_ENGINE_KEYS
from core.orchestration.replay_dataset_admission import (
    OPTIONAL_ROLES,
    POLICY_VERSION as ADMISSION_POLICY_VERSION,
    REQUIRED_ROLES,
    SCHEMA_VERSION as ADMISSION_SCHEMA_VERSION,
)


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "active-pipeline-replay-input-coverage-v2"
RECORD_TYPE = "ACTIVE_PIPELINE_REPLAY_INPUT_COVERAGE"
ADMISSION_RECORD_TYPE = "SEALED_REPLAY_DATASET_METADATA_ADMISSION"
ADMISSION_STATUS = "PREREQUISITES_LINKED_AWAITING_CONTROLLED_REPLAY"

_COVERAGE_AUTHORITY = object()

# How an engine obtains its inputs.  An engine may combine kinds; only engines
# whose kinds include DATASET_ARTIFACTS consume admitted dataset roles.
DATASET_ARTIFACTS = "DATASET_ARTIFACTS"
DERIVED_ONLY = "DERIVED_ONLY"
SEALED_LEARNING_STATE = "SEALED_LEARNING_STATE"
SEALED_SOURCE_EVIDENCE = "SEALED_SOURCE_EVIDENCE"
INPUT_KINDS = frozenset(
    {DATASET_ARTIFACTS, DERIVED_ONLY, SEALED_LEARNING_STATE, SEALED_SOURCE_EVIDENCE}
)

ADMITTED_ROLES = frozenset(REQUIRED_ROLES) | frozenset(OPTIONAL_ROLES)

# Identity and boundary fields every real admission record carries.
ADMISSION_IDENTITY_FIELDS = (
    "admission_id",
    "replay_plan_id",
    "replay_plan_record_hash",
    "dataset_manifest_content_evidence_id",
    "dataset_commitment_sha256",
    "record_hash",
)
ADMISSION_FIXED_TRUE = (
    "required_artifact_roles_present",
    "authenticated_source_bytes_verified",
    "approved_source_and_terms_linked",
    "bounded_universe_intervals_reconciled",
    "source_attestation_only",
)
ADMISSION_FIXED_FALSE = (
    "ground_truth_independently_proven",
    "evaluation_observations_interpreted",
    "model_trained",
    "replay_executed",
    "performance_calculated",
    "performance_claim_allowed",
    "paper_broker_submission_enabled",
    "broker_connection_allowed",
    "live_trading_enabled",
)

FIXED_FALSE_FIELDS = (
    "evaluation_dataset_opened",
    "dataset_artifact_bytes_read",
    "replay_executed",
    "performance_calculated",
    "performance_claim_allowed",
    "paper_broker_submission_enabled",
    "broker_connection_allowed",
    "order_submitted",
    "live_trading_enabled",
)

# Exactly the 22 engine keys the sealed replay context pins, mapped to their
# dependency kinds and, for artifact-backed engines, their required roles.
# Each mapping below is justified by the engine's own source or its pipeline
# call site; see docs/PROJECT_STATUS.md for the residual uncertainty note.
REPLAY_ENGINE_INPUTS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    # FundamentalAnalysisEngine imports AnalystSource and EarningsSource
    # alongside financial data, so reported fundamentals alone are insufficient.
    "fundamental": (
        frozenset({DATASET_ARTIFACTS}),
        frozenset(
            {
                "POINT_IN_TIME_FUNDAMENTALS",
                "POINT_IN_TIME_ANALYST_AND_EARNINGS_ESTIMATES",
            }
        ),
    ),
    # ValuationEngine needs current price and shares outstanding and imports the
    # same analyst/earnings sources.
    "valuation": (
        frozenset({DATASET_ARTIFACTS}),
        frozenset(
            {
                "POINT_IN_TIME_FUNDAMENTALS",
                "TOTAL_RETURN_PRICES",
                "POINT_IN_TIME_ANALYST_AND_EARNINGS_ESTIMATES",
            }
        ),
    ),
    # InvestmentDecisionEngine.analyse(fundamental, valuation).
    "decision": (frozenset({DERIVED_ONLY}), frozenset()),
    "catalyst": (
        frozenset({DATASET_ARTIFACTS}),
        frozenset({"POINT_IN_TIME_CORPORATE_EVENTS"}),
    ),
    "news": (frozenset({DATASET_ARTIFACTS}), frozenset({"POINT_IN_TIME_NEWS"})),
    # CatalystEvidenceBridge imports only datetime and transforms catalysts.
    "catalyst_bridge": (frozenset({DERIVED_ONLY}), frozenset()),
    "catalyst_validation": (frozenset({DERIVED_ONLY}), frozenset()),
    "catalyst_probability": (frozenset({DERIVED_ONLY}), frozenset()),
    # ThesisChallenger imports only datetime; the ticker it receives is a label.
    "thesis": (frozenset({DERIVED_ONLY}), frozenset()),
    "synthesis": (frozenset({DERIVED_ONLY}), frozenset()),
    "audit": (frozenset({DERIVED_ONLY}), frozenset()),
    # MarketSignalEngine scores Volume Confirmation, Volume Ratio 20d To 60d and
    # Support Resistance, which require raw OHLCV session bars.
    "market_signals": (
        frozenset({DATASET_ARTIFACTS}),
        frozenset(
            {
                "TOTAL_RETURN_PRICES",
                "RAW_DAILY_SESSION_BARS",
                "MARKET_CALENDARS_AND_HALTS",
            }
        ),
    ),
    "sentiment": (frozenset({DERIVED_ONLY}), frozenset()),
    # MarketRegimeEngine.classify reads only the benchmark Close series.
    "market_regime": (
        frozenset({DATASET_ARTIFACTS}),
        frozenset({"TOTAL_RETURN_PRICES", "MARKET_CALENDARS_AND_HALTS"}),
    ),
    "macro_environment": (
        frozenset({DATASET_ARTIFACTS}),
        frozenset({"POINT_IN_TIME_MACRO_SERIES"}),
    ),
    "specialist_research": (
        frozenset({DATASET_ARTIFACTS}),
        frozenset({"POINT_IN_TIME_SPECIALIST_RESEARCH"}),
    ),
    "master_decision": (frozenset({DERIVED_ONLY}), frozenset()),
    # OutcomeLearningAdapter.for_decision(decision, path) combines a prior-stage
    # decision with the sealed learning-state file.
    "outcome_learning": (
        frozenset({DERIVED_ONLY, SEALED_LEARNING_STATE}),
        frozenset(),
    ),
    # ValuationQualityEngine.assess(valuation, decision, source_ledger).
    "valuation_quality": (
        frozenset({DERIVED_ONLY, SEALED_SOURCE_EVIDENCE}),
        frozenset(),
    ),
    "authenticated_sources": (frozenset({SEALED_SOURCE_EVIDENCE}), frozenset()),
    "diagnostics": (frozenset({DERIVED_ONLY}), frozenset()),
    "supplemental_evidence": (
        frozenset({DATASET_ARTIFACTS}),
        frozenset({"POINT_IN_TIME_SUPPLEMENTAL_PROVIDER_EVIDENCE"}),
    ),
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _snapshot() -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    """Freeze the whole contract so later mutation cannot change an answer."""

    return tuple(
        (key, tuple(sorted(kinds)), tuple(sorted(roles)))
        for key, (kinds, roles) in sorted(REPLAY_ENGINE_INPUTS.items())
    )


def engine_input_kinds() -> dict[str, tuple[str, ...]]:
    """Return how each engine obtains its inputs, sorted."""

    return {key: kinds for key, kinds, _ in _snapshot()}


def engine_input_roles() -> dict[str, tuple[str, ...]]:
    """Return the declared artifact roles for every engine, sorted."""

    return {key: roles for key, _, roles in _snapshot()}


def declared_artifact_roles() -> frozenset[str]:
    """Return every artifact role this contract declares across all engines."""

    return frozenset().union(*(roles for _, roles in REPLAY_ENGINE_INPUTS.values()))


def unadmittable_roles() -> frozenset[str]:
    """Return declared roles the admission ledger has no role definition for."""

    return declared_artifact_roles() - ADMITTED_ROLES


def verify_contract_integrity() -> None:
    """Fail closed if the contract has drifted from the sealed engine keys."""

    keys = set(REPLAY_ENGINE_INPUTS)
    required = set(REQUIRED_ENGINE_KEYS)
    if keys != required:
        missing = sorted(required - keys)
        extra = sorted(keys - required)
        raise ValueError(
            "replay input coverage must map exactly the sealed engine keys "
            f"(missing={missing}, unexpected={extra})"
        )
    for key, value in REPLAY_ENGINE_INPUTS.items():
        if not isinstance(value, tuple) or len(value) != 2:
            raise ValueError(f"{key} must declare exactly one kind set and one role set")
        kinds, roles = value
        if not isinstance(kinds, frozenset) or not kinds or not kinds <= INPUT_KINDS:
            raise ValueError(f"{key} declares an unsupported input kind")
        if not isinstance(roles, frozenset) or any(
            not isinstance(role, str) or not role or role != role.strip().upper()
            for role in roles
        ):
            raise ValueError(f"{key} declares a malformed artifact role")
        if DATASET_ARTIFACTS in kinds and not roles:
            raise ValueError(f"{key} consumes dataset artifacts but declares no role")
        if DATASET_ARTIFACTS not in kinds and roles:
            raise ValueError(f"{key} declares artifact roles but is not artifact-backed")


def _admitted_roles(admission_record: Mapping[str, Any]) -> tuple[str, ...]:
    """Structurally check one already-chain-verified admission record."""

    if not isinstance(admission_record, Mapping):
        raise ValueError("a verified replay-dataset admission record is required")
    if admission_record.get("schema_version") != ADMISSION_SCHEMA_VERSION:
        raise ValueError("admission record schema version is unsupported")
    if admission_record.get("policy_version") != ADMISSION_POLICY_VERSION:
        raise ValueError("admission record policy version is unsupported")
    if admission_record.get("record_type") != ADMISSION_RECORD_TYPE:
        raise ValueError("admission record type is not a sealed dataset admission")
    if admission_record.get("status") != ADMISSION_STATUS:
        raise ValueError("admission record status is not the awaiting-replay status")
    for name in ADMISSION_IDENTITY_FIELDS:
        if not str(admission_record.get(name) or "").strip():
            raise ValueError(f"admission record is missing its {name} identity field")
    for name in ADMISSION_FIXED_TRUE:
        if admission_record.get(name) is not True:
            raise ValueError(f"admission record does not assert {name}")
    for name in ADMISSION_FIXED_FALSE:
        if admission_record.get(name) is not False:
            raise ValueError(f"admission record has forbidden authority: {name}")
    artifacts = admission_record.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("admission record must list its authenticated artifacts")
    roles: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or set(artifact) != {
            "role",
            "content_evidence_id",
        }:
            raise ValueError(
                "each admitted artifact must contain exactly role and content_evidence_id"
            )
        role = str(artifact.get("role") or "").strip().upper()
        identifier = str(artifact.get("content_evidence_id") or "").strip()
        if not role or not identifier:
            raise ValueError("each admitted artifact must name a role and content id")
        if role not in ADMITTED_ROLES:
            raise ValueError(f"admission record contains an unknown artifact role: {role}")
        if role in roles:
            raise ValueError(f"admission record repeats artifact role: {role}")
        roles.append(role)
    missing = sorted(set(REQUIRED_ROLES) - set(roles))
    if missing:
        raise ValueError(
            f"admission record is missing required artifact roles: {missing}"
        )
    return tuple(sorted(roles))


def _derive(
    contract_snapshot: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...],
    admitted_roles: tuple[str, ...],
) -> dict[str, tuple[Any, ...]]:
    """Derive every result field from the snapshot and the admitted roles."""

    if {key for key, _, _ in contract_snapshot} != set(REQUIRED_ENGINE_KEYS):
        raise ValueError("contract snapshot must cover exactly the sealed engine keys")
    available = set(admitted_roles)
    declared: set[str] = set()
    dataset: list[str] = []
    covered: list[str] = []
    uncovered: list[tuple[str, str]] = []
    derived: list[str] = []
    learning: list[str] = []
    source_evidence: list[str] = []

    for key, kinds, roles in sorted(contract_snapshot):
        if DERIVED_ONLY in kinds:
            derived.append(key)
        if SEALED_LEARNING_STATE in kinds:
            learning.append(key)
        if SEALED_SOURCE_EVIDENCE in kinds:
            source_evidence.append(key)
        if DATASET_ARTIFACTS not in kinds:
            continue
        dataset.append(key)
        declared.update(roles)
        absent = sorted(role for role in roles if role not in available)
        if absent:
            uncovered.extend((key, role) for role in absent)
        else:
            covered.append(key)

    return {
        "covered_engine_keys": tuple(covered),
        "uncovered_engine_role_pairs": tuple(sorted(uncovered)),
        "unadmittable_roles": tuple(sorted(declared - ADMITTED_ROLES)),
        "dataset_artifact_engine_keys": tuple(dataset),
        "derived_only_engine_keys": tuple(derived),
        "sealed_learning_state_engine_keys": tuple(learning),
        "sealed_source_evidence_engine_keys": tuple(source_evidence),
    }


@dataclass(frozen=True, slots=True)
class ReplayInputCoverage:
    """Inert coverage answer; it admits nothing and executes nothing."""

    admitted_roles: tuple[str, ...]
    covered_engine_keys: tuple[str, ...]
    uncovered_engine_role_pairs: tuple[tuple[str, str], ...]
    unadmittable_roles: tuple[str, ...]
    dataset_artifact_engine_keys: tuple[str, ...]
    derived_only_engine_keys: tuple[str, ...]
    sealed_learning_state_engine_keys: tuple[str, ...]
    sealed_source_evidence_engine_keys: tuple[str, ...]
    contract_snapshot: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]
    _authority: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._authority is not _COVERAGE_AUTHORITY:
            raise PermissionError(
                "ReplayInputCoverage must be issued by verify_replay_input_coverage"
            )
        # Spend the authority immediately so no copy of this object -- including
        # one made by dataclasses.replace -- can inherit a usable issuance right.
        object.__setattr__(self, "_authority", None)
        expected = _derive(self.contract_snapshot, self.admitted_roles)
        if {name: getattr(self, name) for name in expected} != expected:
            raise ValueError(
                "coverage result fields do not match the sealed contract snapshot"
            )

    @property
    def is_complete(self) -> bool:
        return not self.uncovered_engine_role_pairs

    @property
    def uncovered_engine_keys(self) -> tuple[str, ...]:
        return tuple(sorted({key for key, _ in self.uncovered_engine_role_pairs}))

    def engine_input_kinds(self) -> dict[str, list[str]]:
        return {key: list(kinds) for key, kinds, _ in self.contract_snapshot}

    def engine_input_roles(self) -> dict[str, list[str]]:
        return {key: list(roles) for key, _, roles in self.contract_snapshot}

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "record_type": RECORD_TYPE,
            "status": (
                "REPLAY_INPUT_COVERAGE_COMPLETE_NOT_EXECUTED"
                if self.is_complete
                else "REPLAY_INPUT_COVERAGE_INCOMPLETE_NOT_EXECUTED"
            ),
            "admitted_roles": list(self.admitted_roles),
            "covered_engine_keys": list(self.covered_engine_keys),
            "uncovered_engine_role_pairs": [
                list(pair) for pair in self.uncovered_engine_role_pairs
            ],
            "uncovered_engine_keys": list(self.uncovered_engine_keys),
            "unadmittable_roles": list(self.unadmittable_roles),
            "dataset_artifact_engine_keys": list(self.dataset_artifact_engine_keys),
            "derived_only_engine_keys": list(self.derived_only_engine_keys),
            "sealed_learning_state_engine_keys": list(
                self.sealed_learning_state_engine_keys
            ),
            "sealed_source_evidence_engine_keys": list(
                self.sealed_source_evidence_engine_keys
            ),
            "engine_input_kinds": self.engine_input_kinds(),
            "engine_input_roles": self.engine_input_roles(),
            "faithful_replay_input_coverage_complete": self.is_complete,
            "chain_verification_is_caller_prerequisite": True,
            "contract_is_inert": True,
            **{field_name: False for field_name in FIXED_FALSE_FIELDS},
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.as_dict())

    def coverage_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def verify_replay_input_coverage(
    admission_record: Mapping[str, Any],
) -> ReplayInputCoverage:
    """Compare every sealed engine's declared inputs with one admission record.

    ``admission_record`` must come from ``ReplayDatasetAdmissionLedger.verify()``.
    Its identity, version and fixed-boundary fields are checked structurally, but
    no dataset byte, blob or ledger file is opened and no hash chain is
    re-verified here: that remains the caller's prerequisite.  A malformed
    record, an unknown or duplicate artifact role, or a missing required role
    fails closed.  Declared roles the admission ledger cannot admit are
    reported, never assumed satisfied.
    """

    verify_contract_integrity()
    admitted = _admitted_roles(admission_record)
    snapshot = _snapshot()

    return ReplayInputCoverage(
        admitted_roles=admitted,
        contract_snapshot=snapshot,
        **_derive(snapshot, admitted),
        _authority=_COVERAGE_AUTHORITY,
    )
