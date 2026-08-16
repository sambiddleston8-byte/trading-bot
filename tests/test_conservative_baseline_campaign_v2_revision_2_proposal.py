from __future__ import annotations

import ast
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import pytest

import core.research.conservative_baseline_campaign_v2_revision_2_proposal as module
from core.research.conservative_baseline_campaign_v2_revision_2_proposal import (
    ACQUISITION_END,
    ACQUISITION_START,
    APPROVAL_STATUS,
    EARLIEST_PUBLISHED_FREE_HISTORY_ON_PROPOSAL_DATE,
    MINIMUM_ROLLING_LOOKBACK_BUFFER_DAYS,
    PROPOSAL_AS_OF,
    PROPOSAL_VALID_UNTIL,
    SPLITS,
    SUPERSEDED_PROPOSAL_SHA256,
    proposal_package,
    assert_capture_window_current,
    proposed_capture_scope,
    required_approval_text,
)
from core.research.conservative_baseline_campaign_v2_revision_proposal import (
    proposal_package as revision_1_proposal_package,
)


def test_revision_2_window_is_completed_contiguous_and_buffered():
    start = date.fromisoformat(ACQUISITION_START)
    end = date.fromisoformat(ACQUISITION_END)
    earliest = date.fromisoformat(EARLIEST_PUBLISHED_FREE_HISTORY_ON_PROPOSAL_DATE)
    assert start == date(2024, 10, 1)
    assert end == date(2025, 7, 31)
    assert end <= date.fromisoformat(PROPOSAL_AS_OF)
    assert (start - earliest).days >= MINIMUM_ROLLING_LOOKBACK_BUFFER_DAYS
    assert date.fromisoformat(PROPOSAL_VALID_UNTIL) == date(2026, 9, 1)
    expected = start
    for split in SPLITS:
        assert date.fromisoformat(split["start"]) == expected
        assert date.fromisoformat(split["end"]) <= date.fromisoformat(PROPOSAL_AS_OF)
        expected = date.fromisoformat(split["end"]) + timedelta(days=1)
    assert expected == end + timedelta(days=1)


def test_revision_2_capture_scope_includes_unadjusted_bars_and_corporate_actions():
    capture = proposed_capture_scope()
    endpoints = capture["endpoints"]
    assert endpoints["DAILY_BARS"]["non_secret_query"]["adjusted"] == "false"
    assert endpoints["DIVIDENDS"]["path"] == "/stocks/v1/dividends"
    assert "ex_dividend_date.gte" in endpoints["DIVIDENDS"][
        "per_symbol_date_filters"
    ]
    assert endpoints["STOCK_SPLITS"]["path"] == "/stocks/v1/splits"
    assert "execution_date.lte" in endpoints["STOCK_SPLITS"][
        "per_symbol_date_filters"
    ]
    assert capture["counts"]["by_role"] == {
        "TRAIN": {
            "bar_requests": 15,
            "corporate_action_initial_requests": 6,
            "initial_requests": 21,
        },
        "VALIDATION": {
            "bar_requests": 6,
            "corporate_action_initial_requests": 6,
            "initial_requests": 12,
        },
        "UNTOUCHED_TEST": {
            "bar_requests": 9,
            "corporate_action_initial_requests": 6,
            "initial_requests": 15,
        },
    }
    assert capture["provider_request_allowed"] is False
    assert capture["untouched_test_request_allowed"] is False
    assert capture["endpoints"]["DIVIDENDS"]["forbidden_evaluation_fields"] == [
        "historical_adjustment_factor",
        "split_adjusted_cash_amount",
    ]
    assert capture["endpoints"]["STOCK_SPLITS"]["forbidden_evaluation_fields"] == [
        "historical_adjustment_factor"
    ]


def test_guardrailed_engine_is_sole_authority_and_every_capability_is_false():
    proposal = proposal_package()
    protocol = proposal["evaluation_protocol"]
    assert protocol["authoritative_evaluator"] == (
        "core.guardrailed_backtest:GuardrailedBacktestEngine"
    )
    assert protocol["vectorbt_on_critical_path"] is False
    assert protocol["vectorbt_required"] is False
    assert protocol["parameter_screening_allowed"] is False
    assert protocol["indicator_warmup_source"].startswith("STRICTLY_EARLIER")
    action_policy = protocol["corporate_action_total_return_policy"]
    assert action_policy["dividend_amount_field"] == "cash_amount"
    assert action_policy["dividend_accrual_event"] == "EX_DIVIDEND_DATE"
    assert action_policy["dividend_reinvestment"] is False
    assert action_policy["events_outside_acquisition_window_allowed"] is False
    assert action_policy["forbidden_provider_adjusted_fields"] == [
        "historical_adjustment_factor",
        "split_adjusted_cash_amount",
    ]
    assert proposal["test_evidence_classification"]["semantic_role"] == (
        "SEALED_RETROSPECTIVE_TEST"
    )
    for field in (
        "data_calls_allowed",
        "api_key_request_allowed",
        "evaluation_allowed",
        "preregistration_append_allowed",
        "provider_bytes_accessed",
        "untouched_test_opened",
        "dataset_admitted",
        "performance_claim_allowed",
        "aws_resource_creation_allowed",
        "broker_connection_allowed",
        "orders_submitted",
        "live_trading_enabled",
    ):
        assert proposal[field] is False


def test_revision_2_hash_is_stable_and_supersession_is_conditional():
    first = proposal_package()
    assert first == proposal_package()
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
    assert revision_1_proposal_package()["proposal_sha256"] == (
        SUPERSEDED_PROPOSAL_SHA256
    )
    assert first["supersession_effect"].startswith("ONLY_AFTER_EXACT")
    assert required_approval_text().endswith(first["proposal_sha256"])


def test_revision_2_rejects_eroded_rolling_window(monkeypatch):
    monkeypatch.setattr(
        module,
        "EARLIEST_PUBLISHED_FREE_HISTORY_ON_PROPOSAL_DATE",
        "2024-09-15",
    )
    with pytest.raises(ValueError, match="rolling-history"):
        module.proposal_package()


def test_revision_2_capture_activation_gate_expires_deterministically():
    assert_capture_window_current(as_of="2026-09-01")
    with pytest.raises(ValueError, match="expired"):
        assert_capture_window_current(as_of="2026-09-02")


def test_revision_2_execution_policy_does_not_mutate_revision_1_identity():
    before = revision_1_proposal_package()["proposal_sha256"]
    module.proposed_execution_policy()
    after = revision_1_proposal_package()["proposal_sha256"]
    assert before == after == SUPERSEDED_PROPOSAL_SHA256


def test_revision_2_proposal_is_inert_and_has_no_vectorbt_import():
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
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
        "core.research.conservative_baseline_campaign_v2_proposal",
        "core.research.conservative_baseline_campaign_v2_revision_proposal",
        "core.research.conservative_baseline_strategy",
    }
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls & {"open", "eval", "exec", "__import__"}


def test_revision_2_digest_is_anchored_in_authoritative_documents():
    root = Path(__file__).resolve().parents[1]
    digest = proposal_package()["proposal_sha256"]
    for relative in (
        "docs/PROJECT_STATUS.md",
        "docs/MASTER_ROADMAP_COMPLETION_AUDIT.md",
    ):
        assert digest in (root / relative).read_text(encoding="utf-8")
