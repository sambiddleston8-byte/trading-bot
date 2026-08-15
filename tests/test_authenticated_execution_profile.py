from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from core.guardrailed_backtest import canonical_engine_configuration
from core.orchestration import resolve_authenticated_execution_profile
from core.orchestration.authenticated_execution_profile import (
    CAMPAIGN_PROFILE_VERSION,
    PROFILE_VERSION,
)
from core.orchestration.historical_quarantine_preregistration import (
    HistoricalQuarantinePreregistrationLedger,
)
from core.research.conservative_baseline_campaign import (
    approved_quarantine_definition,
    approved_execution_policy,
    approved_execution_policy_sha256,
)


SCENARIOS = {
    "BASE": {
        "commission_bps_per_side": "0",
        "spread_bps_per_side": "5",
        "slippage_bps_per_side": "10",
        "latency_bps_per_side": "1",
        "market_impact_bps_per_side": "10",
        "maximum_volume_participation_rate": "0.02",
        "maximum_order_age_minutes": "2000",
    },
    "PESSIMISTIC": {
        "commission_bps_per_side": "0",
        "spread_bps_per_side": "10",
        "slippage_bps_per_side": "20",
        "latency_bps_per_side": "2",
        "market_impact_bps_per_side": "20",
        "maximum_volume_participation_rate": "0.01",
        "maximum_order_age_minutes": "4000",
    },
}


def policy(**changes):
    value = {
        "replay_plan_id": "REPLAY-1",
        "replay_plan_record_hash": "a" * 64,
        "replay_execution_policy_record_id": "REXP-1",
        "record_hash": "b" * 64,
        "execution_policy_id": "EXEC-1",
        "execution_policy_version": "execution-v1",
        "scenarios": {name: dict(item) for name, item in SCENARIOS.items()},
    }
    value.update(changes)
    return value


def campaign_policy():
    approved = approved_execution_policy()
    return policy(
        execution_policy_id=approved["cost_policy_id"],
        execution_policy_version=approved["cost_policy_version"],
        scenarios={name: dict(value) for name, value in approved["scenarios"].items()},
    )


ROOT = Path(__file__).resolve().parents[1]


def campaign_preregistration(tmp_path, *, substitute_strategy=False):
    now = datetime.now(timezone.utc)
    repository_root = ROOT
    definition = approved_quarantine_definition(
        registered_by="SAM_AND_PAT_LOCAL_RESEARCH",
        entitlement_metadata={
            "plan_name": "STOCKS_BASIC_FREE",
            "terms_uri": "https://massive.com/stocks",
            "terms_retrieved_at": (now - timedelta(minutes=1)).isoformat(),
            "terms_payload_sha256": "d" * 64,
            "asserted_request_limit_per_minute": 5,
            "asserted_incremental_cost_usd": "0.00",
        },
    )
    if substitute_strategy:
        repository_root = tmp_path / "substituted-repository"
        source = repository_root / "research" / "other.py"
        source.parent.mkdir(parents=True)
        source.write_text("class OtherStrategy:\n    pass\n")
        definition.update(
            strategy_entrypoint="research.other:OtherStrategy",
            strategy_source_path="research/other.py",
            strategy_version="other-strategy-v1",
            parameter_space={"fixed": True},
        )
    ledger = HistoricalQuarantinePreregistrationLedger(
        tmp_path / "campaign-preregistration.jsonl",
        repository_root=repository_root,
        clock=lambda: now,
        git_revision_resolver=lambda _: "e" * 40,
        worktree_clean_resolver=lambda _: True,
    )
    record = ledger.preregister(**definition)
    return ledger, record


def test_derives_exact_fixed_base_profile_from_verified_policy():
    result = resolve_authenticated_execution_profile(policy(), "BASE")
    assert result.config.initial_cash == Decimal("100000")
    assert result.config.max_equity_risk_per_trade == Decimal("0.01")
    assert result.config.atr_window == 14
    assert result.config.baseline_slippage_bps == Decimal("10")
    assert result.config.bid_ask_half_spread_bps == Decimal("5")
    assert result.config.latency_adverse_bps == Decimal("1")
    assert result.config.maximum_lagged_volume_participation == Decimal("0.02")
    assert result.config.maximum_order_age_minutes == Decimal("2000")
    assert result.fee_schedule.tiers[0].variable_bps == 0
    assert result.fee_schedule.tiers[0].minimum_fee == 0
    assert result.profile_version == PROFILE_VERSION
    assert result.config.maximum_position_fraction == Decimal("1")
    assert result.engine_config_canonical_json == canonical_engine_configuration(
        result.config, result.fee_schedule
    )
    assert result.simulation_only is True
    assert result.network_allowed is False
    assert result.broker_connection_allowed is False
    assert result.orders_submitted is False
    assert result.live_trading_enabled is False


def test_versioned_campaign_profile_binds_the_approved_25_percent_cap(tmp_path):
    ledger, preregistration = campaign_preregistration(tmp_path)
    result = resolve_authenticated_execution_profile(
        campaign_policy(),
        "BASE",
        campaign_preregistration_ledger=ledger,
        campaign_preregistration_id=preregistration["preregistration_id"],
    )

    assert result.profile_version == CAMPAIGN_PROFILE_VERSION
    assert result.config.initial_cash == Decimal("100000")
    assert result.config.maximum_position_fraction == Decimal("0.25")
    assert result.config.max_equity_risk_per_trade == Decimal("0.01")
    assert result.config.maximum_aggregate_open_risk == Decimal("0.01")
    assert result.campaign_preregistration_id == preregistration["preregistration_id"]
    assert result.campaign_preregistration_record_hash == preregistration["record_hash"]
    assert (
        result.campaign_execution_policy_sha256
        == approved_execution_policy_sha256()
    )
    assert result.simulation_only is True
    assert result.network_allowed is False
    assert result.broker_connection_allowed is False
    assert result.orders_submitted is False
    assert result.live_trading_enabled is False


def test_campaign_profile_requires_verified_ledger_and_exact_campaign(tmp_path):
    ledger, preregistration = campaign_preregistration(tmp_path)
    with pytest.raises(ValueError, match="ledger and id"):
        resolve_authenticated_execution_profile(
            campaign_policy(),
            "BASE",
            campaign_preregistration_ledger=ledger,
        )
    with pytest.raises(ValueError, match="not found"):
        resolve_authenticated_execution_profile(
            campaign_policy(),
            "BASE",
            campaign_preregistration_ledger=ledger,
            campaign_preregistration_id="HQP-NOT-REGISTERED",
        )

    substituted_ledger, substituted_record = campaign_preregistration(
        tmp_path / "other",
        substitute_strategy=True,
    )
    with pytest.raises(ValueError, match="approved campaign contract"):
        resolve_authenticated_execution_profile(
            campaign_policy(),
            "BASE",
            campaign_preregistration_ledger=substituted_ledger,
            campaign_preregistration_id=substituted_record["preregistration_id"],
        )

    substituted = campaign_policy()
    substituted["scenarios"] = {
        name: dict(value) for name, value in substituted["scenarios"].items()
    }
    substituted["scenarios"]["BASE"]["commission_bps_per_side"] = "2"
    with pytest.raises(ValueError, match="approved campaign contract"):
        resolve_authenticated_execution_profile(
            substituted,
            "BASE",
            campaign_preregistration_ledger=ledger,
            campaign_preregistration_id=preregistration["preregistration_id"],
        )
    wrong_version = campaign_policy()
    wrong_version["execution_policy_version"] = "wrong-version"
    with pytest.raises(ValueError, match="approved campaign contract"):
        resolve_authenticated_execution_profile(
            wrong_version,
            "BASE",
            campaign_preregistration_ledger=ledger,
            campaign_preregistration_id=preregistration["preregistration_id"],
        )


def test_derives_required_pessimistic_costs_and_constraints():
    result = resolve_authenticated_execution_profile(policy(), "PESSIMISTIC")
    assert result.config.execution_scenario == "PESSIMISTIC"
    assert result.config.stop_pierce_fill_fraction == 1
    assert result.config.baseline_slippage_bps == 20
    assert result.config.bid_ask_half_spread_bps == 10
    assert result.config.latency_adverse_bps == 2
    assert result.config.liquidity_impact_bps_at_max_participation == 20
    assert result.config.maximum_lagged_volume_participation == Decimal("0.01")


def test_unknown_scenario_weak_costs_and_malformed_policy_fail_closed():
    with pytest.raises(ValueError, match="BASE or PESSIMISTIC"):
        resolve_authenticated_execution_profile(policy(), "FAVOURABLE")
    weak = policy()
    weak["scenarios"]["PESSIMISTIC"]["slippage_bps_per_side"] = "19"
    with pytest.raises(ValueError, match="PESSIMISTIC"):
        resolve_authenticated_execution_profile(weak, "PESSIMISTIC")
    with pytest.raises(ValueError, match="exact scenarios"):
        resolve_authenticated_execution_profile(policy(scenarios={"BASE": {}}), "BASE")
