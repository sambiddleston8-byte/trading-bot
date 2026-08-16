from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

import core.orchestration.massive_entitlement_evidence as module
from core.orchestration.massive_entitlement_evidence import (
    CAPTURE_APPROVAL_RECORD_ID,
    PUBLIC_DOCUMENT_CONTRACTS,
    assess_capture_activation_evidence,
    build_authenticated_account_evidence,
    build_public_documentation_evidence,
)


NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def _documents() -> list[dict[str, str]]:
    return [
        {
            "document_id": document_id,
            "uri": contract["uri"],
            "retrieved_at": "2026-08-16T11:00:00+00:00",
            "payload_sha256": hashlib.sha256(document_id.encode()).hexdigest(),
        }
        for document_id, contract in PUBLIC_DOCUMENT_CONTRACTS.items()
    ]


def _public() -> dict:
    return build_public_documentation_evidence(_documents(), assessed_at=NOW)


def _account(**changes) -> dict:
    evidence = {
        "captured_at": "2026-08-16T11:30:00+00:00",
        "account_binding_evidence_kind": "AUTHENTICATED_DASHBOARD_EXPORT",
        "account_binding_payload_sha256": "a" * 64,
        "account_identity_sha256": "b" * 64,
        "plan_name": "Stocks Basic Free",
        "plan_evidence_payload_sha256": "c" * 64,
        "custom_bars_access_confirmed": True,
        "historical_lookback_start": "2024-08-01",
        "request_limit_per_minute": 5,
        "incremental_cost_usd": "0.00",
        "terms_uri": "https://massive.com/stocks",
        "terms_retrieved_at": "2026-08-16T11:15:00+00:00",
        "terms_payload_sha256": "d" * 64,
        "authenticated_account_session": True,
        "credential_material_recorded": False,
        "market_data_response_used_for_account_binding": False,
        "request_id_used_for_account_binding": False,
    }
    evidence.update(changes)
    return build_authenticated_account_evidence(evidence, assessed_at=NOW)


def _rehash(value: dict, field: str) -> None:
    material = {key: item for key, item in value.items() if key != field}
    value[field] = hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def test_public_docs_preserve_product_facts_but_never_prove_account_binding():
    evidence = _public()

    assert evidence["custom_bars_published_free_plan_access"] is True
    assert evidence["flat_files_published_free_plan_access"] is False
    assert evidence["public_documentation_proves_account_identity"] is False
    assert evidence["public_documentation_proves_account_plan"] is False
    assert evidence["public_documentation_proves_current_account_entitlement"] is False
    assert all(
        document["authenticated_account_evidence"] is False
        for document in evidence["documents"]
    )


def test_public_docs_must_bind_all_exact_official_bytes_before_assessment():
    with pytest.raises(ValueError, match="all exact"):
        build_public_documentation_evidence(_documents()[:-1], assessed_at=NOW)

    wrong = _documents()
    wrong[0]["uri"] = "https://example.invalid/docs"
    with pytest.raises(ValueError, match="official Massive|differs"):
        build_public_documentation_evidence(wrong, assessed_at=NOW)

    future = _documents()
    future[0]["retrieved_at"] = "2026-08-16T12:00:01+00:00"
    with pytest.raises(ValueError, match="after assessment"):
        build_public_documentation_evidence(future, assessed_at=NOW)


@pytest.mark.parametrize(
    "kind",
    [
        "API_REQUEST_ID",
        "MARKET_DATA_RESPONSE",
        "PUBLIC_DOCUMENTATION",
        "USER_ASSERTION",
    ],
)
def test_market_data_and_public_material_cannot_be_account_binding(kind):
    with pytest.raises(ValueError, match="cannot prove"):
        _account(account_binding_evidence_kind=kind)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"authenticated_account_session": False}, "authenticated account"),
        ({"credential_material_recorded": True}, "credential material"),
        (
            {"market_data_response_used_for_account_binding": True},
            "market-data response",
        ),
        ({"request_id_used_for_account_binding": True}, "request_id"),
        ({"historical_lookback_start": "2024-08-02"}, "lookback"),
        ({"request_limit_per_minute": 6}, "approved value 5"),
        ({"incremental_cost_usd": "0.01"}, "exactly 0.00"),
        ({"terms_uri": "https://massive.com/other"}, "approved proposal"),
        ({"custom_bars_access_confirmed": False}, "custom-bars access"),
    ],
)
def test_authenticated_evidence_rejects_missing_or_substituted_proof(changes, message):
    with pytest.raises(ValueError, match=message):
        _account(**changes)


def test_public_docs_alone_leave_activation_blocked_and_every_authority_false():
    assessment = assess_capture_activation_evidence(
        public_documentation=_public(), authenticated_account=None
    )

    assert assessment["capture_approval_record_id"] == CAPTURE_APPROVAL_RECORD_ID
    assert assessment["evidence_complete_for_separate_activation_record"] is False
    assert assessment["missing_evidence"] == [
        "AUTHENTICATED_ACCOUNT_BOUND_ENTITLEMENT_EVIDENCE"
    ]
    assert assessment["public_documentation_is_not_account_binding"] is True
    assert assessment["free_plan_flat_file_access_inferred"] is False
    for field in (
        "capture_activation_issued",
        "data_calls_allowed",
        "provider_bytes_accessed",
        "untouched_test_opened",
        "dataset_admitted",
        "evaluation_allowed",
        "broker_connection_allowed",
        "orders_submitted",
        "live_trading_enabled",
    ):
        assert assessment[field] is False


def test_complete_evidence_only_qualifies_a_later_activation_record():
    assessment = assess_capture_activation_evidence(
        public_documentation=_public(), authenticated_account=_account()
    )

    assert assessment["evidence_complete_for_separate_activation_record"] is True
    assert assessment["missing_evidence"] == []
    assert assessment["capture_activation_issued"] is False
    assert assessment["data_calls_allowed"] is False
    assert assessment["provider_bytes_accessed"] is False


def test_rehashed_semantic_tampering_still_fails_closed():
    public = deepcopy(_public())
    public["public_documentation_proves_account_identity"] = True
    _rehash(public, "evidence_bundle_sha256")
    with pytest.raises(ValueError, match="invalid or tampered"):
        assess_capture_activation_evidence(
            public_documentation=public, authenticated_account=None
        )

    account = deepcopy(_account())
    account["request_id_used_for_account_binding"] = True
    _rehash(account, "evidence_bundle_sha256")
    with pytest.raises(ValueError, match="invalid or tampered"):
        assess_capture_activation_evidence(
            public_documentation=_public(), authenticated_account=account
        )

    malformed_date = deepcopy(_account())
    malformed_date["historical_lookback_start"] = "not-a-date"
    _rehash(malformed_date, "evidence_bundle_sha256")
    with pytest.raises(ValueError, match="invalid or tampered"):
        assess_capture_activation_evidence(
            public_documentation=_public(), authenticated_account=malformed_date
        )


def test_schema_has_no_network_credential_file_replay_or_broker_capability():
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
        "core.orchestration.massive_historical_adapter",
        "core.orchestration.massive_historical_quarantine",
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
