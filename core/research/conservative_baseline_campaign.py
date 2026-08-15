"""Approved inert campaign contract for the fixed conservative baseline.

The helpers in this module create deterministic policy/configuration values
only.  They do not open data, append a preregistration, run a simulation,
contact a provider or broker, or submit an order.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from typing import Any, Mapping

from core.guardrailed_backtest import (
    BacktestConfig,
    ExchangeFeeSchedule,
    ExchangeFeeTier,
)
from core.orchestration.replay_execution_policy import ReplayExecutionPolicy
from core.research.conservative_baseline_strategy import (
    POLICY_VERSION as STRATEGY_VERSION,
    ConservativeBaselineStrategy,
    conservative_baseline_parameters,
)


CAMPAIGN_POLICY_VERSION = "conservative-baseline-aapl-msft-spy-campaign-v1"
ACQUISITION_START = "2025-08-01"
ACQUISITION_END = "2026-07-31"
SPLITS = (
    {"role": "TRAIN", "start": "2025-08-01", "end": "2026-02-28"},
    {"role": "VALIDATION", "start": "2026-03-01", "end": "2026-04-30"},
    {"role": "UNTOUCHED_TEST", "start": "2026-05-01", "end": "2026-07-31"},
)

_SCENARIOS = {
    "BASE": {
        "commission_bps_per_side": "1",
        "spread_bps_per_side": "5",
        "slippage_bps_per_side": "10",
        "latency_bps_per_side": "2",
        "market_impact_bps_per_side": "10",
        "maximum_volume_participation_rate": "0.02",
        "maximum_order_age_minutes": "10080",
    },
    "PESSIMISTIC": {
        "commission_bps_per_side": "2",
        "spread_bps_per_side": "10",
        "slippage_bps_per_side": "20",
        "latency_bps_per_side": "4",
        "market_impact_bps_per_side": "20",
        "maximum_volume_participation_rate": "0.01",
        "maximum_order_age_minutes": "20160",
    },
}

_SIMULATION_CONTROLS = {
    "initial_cash_per_candidate_usd": "100000",
    "maximum_position_fraction": "0.25",
    "maximum_equity_risk_per_trade": "0.01",
    "maximum_aggregate_open_risk": "0.01",
    "atr_window": 14,
    "atr_stop_multiple": "2",
    "lagged_liquidity_lookback": 20,
    "cash_settlement_sessions": 1,
    "fractional_shares_allowed": False,
    "base_stop_pierce_fill_fraction": "0.5",
    "pessimistic_stop_pierce_fill_fraction": "1",
}

_SUCCESS_THRESHOLDS = {
    "all_candidate_symbols_must_pass": ["AAPL", "MSFT"],
    "benchmark_only_symbol": "SPY",
    "minimum_total_return_each_candidate": "0",
    "minimum_spy_relative_return_each_candidate": "0",
    "maximum_drawdown_each_candidate": "0.20",
    "minimum_completed_trades_each_candidate": 2,
    "pessimistic_scenario_must_pass": True,
    "cross_symbol_selection_allowed": False,
    "performance_claim_allowed": False,
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def approved_execution_policy() -> dict[str, Any]:
    cost_policy = ReplayExecutionPolicy.define(
        {name: dict(values) for name, values in _SCENARIOS.items()}
    )
    return {
        "campaign_policy_version": CAMPAIGN_POLICY_VERSION,
        "cost_policy_version": cost_policy["policy_version"],
        "cost_policy_id": cost_policy["execution_policy_id"],
        "scenarios": cost_policy["scenarios"],
        "simulation_controls": dict(_SIMULATION_CONTROLS),
        "aapl_msft_separate_candidate_runs": True,
        "spy_benchmark_only": True,
        "parameter_search_allowed": False,
        "same_bar_execution_allowed": False,
        "next_open_execution_required": True,
        "simulation_only": True,
        "performance_claim_allowed": False,
        "broker_connection_allowed": False,
        "orders_submitted": False,
        "live_trading_enabled": False,
    }


def approved_execution_policy_sha256() -> str:
    return hashlib.sha256(
        _canonical_json(approved_execution_policy()).encode("utf-8")
    ).hexdigest()


def approved_evaluation_protocol() -> dict[str, Any]:
    return {
        "primary_metric": "COMPLETE_BENCHMARK_RELATIVE_RETURN",
        "optimization_direction": "MAXIMIZE",
        "tie_break_metrics": [
            {"metric": "MAXIMUM_DRAWDOWN", "direction": "MINIMIZE"},
            {"metric": "TURNOVER", "direction": "MINIMIZE"},
        ],
        "success_thresholds": json.loads(_canonical_json(_SUCCESS_THRESHOLDS)),
        "warmup_observations": 50,
        "purge_observations": 1,
        "embargo_observations": 1,
        "maximum_untouched_test_evaluations": 1,
        "execution_policy_version": CAMPAIGN_POLICY_VERSION,
        "execution_policy_sha256": approved_execution_policy_sha256(),
        "selection_rule_version": "both-candidates-pass-no-cross-symbol-selection-v1",
    }


def approved_quarantine_definition(
    *,
    registered_by: str,
    entitlement_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the exact definition; a separate ledger still controls any append."""

    return {
        "registered_by": str(registered_by),
        "acquisition_start": ACQUISITION_START,
        "acquisition_end": ACQUISITION_END,
        "splits": [dict(value) for value in SPLITS],
        "strategy_entrypoint": (
            "core.research.conservative_baseline_strategy:"
            f"{ConservativeBaselineStrategy.__name__}"
        ),
        "strategy_source_path": "core/research/conservative_baseline_strategy.py",
        "strategy_version": STRATEGY_VERSION,
        "parameter_space": conservative_baseline_parameters(),
        "evaluation_protocol": approved_evaluation_protocol(),
        "entitlement_metadata": dict(entitlement_metadata),
    }


@dataclass(frozen=True)
class ApprovedExecutionProfile:
    scenario: str
    config: BacktestConfig
    fee_schedule: ExchangeFeeSchedule
    execution_policy_sha256: str
    simulation_only: bool = True
    broker_connection_allowed: bool = False
    orders_submitted: bool = False
    live_trading_enabled: bool = False


def approved_execution_profile(scenario: str) -> ApprovedExecutionProfile:
    resolved = str(scenario or "").strip().upper()
    policy = approved_execution_policy()
    if resolved not in policy["scenarios"]:
        raise ValueError("scenario must be BASE or PESSIMISTIC")
    values = policy["scenarios"][resolved]
    controls = policy["simulation_controls"]
    stop_pierce = (
        controls["base_stop_pierce_fill_fraction"]
        if resolved == "BASE"
        else controls["pessimistic_stop_pierce_fill_fraction"]
    )
    config = BacktestConfig(
        initial_cash=Decimal(controls["initial_cash_per_candidate_usd"]),
        max_equity_risk_per_trade=Decimal(controls["maximum_equity_risk_per_trade"]),
        maximum_aggregate_open_risk=Decimal(controls["maximum_aggregate_open_risk"]),
        maximum_position_fraction=Decimal(controls["maximum_position_fraction"]),
        atr_window=controls["atr_window"],
        atr_stop_multiple=Decimal(controls["atr_stop_multiple"]),
        baseline_slippage_bps=Decimal(values["slippage_bps_per_side"]),
        bid_ask_half_spread_bps=Decimal(values["spread_bps_per_side"]),
        latency_adverse_bps=Decimal(values["latency_bps_per_side"]),
        liquidity_impact_bps_at_max_participation=Decimal(
            values["market_impact_bps_per_side"]
        ),
        stop_pierce_fill_fraction=Decimal(stop_pierce),
        lagged_liquidity_lookback=controls["lagged_liquidity_lookback"],
        maximum_lagged_volume_participation=Decimal(
            values["maximum_volume_participation_rate"]
        ),
        allow_fractional_shares=controls["fractional_shares_allowed"],
        cash_settlement_sessions=controls["cash_settlement_sessions"],
        maximum_order_age_minutes=Decimal(values["maximum_order_age_minutes"]),
        execution_scenario=resolved,
    )
    fees = ExchangeFeeSchedule(
        f"{CAMPAIGN_POLICY_VERSION}-{resolved}-COMMISSION",
        (
            ExchangeFeeTier(
                None,
                Decimal(values["commission_bps_per_side"]),
                Decimal("0"),
            ),
        ),
    )
    return ApprovedExecutionProfile(
        scenario=resolved,
        config=config,
        fee_schedule=fees,
        execution_policy_sha256=approved_execution_policy_sha256(),
    )
