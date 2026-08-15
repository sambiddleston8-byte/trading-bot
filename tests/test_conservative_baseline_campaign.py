from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from core.orchestration.historical_quarantine_preregistration import (
    HistoricalQuarantinePreregistrationLedger,
)
from core.research.conservative_baseline_campaign import (
    ACQUISITION_END,
    ACQUISITION_START,
    CAMPAIGN_POLICY_VERSION,
    approved_evaluation_protocol,
    approved_execution_policy,
    approved_execution_policy_sha256,
    approved_execution_profile,
    approved_quarantine_definition,
)


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "research"
    / "conservative_baseline_campaign.py"
)
ROOT = Path(__file__).resolve().parents[1]


def test_campaign_definition_binds_approved_window_strategy_and_single_test():
    entitlement = {
        "plan_name": "TEST_ONLY",
        "terms_uri": "https://example.test/terms",
        "terms_retrieved_at": "2026-08-15T00:00:00+00:00",
        "terms_payload_sha256": "a" * 64,
        "asserted_request_limit_per_minute": 5,
        "asserted_incremental_cost_usd": "0.00",
    }
    result = approved_quarantine_definition(
        registered_by="SAM_AND_PAT_LOCAL_RESEARCH",
        entitlement_metadata=entitlement,
    )

    assert result["acquisition_start"] == ACQUISITION_START == "2025-08-01"
    assert result["acquisition_end"] == ACQUISITION_END == "2026-07-31"
    assert [value["role"] for value in result["splits"]] == [
        "TRAIN",
        "VALIDATION",
        "UNTOUCHED_TEST",
    ]
    assert result["strategy_entrypoint"].endswith(":ConservativeBaselineStrategy")
    assert result["parameter_space"]["parameter_search_allowed"] is False
    assert result["evaluation_protocol"] == approved_evaluation_protocol()
    assert result["evaluation_protocol"]["maximum_untouched_test_evaluations"] == 1
    assert result["entitlement_metadata"] == entitlement
    assert result["entitlement_metadata"] is not entitlement


def test_definition_passes_the_real_quarantine_preregistration_schema(tmp_path):
    now = datetime.now(timezone.utc)
    definition = approved_quarantine_definition(
        registered_by="SAM_AND_PAT_LOCAL_RESEARCH",
        entitlement_metadata={
            "plan_name": "STOCKS_BASIC_FREE",
            "terms_uri": "https://massive.com/stocks",
            "terms_retrieved_at": (now - timedelta(minutes=1)).isoformat(),
            "terms_payload_sha256": "a" * 64,
            "asserted_request_limit_per_minute": 5,
            "asserted_incremental_cost_usd": "0.00",
        },
    )
    ledger = HistoricalQuarantinePreregistrationLedger(
        tmp_path / "preregistration.jsonl",
        repository_root=ROOT,
        clock=lambda: now,
        git_revision_resolver=lambda _: "b" * 40,
        worktree_clean_resolver=lambda _: True,
    )

    record = ledger.preregister(**definition)

    assert record["evaluation_protocol"]["execution_policy_sha256"] == (
        approved_execution_policy_sha256()
    )
    assert record["strategy_source_path"] == (
        "core/research/conservative_baseline_strategy.py"
    )
    assert record["parameter_space_canonical_json"]
    assert record["quarantine_only"] is True
    assert record["dataset_admitted"] is False
    assert record["guardrailed_replay_executed"] is False
    assert ledger.verify() == [record]


def test_success_thresholds_require_both_candidates_without_symbol_selection():
    thresholds = approved_evaluation_protocol()["success_thresholds"]

    assert thresholds == {
        "all_candidate_symbols_must_pass": ["AAPL", "MSFT"],
        "benchmark_only_symbol": "SPY",
        "cross_symbol_selection_allowed": False,
        "maximum_drawdown_each_candidate": "0.20",
        "minimum_completed_trades_each_candidate": 2,
        "minimum_spy_relative_return_each_candidate": "0",
        "minimum_total_return_each_candidate": "0",
        "performance_claim_allowed": False,
        "pessimistic_scenario_must_pass": True,
    }


def test_execution_policy_hash_is_stable_and_every_authority_remains_false():
    first = approved_execution_policy()
    second = approved_execution_policy()

    assert first == second
    assert first is not second
    assert len(approved_execution_policy_sha256()) == 64
    assert first["campaign_policy_version"] == CAMPAIGN_POLICY_VERSION
    assert first["parameter_search_allowed"] is False
    assert first["same_bar_execution_allowed"] is False
    assert first["next_open_execution_required"] is True
    for field in (
        "performance_claim_allowed",
        "broker_connection_allowed",
        "orders_submitted",
        "live_trading_enabled",
    ):
        assert first[field] is False


def test_approved_base_and_pessimistic_profiles_pin_risk_costs_and_position_cap():
    base = approved_execution_profile("BASE")
    pessimistic = approved_execution_profile("PESSIMISTIC")

    assert base.config.initial_cash == Decimal("100000")
    assert base.config.maximum_position_fraction == Decimal("0.25")
    assert base.config.max_equity_risk_per_trade == Decimal("0.01")
    assert base.config.maximum_aggregate_open_risk == Decimal("0.01")
    assert base.config.atr_window == 14
    assert base.config.atr_stop_multiple == Decimal("2")
    assert base.config.baseline_slippage_bps == Decimal("10")
    assert base.config.bid_ask_half_spread_bps == Decimal("5")
    assert base.config.latency_adverse_bps == Decimal("2")
    assert base.config.liquidity_impact_bps_at_max_participation == Decimal("10")
    assert base.config.maximum_lagged_volume_participation == Decimal("0.02")
    assert base.config.maximum_order_age_minutes == Decimal("10080")
    assert base.fee_schedule.tiers[0].variable_bps == Decimal("1")
    assert pessimistic.config.baseline_slippage_bps == Decimal("20")
    assert pessimistic.config.bid_ask_half_spread_bps == Decimal("10")
    assert pessimistic.config.latency_adverse_bps == Decimal("4")
    assert pessimistic.config.liquidity_impact_bps_at_max_participation == Decimal("20")
    assert pessimistic.config.maximum_lagged_volume_participation == Decimal("0.01")
    assert pessimistic.config.maximum_order_age_minutes == Decimal("20160")
    assert pessimistic.config.stop_pierce_fill_fraction == Decimal("1")
    assert pessimistic.fee_schedule.tiers[0].variable_bps == Decimal("2")
    assert base.execution_policy_sha256 == pessimistic.execution_policy_sha256
    for profile in (base, pessimistic):
        assert profile.simulation_only is True
        assert profile.broker_connection_allowed is False
        assert profile.orders_submitted is False
        assert profile.live_trading_enabled is False


def test_unknown_scenario_fails_closed():
    with pytest.raises(ValueError, match="BASE or PESSIMISTIC"):
        approved_execution_profile("FAVOURABLE")


def test_campaign_policy_module_has_only_allowlisted_inert_imports():
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
        "dataclasses",
        "decimal",
        "hashlib",
        "json",
        "typing",
        "core.guardrailed_backtest",
        "core.orchestration.replay_execution_policy",
        "core.research.conservative_baseline_strategy",
    }
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls & {"__import__", "eval", "exec", "open"}
