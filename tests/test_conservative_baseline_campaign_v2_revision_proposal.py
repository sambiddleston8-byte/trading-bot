from __future__ import annotations

import ast
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

import core.research.conservative_baseline_campaign_v2_revision_proposal as proposal_module
from core.orchestration.historical_quarantine_preregistration import (
    MAX_ACQUISITION_DAYS,
)
from core.research.conservative_baseline_campaign import (
    ACQUISITION_START as CAMPAIGN_V1_ACQUISITION_START,
    approved_execution_policy,
)
from core.research.conservative_baseline_campaign_v2_revision_proposal import (
    ACQUISITION_END,
    ACQUISITION_START,
    APPROVAL_STATUS,
    COMPLETED_HISTORY_CUTOFF,
    EXPECTED_CAPTURE_COUNTS,
    SPLITS,
    SUPERSEDED_PROPOSAL_SHA256,
    proposal_package,
    proposed_evaluation_protocol,
    proposed_execution_policy,
    proposed_quarantine_definition,
)
from core.orchestration.campaign_v2_preregistration import PROPOSAL_SHA256


SOURCE = Path(proposal_module.__file__)


def test_revision_window_is_completed_contiguous_bounded_and_disjoint_from_v1():
    start = date.fromisoformat(ACQUISITION_START)
    end = date.fromisoformat(ACQUISITION_END)
    assert (end - start).days + 1 <= MAX_ACQUISITION_DAYS
    expected = start
    for split in SPLITS:
        assert date.fromisoformat(split["start"]) == expected
        expected = date.fromisoformat(split["end"]) + timedelta(days=1)
    assert expected == end + timedelta(days=1)
    assert end <= date.fromisoformat(COMPLETED_HISTORY_CUTOFF)
    assert end < date.fromisoformat(CAMPAIGN_V1_ACQUISITION_START)
    assert SPLITS[-1] == {
        "role": "UNTOUCHED_TEST",
        "start": "2025-05-01",
        "end": "2025-07-31",
    }
    assert EXPECTED_CAPTURE_COUNTS == {
        "TRAIN": 21,
        "VALIDATION": 6,
        "UNTOUCHED_TEST": 9,
        "TOTAL": 36,
    }


def test_revision_preserves_execution_economics_and_protocol_controls():
    v1 = approved_execution_policy()
    revision = proposed_execution_policy()
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
        assert revision[field] == v1[field]
    assert revision["campaign_policy_version"].endswith("v2-revision-1")
    assert Decimal(revision["simulation_controls"]["maximum_position_fraction"]) == Decimal("0.25")
    protocol = proposed_evaluation_protocol()
    assert protocol["purge_observations"] == 1
    assert protocol["embargo_observations"] == 1
    assert protocol["maximum_untouched_test_evaluations"] == 1
    assert protocol["success_thresholds"]["performance_claim_allowed"] is False


def test_revision_hash_is_stable_pending_and_retrospective_limits_are_explicit():
    first = proposal_package()
    assert first == proposal_package()
    assert APPROVAL_STATUS == "PENDING_EXPLICIT_HUMAN_APPROVAL"
    assert first["proposal_sha256"] == (
        "7c43094e64f324d6987b67a25d03626eb4defe4096ae1b135e1c0319b60fc0d5"
    )
    assert json.loads(json.dumps(first)) == first
    material = {key: value for key, value in first.items() if key != "proposal_sha256"}
    assert first["proposal_sha256"] == hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    assert first["approval_status"] == APPROVAL_STATUS
    assert first["supersedes_proposal_sha256"] == SUPERSEDED_PROPOSAL_SHA256
    assert SUPERSEDED_PROPOSAL_SHA256 == PROPOSAL_SHA256
    assert first["opened_campaign_v1_overlap"] is False
    assert first["test_evidence_classification"] == {
        "schema_role": "UNTOUCHED_TEST",
        "semantic_role": "SEALED_RETROSPECTIVE_TEST",
        "genuinely_future_at_preregistration": False,
        "single_open_only": True,
        "promotion_or_track_record_authority": False,
        "claim_outcomes_were_unknown_to_researchers": False,
    }
    assert first["data_calls_allowed"] is False
    assert first["evaluation_allowed"] is False
    assert first["preregistration_append_allowed"] is False
    assert first["performance_claim_allowed"] is False
    limitations = first["research_exemption_extension"]["limitations"]
    assert "RETROSPECTIVE_AT_PREREGISTRATION_NOT_GENUINELY_FUTURE_UNTOUCHED" in limitations
    assert "NO_PROMOTION_OR_TRACK_RECORD_AUTHORITY" in limitations


def test_revision_digest_is_anchored_in_authoritative_documents():
    root = Path(__file__).resolve().parents[1]
    digest = proposal_package()["proposal_sha256"]
    for relative in (
        "docs/PROJECT_STATUS.md",
        "docs/MASTER_ROADMAP_COMPLETION_AUDIT.md",
    ):
        assert digest in (root / relative).read_text()


def test_revision_definition_is_inert_and_rejects_superseded_registration():
    entitlement = {"plan_name": "PENDING_REVALIDATION"}
    definition = proposed_quarantine_definition(
        registered_by="SAM_AND_PAT_LOCAL_RESEARCH",
        entitlement_metadata=entitlement,
    )
    assert definition["proposal_control"] == {
        "approval_status": APPROVAL_STATUS,
        "explicit_approval_record_required": True,
        "superseded_v2_preregistration_ledger_accepted": False,
        "retrospective_test_classification_required": True,
    }
    assert definition["parameter_space"]["parameter_search_allowed"] is False
    assert definition["entitlement_metadata"] == entitlement


def test_revision_guards_reject_overlap_with_opened_v1(monkeypatch):
    monkeypatch.setattr(proposal_module, "ACQUISITION_END", CAMPAIGN_V1_ACQUISITION_START)
    with pytest.raises(ValueError, match="completed disjoint history"):
        proposal_module.proposal_package()


def test_revision_proposal_has_no_data_network_engine_or_broker_capability():
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
        "core.research.conservative_baseline_campaign_v2_proposal",
        "core.research.conservative_baseline_strategy",
    }
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls & {"__import__", "eval", "exec", "open"}
