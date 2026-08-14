from decimal import Decimal

import pytest

from core.guardrailed_backtest import canonical_engine_configuration
from core.orchestration import resolve_authenticated_execution_profile


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
    assert result.engine_config_canonical_json == canonical_engine_configuration(
        result.config, result.fee_schedule
    )
    assert result.simulation_only is True
    assert result.network_allowed is False
    assert result.broker_connection_allowed is False
    assert result.orders_submitted is False
    assert result.live_trading_enabled is False


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
