from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from core.orchestration.active_pipeline_replay_context import REQUIRED_ENGINE_KEYS
from core.orchestration.replay_dataset_admission import OPTIONAL_ROLES, REQUIRED_ROLES
from core.orchestration import replay_input_coverage as module
from core.orchestration.replay_input_coverage import (
    ADMISSION_FIXED_FALSE,
    ADMISSION_FIXED_TRUE,
    ADMISSION_IDENTITY_FIELDS,
    DATASET_ARTIFACTS,
    DERIVED_ONLY,
    FIXED_FALSE_FIELDS,
    REPLAY_ENGINE_INPUTS,
    SEALED_LEARNING_STATE,
    SEALED_SOURCE_EVIDENCE,
    ReplayInputCoverage,
    declared_artifact_roles,
    engine_input_kinds,
    engine_input_roles,
    unadmittable_roles,
    verify_contract_integrity,
    verify_replay_input_coverage,
)


EXPECTED_DATASET = (
    "catalyst",
    "fundamental",
    "macro_environment",
    "market_regime",
    "market_signals",
    "news",
    "specialist_research",
    "supplemental_evidence",
    "valuation",
)
EXPECTED_COVERED = ("market_regime",)
EXPECTED_UNCOVERED = (
    ("catalyst", "POINT_IN_TIME_CORPORATE_EVENTS"),
    ("fundamental", "POINT_IN_TIME_ANALYST_AND_EARNINGS_ESTIMATES"),
    ("macro_environment", "POINT_IN_TIME_MACRO_SERIES"),
    ("market_signals", "RAW_DAILY_SESSION_BARS"),
    ("news", "POINT_IN_TIME_NEWS"),
    ("specialist_research", "POINT_IN_TIME_SPECIALIST_RESEARCH"),
    ("supplemental_evidence", "POINT_IN_TIME_SUPPLEMENTAL_PROVIDER_EVIDENCE"),
    ("valuation", "POINT_IN_TIME_ANALYST_AND_EARNINGS_ESTIMATES"),
)
EXPECTED_DERIVED = (
    "audit",
    "catalyst_bridge",
    "catalyst_probability",
    "catalyst_validation",
    "decision",
    "diagnostics",
    "master_decision",
    "outcome_learning",
    "sentiment",
    "synthesis",
    "thesis",
    "valuation_quality",
)


def admission(roles=None):
    """Build one admission record shape; no ledger, no dataset, no bytes."""

    resolved = sorted(REQUIRED_ROLES) if roles is None else list(roles)
    return {
        "schema_version": module.ADMISSION_SCHEMA_VERSION,
        "policy_version": module.ADMISSION_POLICY_VERSION,
        "record_type": module.ADMISSION_RECORD_TYPE,
        "status": module.ADMISSION_STATUS,
        **{name: f"VALUE-{name}" for name in ADMISSION_IDENTITY_FIELDS},
        **{name: True for name in ADMISSION_FIXED_TRUE},
        **{name: False for name in ADMISSION_FIXED_FALSE},
        "artifacts": [
            {"role": role, "content_evidence_id": f"SRC-{role}"} for role in resolved
        ],
    }


def test_contract_maps_exactly_the_twenty_two_sealed_engine_keys():
    verify_contract_integrity()

    assert set(REPLAY_ENGINE_INPUTS) == set(REQUIRED_ENGINE_KEYS)
    assert len(REPLAY_ENGINE_INPUTS) == 22
    kinds = engine_input_kinds()
    roles = engine_input_roles()
    assert set(kinds) == set(REQUIRED_ENGINE_KEYS)
    for key, engine_kinds in kinds.items():
        assert engine_kinds, key
        if DATASET_ARTIFACTS in engine_kinds:
            assert roles[key], key
        else:
            assert roles[key] == (), key


def test_hybrid_and_single_kind_dependencies_are_represented_exactly():
    coverage = verify_replay_input_coverage(admission())
    kinds = coverage.engine_input_kinds()

    assert kinds["outcome_learning"] == [DERIVED_ONLY, SEALED_LEARNING_STATE]
    assert kinds["valuation_quality"] == [DERIVED_ONLY, SEALED_SOURCE_EVIDENCE]
    assert kinds["authenticated_sources"] == [SEALED_SOURCE_EVIDENCE]
    assert kinds["decision"] == [DERIVED_ONLY]
    assert kinds["market_signals"] == [DATASET_ARTIFACTS]
    assert coverage.derived_only_engine_keys == EXPECTED_DERIVED
    assert coverage.sealed_learning_state_engine_keys == ("outcome_learning",)
    assert coverage.sealed_source_evidence_engine_keys == (
        "authenticated_sources",
        "valuation_quality",
    )
    assert coverage.dataset_artifact_engine_keys == EXPECTED_DATASET
    every = (
        set(coverage.dataset_artifact_engine_keys)
        | set(coverage.derived_only_engine_keys)
        | set(coverage.sealed_learning_state_engine_keys)
        | set(coverage.sealed_source_evidence_engine_keys)
    )
    assert every == set(REQUIRED_ENGINE_KEYS)
    assert set(coverage.covered_engine_keys) | set(coverage.uncovered_engine_keys) == set(
        EXPECTED_DATASET
    )


def test_current_admission_roles_leave_eight_engines_uncovered():
    coverage = verify_replay_input_coverage(admission())

    assert coverage.admitted_roles == tuple(sorted(REQUIRED_ROLES))
    assert coverage.covered_engine_keys == EXPECTED_COVERED
    assert coverage.uncovered_engine_role_pairs == EXPECTED_UNCOVERED
    assert len(coverage.uncovered_engine_keys) == 8
    assert coverage.is_complete is False


def test_uncovered_roles_are_reported_but_never_treated_as_admitted():
    coverage = verify_replay_input_coverage(admission())
    needed = {role for _, role in coverage.uncovered_engine_role_pairs}

    assert set(coverage.unadmittable_roles) <= needed
    assert needed - set(coverage.unadmittable_roles) == {"RAW_DAILY_SESSION_BARS"}
    assert needed.isdisjoint(set(coverage.admitted_roles))
    assert set(coverage.unadmittable_roles).isdisjoint(
        set(REQUIRED_ROLES) | set(OPTIONAL_ROLES)
    )
    assert unadmittable_roles() == frozenset(coverage.unadmittable_roles)
    assert declared_artifact_roles() >= needed


def test_optional_raw_session_bars_change_market_signal_coverage():
    baseline = verify_replay_input_coverage(admission())
    with_bars = verify_replay_input_coverage(
        admission(sorted(set(REQUIRED_ROLES) | {"RAW_DAILY_SESSION_BARS"}))
    )

    assert "market_signals" in baseline.uncovered_engine_keys
    assert baseline.covered_engine_keys == ("market_regime",)
    assert with_bars.covered_engine_keys == ("market_regime", "market_signals")
    assert "market_signals" not in with_bars.uncovered_engine_keys
    assert len(with_bars.uncovered_engine_role_pairs) == 7
    assert with_bars.is_complete is False


@pytest.mark.parametrize("dropped", sorted(REQUIRED_ROLES))
def test_missing_required_artifact_role_fails_closed(dropped):
    roles = [role for role in sorted(REQUIRED_ROLES) if role != dropped]

    with pytest.raises(ValueError, match="missing required artifact roles"):
        verify_replay_input_coverage(admission(roles))


@pytest.mark.parametrize(
    "record,fragment",
    [
        (None, "verified replay-dataset admission record"),
        ([], "verified replay-dataset admission record"),
        ({**admission(), "schema_version": "9.9"}, "schema version"),
        ({**admission(), "policy_version": "other-v1"}, "policy version"),
        ({**admission(), "record_type": "OTHER"}, "record type"),
        ({**admission(), "status": "REPLAY_EXECUTED"}, "status"),
        ({**admission(), "artifacts": []}, "list its authenticated artifacts"),
        ({**admission(), "artifacts": {}}, "list its authenticated artifacts"),
    ],
)
def test_malformed_admission_records_fail_closed(record, fragment):
    with pytest.raises(ValueError, match=fragment):
        verify_replay_input_coverage(record)


@pytest.mark.parametrize("name", ADMISSION_IDENTITY_FIELDS)
def test_missing_admission_identity_field_fails_closed(name):
    record = admission()
    record[name] = "   "

    with pytest.raises(ValueError, match=f"missing its {name} identity field"):
        verify_replay_input_coverage(record)
    del record[name]
    with pytest.raises(ValueError, match=f"missing its {name} identity field"):
        verify_replay_input_coverage(record)


@pytest.mark.parametrize("name", ADMISSION_FIXED_TRUE)
def test_unasserted_admission_boundary_field_fails_closed(name):
    record = admission()
    record[name] = False

    with pytest.raises(ValueError, match=f"does not assert {name}"):
        verify_replay_input_coverage(record)


@pytest.mark.parametrize("name", ADMISSION_FIXED_FALSE)
def test_admission_authority_flag_must_stay_false(name):
    record = admission()
    record[name] = True

    with pytest.raises(ValueError, match=f"forbidden authority: {name}"):
        verify_replay_input_coverage(record)


def test_unknown_duplicate_or_misshaped_artifacts_fail_closed():
    unknown = admission()
    unknown["artifacts"].append(
        {"role": "POINT_IN_TIME_NEWS", "content_evidence_id": "SRC-NEWS"}
    )
    with pytest.raises(ValueError, match="unknown artifact role"):
        verify_replay_input_coverage(unknown)

    duplicate = admission()
    duplicate["artifacts"].append(
        {"role": "TOTAL_RETURN_PRICES", "content_evidence_id": "SRC-OTHER"}
    )
    with pytest.raises(ValueError, match="repeats artifact role"):
        verify_replay_input_coverage(duplicate)

    extra_field = admission()
    extra_field["artifacts"][0] = {**extra_field["artifacts"][0], "trusted": True}
    with pytest.raises(ValueError, match="exactly role and content_evidence_id"):
        verify_replay_input_coverage(extra_field)

    blank = admission()
    blank["artifacts"][0] = {"role": "  ", "content_evidence_id": "SRC-1"}
    with pytest.raises(ValueError, match="must name a role and content id"):
        verify_replay_input_coverage(blank)


def test_coverage_cannot_be_fabricated_by_direct_construction():
    fields = {
        "admitted_roles": (),
        "covered_engine_keys": tuple(sorted(REQUIRED_ENGINE_KEYS)),
        "uncovered_engine_role_pairs": (),
        "unadmittable_roles": (),
        "dataset_artifact_engine_keys": (),
        "derived_only_engine_keys": (),
        "sealed_learning_state_engine_keys": (),
        "sealed_source_evidence_engine_keys": (),
        "contract_snapshot": (),
    }

    with pytest.raises(PermissionError, match="verify_replay_input_coverage"):
        ReplayInputCoverage(**fields)
    with pytest.raises(PermissionError, match="verify_replay_input_coverage"):
        ReplayInputCoverage(**fields, _authority=object())
    with pytest.raises(PermissionError, match="verify_replay_input_coverage"):
        ReplayInputCoverage(**fields, _authority=None)

    issued = verify_replay_input_coverage(admission())
    assert issued.is_complete is False
    with pytest.raises(FrozenInstanceError):
        issued.covered_engine_keys = tuple(sorted(REQUIRED_ENGINE_KEYS))
    with pytest.raises(FrozenInstanceError):
        issued.uncovered_engine_role_pairs = ()


def test_issued_coverage_cannot_be_forged_through_dataclasses_replace():
    issued = verify_replay_input_coverage(admission())
    assert issued.is_complete is False
    assert issued._authority is None

    for change in (
        {"uncovered_engine_role_pairs": ()},
        {"covered_engine_keys": tuple(sorted(REQUIRED_ENGINE_KEYS))},
        {"admitted_roles": tuple(sorted(declared_artifact_roles()))},
        {},
    ):
        with pytest.raises(PermissionError, match="verify_replay_input_coverage"):
            replace(issued, **change)


def test_replayed_authority_cannot_smuggle_inconsistent_result_fields():
    issued = verify_replay_input_coverage(admission())
    authority = module._COVERAGE_AUTHORITY

    with pytest.raises(ValueError, match="do not match the sealed contract snapshot"):
        replace(issued, uncovered_engine_role_pairs=(), _authority=authority)
    with pytest.raises(ValueError, match="do not match the sealed contract snapshot"):
        replace(
            issued,
            covered_engine_keys=tuple(sorted(REQUIRED_ENGINE_KEYS)),
            _authority=authority,
        )
    with pytest.raises(ValueError, match="do not match the sealed contract snapshot"):
        replace(issued, dataset_artifact_engine_keys=(), _authority=authority)
    with pytest.raises(ValueError, match="snapshot must cover exactly"):
        replace(issued, contract_snapshot=(), _authority=authority)

    faithful = replace(issued, _authority=authority)
    assert faithful == issued
    assert faithful.coverage_sha256() == issued.coverage_sha256()
    assert faithful.is_complete is False


def test_normal_issuance_remains_the_only_route_to_a_valid_answer():
    first = verify_replay_input_coverage(admission())
    second = verify_replay_input_coverage(admission())

    assert first == second
    assert first.covered_engine_keys == EXPECTED_COVERED
    assert first.uncovered_engine_role_pairs == EXPECTED_UNCOVERED
    assert first.coverage_sha256() == second.coverage_sha256()
    assert first._authority is None and second._authority is None


@pytest.mark.parametrize(
    "mutation,fragment",
    [
        ("drop_key", "missing="),
        ("extra_key", "unexpected="),
        ("bad_kind", "unsupported input kind"),
        ("empty_kind", "unsupported input kind"),
        ("artifact_without_role", "declares no role"),
        ("derived_with_role", "not artifact-backed"),
        ("malformed_role", "malformed artifact role"),
    ],
)
def test_contract_drift_or_tampering_fails_closed(monkeypatch, mutation, fragment):
    tampered = dict(REPLAY_ENGINE_INPUTS)
    if mutation == "drop_key":
        tampered.pop("news")
    elif mutation == "extra_key":
        tampered["speculative_engine"] = (frozenset({DERIVED_ONLY}), frozenset())
    elif mutation == "bad_kind":
        tampered["news"] = (frozenset({"AMBIENT_PROVIDER"}), frozenset())
    elif mutation == "empty_kind":
        tampered["news"] = (frozenset(), frozenset())
    elif mutation == "artifact_without_role":
        tampered["news"] = (frozenset({DATASET_ARTIFACTS}), frozenset())
    elif mutation == "derived_with_role":
        tampered["decision"] = (
            frozenset({DERIVED_ONLY}),
            frozenset({"TOTAL_RETURN_PRICES"}),
        )
    else:
        tampered["news"] = (
            frozenset({DATASET_ARTIFACTS}),
            frozenset({"point_in_time_news"}),
        )
    monkeypatch.setattr(module, "REPLAY_ENGINE_INPUTS", tampered)

    with pytest.raises(ValueError, match=fragment):
        verify_contract_integrity()
    with pytest.raises(ValueError, match=fragment):
        verify_replay_input_coverage(admission())


def test_issued_mapping_snapshot_survives_later_global_mutation(monkeypatch):
    coverage = verify_replay_input_coverage(admission())
    before_json = coverage.canonical_json()
    before_digest = coverage.coverage_sha256()

    monkeypatch.setattr(
        module,
        "REPLAY_ENGINE_INPUTS",
        {"news": (frozenset({DERIVED_ONLY}), frozenset())},
    )

    assert coverage.engine_input_kinds()["news"] == [DATASET_ARTIFACTS]
    assert coverage.engine_input_roles()["news"] == ["POINT_IN_TIME_NEWS"]
    assert len(coverage.contract_snapshot) == 22
    assert coverage.canonical_json() == before_json
    assert coverage.coverage_sha256() == before_digest


def test_coverage_payload_is_deterministic_and_order_independent():
    first = verify_replay_input_coverage(admission())
    reversed_record = admission()
    reversed_record["artifacts"] = list(reversed(reversed_record["artifacts"]))
    second = verify_replay_input_coverage(reversed_record)

    assert first == second
    assert first.canonical_json() == second.canonical_json()
    assert first.coverage_sha256() == second.coverage_sha256()
    assert len(first.coverage_sha256()) == 64


def test_payload_denies_dataset_access_execution_and_trading_authority():
    coverage = verify_replay_input_coverage(admission())
    payload = coverage.as_dict()

    assert payload["record_type"] == module.RECORD_TYPE
    assert payload["status"] == "REPLAY_INPUT_COVERAGE_INCOMPLETE_NOT_EXECUTED"
    assert payload["faithful_replay_input_coverage_complete"] is False
    assert payload["chain_verification_is_caller_prerequisite"] is True
    assert payload["contract_is_inert"] is True
    assert all(payload[name] is False for name in FIXED_FALSE_FIELDS)
    for name in (
        "evaluation_dataset_opened",
        "dataset_artifact_bytes_read",
        "replay_executed",
        "performance_claim_allowed",
        "broker_connection_allowed",
        "order_submitted",
        "live_trading_enabled",
    ):
        assert name in FIXED_FALSE_FIELDS
    assert not hasattr(coverage, "run")
    assert not hasattr(coverage, "save")
    assert not hasattr(coverage, "admit")
    assert payload["uncovered_engine_role_pairs"] == [
        list(pair) for pair in EXPECTED_UNCOVERED
    ]
