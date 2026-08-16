from __future__ import annotations

import ast
from datetime import date
import hashlib
import json
from pathlib import Path

import pytest

import core.research.campaign_v2_revision_capture_activation_proposal as module
from core.research.campaign_v2_revision_capture_activation_proposal import (
    APPROVAL_STATUS,
    AUTHORIZED_SPLIT_ROLES,
    PARENT_PREREGISTRATION_ID,
    PARENT_PREREGISTRATION_RECORD_SHA256,
    activation_proposal,
    planned_request_slices,
    required_approval_text,
)


def test_activation_proposal_is_stable_pending_and_inert():
    first = activation_proposal(current_real_world_date=date(2026, 8, 16))
    second = activation_proposal(current_real_world_date=date(2026, 8, 16))
    assert first == second
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
    assert first["proposal_sha256"] == (
        "ac80cd34ccfd9a620daa7d81812261f97a144402e56e7b3eb20825565266d02a"
    )
    assert first["approval_status"] == APPROVAL_STATUS
    assert first["capture_activation_issued"] is False
    assert first["entitlement_revalidated"] is False
    assert first["data_calls_allowed"] is False
    assert first["provider_bytes_accessed"] is False
    assert first["evaluation_allowed"] is False
    assert first["broker_connection_allowed"] is False
    assert first["orders_submitted"] is False
    assert first["live_trading_enabled"] is False


def test_activation_is_bound_to_exact_approved_parent():
    proposal = activation_proposal(current_real_world_date=date(2026, 8, 16))
    assert proposal["parent_preregistration_id"] == PARENT_PREREGISTRATION_ID
    assert (
        proposal["parent_preregistration_record_sha256"]
        == PARENT_PREREGISTRATION_RECORD_SHA256
    )
    assert proposal["parent_campaign_proposal_sha256"] == (
        "7c43094e64f324d6987b67a25d03626eb4defe4096ae1b135e1c0319b60fc0d5"
    )


def test_only_train_validation_slices_are_authorized_and_test_stays_sealed():
    proposal = activation_proposal(current_real_world_date=date(2026, 8, 16))
    assert [item["role"] for item in proposal["authorized_splits"]] == list(
        AUTHORIZED_SPLIT_ROLES
    )
    assert proposal["sealed_split"]["role"] == "UNTOUCHED_TEST"
    assert proposal["sealed_split_semantic_role"] == "SEALED_RETROSPECTIVE_TEST"
    assert proposal["request_policy"]["untouched_test_request_allowed"] is False
    slices = proposal["authorized_request_slices"]
    assert len(slices) == proposal["authorized_request_count"] == 27
    assert {item[1] for item in slices} == {"TRAIN", "VALIDATION"}
    assert all(item[1] != "UNTOUCHED_TEST" for item in slices)
    assert sum(1 for item in slices if item[1] == "TRAIN") == 21
    assert sum(1 for item in slices if item[1] == "VALIDATION") == 6
    assert tuple(tuple(item) for item in slices) == planned_request_slices()


def test_slice_helper_rejects_alternate_dates_roles_and_baskets():
    with pytest.raises(ValueError, match="target basket changed"):
        planned_request_slices(target_basket=["AAPL"])
    with pytest.raises(ValueError, match="exact campaign splits"):
        planned_request_slices(
            splits=[
                {"role": "TRAIN", "start": "2024-08-02", "end": "2025-02-28"},
                {
                    "role": "VALIDATION",
                    "start": "2025-03-01",
                    "end": "2025-04-30",
                },
            ]
        )
    with pytest.raises(ValueError, match="exact campaign splits"):
        planned_request_slices(
            splits=[
                {"role": "TRAIN", "start": "2024-08-01", "end": "2025-02-28"},
                {
                    "role": "UNTOUCHED_TEST",
                    "start": "2025-05-01",
                    "end": "2025-07-31",
                },
            ]
        )


def test_every_campaign_partition_must_end_on_or_before_real_world_date():
    proposal = activation_proposal(current_real_world_date=date(2025, 7, 31))
    ends = [
        *(item["end"] for item in proposal["authorized_splits"]),
        proposal["sealed_split"]["end"],
    ]
    assert all(date.fromisoformat(value) <= date(2025, 7, 31) for value in ends)
    with pytest.raises(ValueError, match="UNTOUCHED_TEST ends after"):
        activation_proposal(current_real_world_date=date(2025, 7, 30))
    with pytest.raises(ValueError, match="VALIDATION ends after"):
        activation_proposal(current_real_world_date=date(2025, 4, 29))
    with pytest.raises(ValueError, match="TRAIN ends after"):
        activation_proposal(current_real_world_date=date(2025, 2, 27))


def test_entitlement_requirements_do_not_invent_current_account_evidence():
    requirements = activation_proposal(
        current_real_world_date=date(2026, 8, 16)
    )["entitlement_evidence_required_before_activation"]
    assert requirements == {
        "authenticated_account_session": True,
        "account_identity_sha256": True,
        "account_entitlement_authenticated": True,
        "completed_historical_daily_aggregate_access_confirmed": True,
        "historical_lookback_covers_acquisition_start": "2024-08-01",
        "plan_name": True,
        "asserted_request_limit_per_minute": 5,
        "asserted_incremental_cost_usd": "0.00",
        "terms_uri": "https://massive.com/stocks",
        "terms_retrieved_at": True,
        "terms_payload_sha256": True,
        "credential_material_recorded": False,
        "redistribution_permission_inferred": False,
        "provider_payload_semantics_qualified": False,
    }


def test_approval_text_is_hash_specific_but_does_not_activate_anything():
    proposal = activation_proposal(current_real_world_date=date.today())
    assert required_approval_text().endswith(proposal["proposal_sha256"])
    assert required_approval_text().startswith(
        "I authorize Campaign v2 revision-1 TRAIN and VALIDATION quarantined capture"
    )
    assert proposal["data_calls_allowed"] is False


def test_activation_proposal_hash_is_anchored_in_authoritative_documents():
    root = Path(__file__).resolve().parents[1]
    digest = activation_proposal(
        current_real_world_date=date(2026, 8, 16)
    )["proposal_sha256"]
    for relative in (
        "docs/PROJECT_STATUS.md",
        "docs/MASTER_ROADMAP_COMPLETION_AUDIT.md",
    ):
        assert digest in (root / relative).read_text()


def test_proposal_module_has_no_credential_provider_network_replay_or_broker_capability():
    tree = ast.parse(Path(module.__file__).read_text())
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
    forbidden = {
        "requests",
        "httpx",
        "urllib.request",
        "core.orchestration.massive_historical_quarantine",
        "core.orchestration.massive_historical_adapter",
        "core.research.vectorbt_pilot",
        "core.guardrailed_backtest",
    }
    assert imports.isdisjoint(forbidden)
    assert not any("broker" in value or "alpaca" in value for value in imports)
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls & {"open", "eval", "exec", "__import__"}
