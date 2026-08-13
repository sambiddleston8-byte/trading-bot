from __future__ import annotations

"""Versioned execution-realism policy for future historical replay."""

from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Mapping


POLICY_VERSION = "next-tradable-participation-capped-execution-v1"
SCENARIOS = ("BASE", "PESSIMISTIC")
MINIMUM_COST_BPS = Decimal("1")
MAXIMUM_PARTICIPATION_RATE = Decimal("0.10")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _decimal(value: Any, name: str, *, positive: bool = False) -> str:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite() or resolved < 0 or (positive and resolved <= 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be a {qualifier} finite decimal")
    return "0" if resolved == 0 else format(resolved.normalize(), "f")


def _scenario(name: str, value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a scenario object")
    result = {
        "commission_bps_per_side": _decimal(
            value.get("commission_bps_per_side"), f"{name}.commission", positive=False
        ),
        "spread_bps_per_side": _decimal(
            value.get("spread_bps_per_side"), f"{name}.spread", positive=True
        ),
        "slippage_bps_per_side": _decimal(
            value.get("slippage_bps_per_side"), f"{name}.slippage", positive=True
        ),
        "latency_bps_per_side": _decimal(
            value.get("latency_bps_per_side"), f"{name}.latency", positive=True
        ),
        "market_impact_bps_per_side": _decimal(
            value.get("market_impact_bps_per_side"), f"{name}.market_impact", positive=True
        ),
        "maximum_volume_participation_rate": _decimal(
            value.get("maximum_volume_participation_rate"), f"{name}.participation", positive=True
        ),
        "maximum_order_age_minutes": _decimal(
            value.get("maximum_order_age_minutes"), f"{name}.order_age", positive=True
        ),
    }
    if any(
        Decimal(result[field]) < MINIMUM_COST_BPS
        for field in (
            "spread_bps_per_side", "slippage_bps_per_side",
            "latency_bps_per_side", "market_impact_bps_per_side",
        )
    ):
        raise ValueError(f"{name} non-commission costs must each be at least 1 basis point")
    if Decimal(result["maximum_volume_participation_rate"]) > MAXIMUM_PARTICIPATION_RATE:
        raise ValueError(f"{name} maximum participation cannot exceed 10%")
    return result


class ReplayExecutionPolicy:
    """Validate an inert base/pessimistic execution contract; no fill is generated."""

    @classmethod
    def define(cls, scenarios: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        if not isinstance(scenarios, Mapping) or set(scenarios) != set(SCENARIOS):
            raise ValueError("scenarios must contain exactly BASE and PESSIMISTIC")
        resolved = {name: _scenario(name, scenarios[name]) for name in SCENARIOS}
        base = resolved["BASE"]
        pessimistic = resolved["PESSIMISTIC"]
        cost_fields = (
            "commission_bps_per_side", "spread_bps_per_side", "slippage_bps_per_side",
            "latency_bps_per_side", "market_impact_bps_per_side",
        )
        if any(Decimal(pessimistic[field]) < Decimal(base[field]) * 2 for field in cost_fields):
            raise ValueError("pessimistic per-side costs must be at least twice base costs")
        if (
            Decimal(pessimistic["maximum_volume_participation_rate"])
            > Decimal(base["maximum_volume_participation_rate"])
        ):
            raise ValueError("pessimistic participation must not exceed base participation")
        if (
            Decimal(pessimistic["maximum_order_age_minutes"])
            < Decimal(base["maximum_order_age_minutes"])
        ):
            raise ValueError("pessimistic order age must not be shorter than base order age")
        identity = [resolved, POLICY_VERSION]
        return {
            "execution_policy_id": "EXEC-" + hashlib.sha256(
                _canonical_json(identity).encode("utf-8")
            ).hexdigest()[:32].upper(),
            "policy_version": POLICY_VERSION,
            "status": "EXECUTION_REALISM_POLICY_DEFINED_NOT_EXECUTED",
            "scenarios": resolved,
            "decision_price_execution_allowed": False,
            "same_bar_close_execution_allowed": False,
            "execution_price_policy": "FIRST_ELIGIBLE_NEXT_TRADABLE_OBSERVATION_AFTER_DECISION",
            "market_calendar_required": True,
            "halt_and_market_closure_handling_required": True,
            "volume_evidence_required": True,
            "participation_cap_required": True,
            "partial_fills_required": True,
            "rejections_required": True,
            "cancellations_required": True,
            "unfilled_remainder_carried_or_cancelled_explicitly": True,
            "buy_cash_and_fee_check_required": True,
            "sell_position_check_required": True,
            "settled_cash_policy_required": True,
            "fractional_share_policy_required": True,
            "regulatory_fees_and_borrow_costs_required_when_applicable": True,
            "favourable_scenario_can_support_promotion": False,
            "base_scenario_can_support_promotion_alone": False,
            "pessimistic_scenario_must_pass": True,
            "network_allowed": False,
            "broker_connection_allowed": False,
            "orders_submitted": False,
            "fills_generated": False,
            "performance_calculated": False,
            "live_trading_enabled": False,
        }
