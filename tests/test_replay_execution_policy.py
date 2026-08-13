import pytest

from core.orchestration.replay_execution_policy import ReplayExecutionPolicy


SCENARIOS = {
    "BASE": {
        "commission_bps_per_side": "1", "spread_bps_per_side": "2",
        "slippage_bps_per_side": "3", "latency_bps_per_side": "1",
        "market_impact_bps_per_side": "2", "maximum_volume_participation_rate": "0.05",
        "maximum_order_age_minutes": "30",
    },
    "PESSIMISTIC": {
        "commission_bps_per_side": "2", "spread_bps_per_side": "4",
        "slippage_bps_per_side": "6", "latency_bps_per_side": "2",
        "market_impact_bps_per_side": "4", "maximum_volume_participation_rate": "0.02",
        "maximum_order_age_minutes": "60",
    },
}


def define(**changes):
    values = {name: dict(item) for name, item in SCENARIOS.items()}
    for name, mutation in changes.items():
        values[name].update(mutation)
    return ReplayExecutionPolicy.define(values)


def test_defines_next_tradable_partial_fill_policy_without_execution():
    result = define()
    assert result["same_bar_close_execution_allowed"] is False
    assert result["execution_price_policy"].startswith("FIRST_ELIGIBLE_NEXT_TRADABLE")
    for field in (
        "market_calendar_required", "halt_and_market_closure_handling_required",
        "volume_evidence_required", "participation_cap_required", "partial_fills_required",
        "rejections_required", "cancellations_required", "buy_cash_and_fee_check_required",
        "sell_position_check_required", "settled_cash_policy_required",
        "fractional_share_policy_required", "pessimistic_scenario_must_pass",
    ):
        assert result[field] is True
    for field in (
        "network_allowed", "broker_connection_allowed", "orders_submitted",
        "fills_generated", "performance_calculated", "live_trading_enabled",
    ):
        assert result[field] is False


def test_identity_is_deterministic():
    assert define() == define()


@pytest.mark.parametrize("changes,fragment", [
    ({"BASE": {"spread_bps_per_side": "0.5"}}, "at least 1"),
    ({"BASE": {"maximum_volume_participation_rate": "0.11"}}, "10%"),
    ({"PESSIMISTIC": {"slippage_bps_per_side": "5"}}, "twice"),
    ({"PESSIMISTIC": {"maximum_volume_participation_rate": "0.06"}}, "must not exceed"),
    ({"PESSIMISTIC": {"maximum_order_age_minutes": "20"}}, "must not be shorter"),
])
def test_unrealistic_or_non_pessimistic_settings_fail_closed(changes, fragment):
    with pytest.raises(ValueError, match=fragment):
        define(**changes)


def test_requires_exact_scenarios():
    with pytest.raises(ValueError, match="exactly"):
        ReplayExecutionPolicy.define({"BASE": SCENARIOS["BASE"]})
