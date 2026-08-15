from __future__ import annotations

import ast
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import inspect
import json
from pathlib import Path

import pytest

import core.research.conservative_baseline_campaign_v2_proposal as proposal_module
from core.decision_ledger import LedgerIntegrityError
from core.orchestration.historical_quarantine_preregistration import (
    MAX_ACQUISITION_DAYS,
    HistoricalQuarantinePreregistrationLedger,
)
from core.research.conservative_baseline_campaign import (
    ACQUISITION_END as CAMPAIGN_V1_ACQUISITION_END,
    approved_execution_policy,
)
from core.research.conservative_baseline_campaign_v2_proposal import (
    ACQUISITION_END,
    ACQUISITION_START,
    APPROVAL_STATUS,
    EXPECTED_CAPTURE_COUNTS,
    PARENT_ASSUMPTION_SHA256,
    SPLITS,
    proposal_package,
    proposed_evaluation_protocol,
    proposed_execution_policy,
    proposed_quarantine_definition,
    proposed_research_exemption_extension,
)
from core.research.massive_research_exempt_replay import (
    AVAILABILITY_ASSUMPTION,
    INDEX_ASSUMPTION,
    SESSION_ASSUMPTION,
    ResearchExemptionLedger,
    admit_train_validation,
    close_campaign_v1,
    vectorbt_fixed_parameter_screen,
)


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "core/research/conservative_baseline_campaign_v2_proposal.py"
)


def test_v2_window_is_contiguous_bounded_and_has_fresh_future_untouched_dates():
    start = date.fromisoformat(ACQUISITION_START)
    end = date.fromisoformat(ACQUISITION_END)
    assert (end - start).days + 1 <= MAX_ACQUISITION_DAYS
    expected = start
    for split in SPLITS:
        assert date.fromisoformat(split["start"]) == expected
        expected = date.fromisoformat(split["end"]) + timedelta(days=1)
    assert expected == end + timedelta(days=1)
    assert SPLITS[-1] == {
        "role": "UNTOUCHED_TEST",
        "start": "2026-09-01",
        "end": "2027-02-28",
    }
    assert date.fromisoformat(SPLITS[-1]["start"]) > date.fromisoformat(
        CAMPAIGN_V1_ACQUISITION_END
    )
    assert EXPECTED_CAPTURE_COUNTS == {
        "TRAIN": 12,
        "VALIDATION": 6,
        "UNTOUCHED_TEST": 18,
        "TOTAL": 36,
    }


def test_v2_preserves_v1_execution_economics_but_has_new_pending_identity():
    v1 = approved_execution_policy()
    v2 = proposed_execution_policy()
    for field in (
        "cost_policy_version",
        "cost_policy_id",
        "scenarios",
        "simulation_controls",
        "aapl_msft_separate_candidate_runs",
        "spy_benchmark_only",
        "parameter_search_allowed",
        "same_bar_execution_allowed",
        "next_open_execution_required",
    ):
        assert v2[field] == v1[field]
    assert v2["campaign_policy_version"].endswith("campaign-v2")
    assert Decimal(v2["simulation_controls"]["maximum_position_fraction"]) == Decimal("0.25")


def test_v2_protocol_derives_mandatory_purge_embargo_and_one_test():
    protocol = proposed_evaluation_protocol()
    assert protocol["warmup_observations"] == 50
    assert protocol["purge_observations"] == 1
    assert protocol["embargo_observations"] == 1
    assert protocol["maximum_untouched_test_evaluations"] == 1
    thresholds = protocol["success_thresholds"]
    assert thresholds["all_candidate_symbols_must_pass"] == ["AAPL", "MSFT"]
    assert thresholds["minimum_completed_trades_each_candidate"] == 2
    assert thresholds["insufficient_completed_trades_outcome"] == (
        "INCONCLUSIVE_NOT_PASS_NOT_FAIL_NO_RERUN"
    )
    assert thresholds["maximum_drawdown_each_candidate"] == "0.20"
    assert thresholds["pessimistic_scenario_must_pass"] is True
    assert thresholds["protocol_conformance_required"] is True
    assert thresholds["performance_claim_allowed"] is False


def test_v2_package_is_hash_stable_inert_and_requires_future_window_schema():
    first = proposal_package()
    second = proposal_package()
    assert first == second
    material = {key: value for key, value in first.items() if key != "proposal_sha256"}
    expected = hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    assert first["proposal_sha256"] == expected
    assert first["approval_status"] == APPROVAL_STATUS
    assert first["current_v1_preregistration_schema_compatible"] is False
    assert first["data_calls_allowed"] is False
    assert first["evaluation_allowed"] is False
    assert first["preregistration_append_allowed"] is False
    assert first["future_window_controls"] == {
        "campaign_v1_closure_must_precede_v2_preregistration": True,
        "distinct_v2_preregistration_ledger_required": True,
        "distinct_v2_quarantine_root_required": True,
        "preregistration_must_precede_any_v2_data_call": True,
        "request_slice_must_be_complete_and_historical_at_fetch_time": True,
        "future_dated_provider_request_allowed": False,
        "untouched_payload_open_before_single_test_allowed": False,
        "train_validation_evaluation_before_validation_end_allowed": False,
        "untouched_evaluation_before_untouched_end_allowed": False,
    }
    disclosure = first["campaign_v1_opened_data_reuse_disclosure"]
    assert disclosure["v1_acquisition_start"] == "2025-08-01"
    assert disclosure["v1_acquisition_end"] == CAMPAIGN_V1_ACQUISITION_END
    assert disclosure["overlaps_v2_train_or_validation"] is True
    assert disclosure["eligible_as_v2_untouched"] is False
    assert disclosure["v1_outcome_was_known_when_v2_splits_were_proposed"] is True
    assert disclosure["strategy_or_parameter_change_based_on_v1_allowed"] is False
    assert first["post_v1_protocol_change_disclosure"] == {
        "primary_metric_changed": True,
        "rationale": (
            "UNADJUSTED_BARS_WITHOUT_COMPLETE_CORPORATE_ACTIONS_CANNOT_"
            "SUPPORT_COMPLETE_TOTAL_OR_BENCHMARK_RELATIVE_RETURN"
        ),
        "replacement_is_diagnostic_only": True,
        "change_selected_to_improve_observed_v1_result": False,
        "promotion_authority_created": False,
    }


def test_v2_definition_is_only_an_inert_schema_shaped_proposal():
    entitlement = {
        "plan_name": "PENDING_REVALIDATION",
        "terms_uri": "https://massive.com/stocks",
        "terms_retrieved_at": "2026-08-15T00:00:00+00:00",
        "terms_payload_sha256": "a" * 64,
        "asserted_request_limit_per_minute": 5,
        "asserted_incremental_cost_usd": "0.00",
    }
    definition = proposed_quarantine_definition(
        registered_by="SAM_AND_PAT_LOCAL_RESEARCH",
        entitlement_metadata=entitlement,
    )
    assert definition["acquisition_end"] == "2027-02-28"
    assert definition["proposal_control"] == {
        "approval_status": APPROVAL_STATUS,
        "explicit_approval_record_required": True,
        "current_v1_preregistration_ledger_accepted": False,
    }
    assert definition["parameter_space"]["parameter_search_allowed"] is False
    assert definition["entitlement_metadata"] == entitlement
    extension = proposed_research_exemption_extension()
    assert extension["status"] == APPROVAL_STATUS
    assert extension["extension_registered"] is False
    assert extension["provider_evidence"] is False
    assert extension["performance_claim_allowed"] is False
    assumptions = {
        "HISTORICAL_AVAILABILITY": AVAILABILITY_ASSUMPTION,
        "INDEX_MEMBERSHIP": INDEX_ASSUMPTION,
        "SESSION_TIMES": SESSION_ASSUMPTION,
    }
    assert PARENT_ASSUMPTION_SHA256 == {
        name: hashlib.sha256(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()
        for name, value in assumptions.items()
    }
    assert extension["assumption_application_policy"].startswith(
        "TO_BE_ASSERTED_AT_CAPTURE_TIME"
    )
    assert "NO_CORPORATE_ACTION_OR_TOTAL_RETURN_EVIDENCE" in extension["limitations"]


def test_v1_preregistration_api_structurally_rejects_v2_proposal_even_after_window():
    definition = proposed_quarantine_definition(
        registered_by="SAM_AND_PAT_LOCAL_RESEARCH",
        entitlement_metadata={
            "plan_name": "PENDING_REVALIDATION",
            "terms_uri": "https://massive.com/stocks",
            "terms_retrieved_at": "2026-08-15T00:00:00+00:00",
            "terms_payload_sha256": "a" * 64,
            "asserted_request_limit_per_minute": 5,
            "asserted_incremental_cost_usd": "0.00",
        },
    )
    assert "proposal_control" not in inspect.signature(
        HistoricalQuarantinePreregistrationLedger.preregister
    ).parameters
    ledger = HistoricalQuarantinePreregistrationLedger("unused.jsonl")
    with pytest.raises(TypeError, match="proposal_control"):
        ledger.preregister(**definition)


def test_v2_fresh_untouched_guard_rejects_overlap_with_opened_v1(monkeypatch):
    changed = tuple(dict(split) for split in SPLITS)
    changed[-1]["start"] = CAMPAIGN_V1_ACQUISITION_END
    monkeypatch.setattr(proposal_module, "SPLITS", changed)
    with pytest.raises(ValueError, match="fresh bounded-test rule"):
        proposal_module.proposal_package()


def test_v2_proposal_module_has_no_data_network_engine_or_broker_capability():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imports.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert imports <= {
        "__future__",
        "datetime",
        "hashlib",
        "json",
        "typing",
        "core.research.conservative_baseline_campaign",
        "core.research.conservative_baseline_strategy",
    }
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls & {"__import__", "eval", "exec", "open"}


def completed_v1_ledger(tmp_path, **result_overrides):
    ledger = ResearchExemptionLedger(tmp_path / "audit.jsonl")
    exemption_id = "RIXA-E657C58EC99C067EAB6879FB035C0A0D"
    exemption = ledger.append(
        "RESEARCH_EXEMPTION_REGISTERED", {"exemption_id": exemption_id}
    )
    admission = ledger.append("TRAIN_VALIDATION_RESEARCH_ADMITTED", {})
    consumption = ledger.append("UNTOUCHED_TEST_CONSUMPTION_STARTED", {})
    result_payload = {
        "result_sha256": "05adfe5236ea7bd50c57b2e04326ec421253365c4b51e9cb83ceb86b86fe4ac1",
        "untouched_test_reusable": False,
        "exemption_id": exemption_id,
        **result_overrides,
    }
    result = ledger.append("UNTOUCHED_TEST_RESULT_RECORDED", result_payload)
    return ledger, exemption, admission, consumption, result


def test_campaign_v1_closure_is_unique_and_keeps_exemption_active(tmp_path):
    ledger, exemption, admission, consumption, result = completed_v1_ledger(
        tmp_path
    )
    closed = close_campaign_v1(ledger=ledger, authorized_by="SAM_AND_PAT_USER")
    assert closed["payload"]["campaign_status"] == "CLOSED_PROTOCOL_NONCONFORMANT"
    assert closed["payload"]["research_exemption_active_for_original_scope"] is True
    assert closed["payload"]["research_exemption_scope_extension_granted"] is False
    assert closed["payload"]["campaign_v1_untouched_reusable"] is False
    assert closed["payload"]["closure_reason_codes"] == [
        "PREREGISTERED_PURGE_AND_EMBARGO_OMITTED",
        "SPY_DIAGNOSTIC_TIMING_MISALIGNED_AND_NOT_REGISTERED_COMPLETE_RETURN",
    ]
    assert closed["payload"]["exemption_record_hash"] == exemption["record_hash"]
    assert closed["payload"]["train_validation_admission_record_hash"] == admission["record_hash"]
    assert closed["payload"]["untouched_consumption_record_hash"] == consumption["record_hash"]
    assert closed["payload"]["result_record_hash"] == result["record_hash"]
    assert close_campaign_v1(
        ledger=ledger, authorized_by="SAM_AND_PAT_USER"
    ) == closed
    try:
        close_campaign_v1(ledger=ledger, authorized_by="SUBSTITUTED_USER")
    except LedgerIntegrityError:
        pass
    else:
        raise AssertionError("substituted Campaign v1 closure authority was accepted")
    with pytest.raises(ValueError, match="Campaign v1 is closed"):
        admit_train_validation(
            ledger=ledger,
            store=object(),
            admitted_root=tmp_path / "admitted",
        )
    with pytest.raises(ValueError, match="Campaign v1 is closed"):
        vectorbt_fixed_parameter_screen((), campaign_state_ledger=ledger)


@pytest.mark.parametrize(
    "overrides",
    [
        {"result_sha256": "f" * 64},
        {"untouched_test_reusable": True},
        {"exemption_id": "RIXA-SUBSTITUTED"},
    ],
)
def test_campaign_v1_closure_rejects_wrong_result_identity(tmp_path, overrides):
    ledger, *_ = completed_v1_ledger(tmp_path, **overrides)
    with pytest.raises(LedgerIntegrityError, match="result identity"):
        close_campaign_v1(ledger=ledger, authorized_by="SAM_AND_PAT_USER")


def test_campaign_v1_closure_rejects_incomplete_reordered_extra_and_direct_chains(tmp_path):
    incomplete = ResearchExemptionLedger(tmp_path / "incomplete.jsonl")
    incomplete.append("RESEARCH_EXEMPTION_REGISTERED", {"exemption_id": "RIXA-X"})
    with pytest.raises(LedgerIntegrityError, match="incomplete or reordered"):
        close_campaign_v1(ledger=incomplete, authorized_by="SAM_AND_PAT_USER")

    reordered = ResearchExemptionLedger(tmp_path / "reordered.jsonl")
    reordered.append("TRAIN_VALIDATION_RESEARCH_ADMITTED", {})
    reordered.append("RESEARCH_EXEMPTION_REGISTERED", {"exemption_id": "RIXA-X"})
    reordered.append("UNTOUCHED_TEST_CONSUMPTION_STARTED", {})
    reordered.append("UNTOUCHED_TEST_RESULT_RECORDED", {})
    with pytest.raises(LedgerIntegrityError, match="incomplete or reordered"):
        close_campaign_v1(ledger=reordered, authorized_by="SAM_AND_PAT_USER")

    extra, *_ = completed_v1_ledger(tmp_path / "extra")
    extra.append("RESEARCH_EXEMPTION_REGISTERED", {"exemption_id": "RIXA-EXTRA"})
    with pytest.raises(LedgerIntegrityError, match="extra events"):
        close_campaign_v1(ledger=extra, authorized_by="SAM_AND_PAT_USER")

    direct = ResearchExemptionLedger(tmp_path / "direct.jsonl")
    direct.append(
        "CAMPAIGN_V1_CLOSED_PROTOCOL_NONCONFORMANT",
        {"authorized_by": "SAM_AND_PAT_USER"},
    )
    with pytest.raises(LedgerIntegrityError, match="incomplete or reordered"):
        close_campaign_v1(ledger=direct, authorized_by="SAM_AND_PAT_USER")


def test_campaign_v1_closure_requires_nonblank_authority(tmp_path):
    ledger, *_ = completed_v1_ledger(tmp_path)
    with pytest.raises(ValueError, match="authorized_by is required"):
        close_campaign_v1(ledger=ledger, authorized_by="   ")


def test_reviewed_result_hash_matches_authoritative_status_document():
    status = (Path(__file__).resolve().parents[1] / "docs/PROJECT_STATUS.md").read_text()
    assert "05adfe5236ea7bd50c57b2e04326ec421253365c4b51e9cb83ceb86b86fe4ac1" in status
