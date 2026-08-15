from __future__ import annotations

"""Derive the only engine configuration allowed by a preregistered scenario."""

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import re
from typing import Any, Mapping

from core.guardrailed_backtest import (
    BacktestConfig,
    ExchangeFeeSchedule,
    ExchangeFeeTier,
    canonical_engine_configuration,
)
from core.orchestration.historical_quarantine_preregistration import (
    HistoricalQuarantinePreregistrationLedger,
)


PROFILE_VERSION = "preregistered-daily-replay-engine-profile-v1"
CAMPAIGN_PROFILE_VERSION = "preregistered-conservative-campaign-engine-profile-v2"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SCENARIOS = {"BASE", "PESSIMISTIC"}


def _required(value: Any, name: str, maximum: int = 300) -> str:
    resolved = str(value or "").strip()
    if not resolved or len(resolved) > maximum:
        raise ValueError(f"{name} is required and must not exceed {maximum} characters")
    return resolved


def _hash(value: Any, name: str) -> str:
    resolved = _required(value, name, 64).lower()
    if not SHA256.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _decimal(value: Any, name: str) -> Decimal:
    try:
        resolved = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite() or resolved < 0:
        raise ValueError(f"{name} must be a non-negative finite decimal")
    return resolved


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


@dataclass(frozen=True)
class AuthenticatedExecutionProfile:
    profile_version: str
    replay_plan_id: str
    replay_plan_record_hash: str
    replay_execution_policy_record_id: str
    replay_execution_policy_record_hash: str
    execution_policy_id: str
    execution_policy_version: str
    scenario: str
    config: BacktestConfig
    fee_schedule: ExchangeFeeSchedule
    engine_config_canonical_json: str
    engine_config_sha256: str
    campaign_preregistration_id: str | None = None
    campaign_preregistration_record_hash: str | None = None
    campaign_execution_policy_sha256: str | None = None
    simulation_only: bool = True
    network_allowed: bool = False
    broker_connection_allowed: bool = False
    orders_submitted: bool = False
    live_trading_enabled: bool = False


def resolve_authenticated_execution_profile(
    policy_record: Mapping[str, Any],
    scenario: str,
    *,
    campaign_preregistration_ledger: (
        HistoricalQuarantinePreregistrationLedger | None
    ) = None,
    campaign_preregistration_id: str | None = None,
) -> AuthenticatedExecutionProfile:
    """Create fixed settings from verified policy and optional campaign records.

    Omitting both campaign arguments preserves the exact legacy-v1 interpretation
    used by existing replay audit records.  Supplying both selects the stricter
    campaign-v2 profile only after the immutable ledger re-verifies the exact
    preregistration and its policy identity matches the approved campaign.
    """
    resolved_scenario = _required(scenario, "scenario", 20).upper()
    if resolved_scenario not in SCENARIOS:
        raise ValueError("execution scenario must be BASE or PESSIMISTIC")
    scenarios = policy_record.get("scenarios")
    if not isinstance(scenarios, Mapping) or set(scenarios) != SCENARIOS:
        raise ValueError("verified execution policy must contain exact scenarios")
    values = scenarios[resolved_scenario]
    if not isinstance(values, Mapping):
        raise ValueError("execution scenario is malformed")

    campaign_identifiers: dict[str, str | None] = {
        "campaign_preregistration_id": None,
        "campaign_preregistration_record_hash": None,
        "campaign_execution_policy_sha256": None,
    }
    profile_version = PROFILE_VERSION
    campaign_requested = (
        campaign_preregistration_ledger is not None
        or campaign_preregistration_id is not None
    )
    if not campaign_requested:
        stop_pierce = Decimal("0.5") if resolved_scenario == "BASE" else Decimal("1")
        config = BacktestConfig(
            initial_cash=Decimal("100000"),
            max_equity_risk_per_trade=Decimal("0.01"),
            maximum_aggregate_open_risk=Decimal("0.06"),
            atr_window=14,
            atr_stop_multiple=Decimal("2"),
            baseline_slippage_bps=_decimal(
                values.get("slippage_bps_per_side"), "slippage_bps_per_side"
            ),
            bid_ask_half_spread_bps=_decimal(
                values.get("spread_bps_per_side"), "spread_bps_per_side"
            ),
            latency_adverse_bps=_decimal(
                values.get("latency_bps_per_side"), "latency_bps_per_side"
            ),
            liquidity_impact_bps_at_max_participation=_decimal(
                values.get("market_impact_bps_per_side"),
                "market_impact_bps_per_side",
            ),
            stop_pierce_fill_fraction=stop_pierce,
            lagged_liquidity_lookback=20,
            maximum_lagged_volume_participation=_decimal(
                values.get("maximum_volume_participation_rate"),
                "maximum_volume_participation_rate",
            ),
            allow_fractional_shares=False,
            cash_settlement_sessions=1,
            maximum_order_age_minutes=_decimal(
                values.get("maximum_order_age_minutes"),
                "maximum_order_age_minutes",
            ),
            execution_scenario=resolved_scenario,
        )
    else:
        if (
            not isinstance(
                campaign_preregistration_ledger,
                HistoricalQuarantinePreregistrationLedger,
            )
            or campaign_preregistration_id is None
        ):
            raise ValueError(
                "campaign profile requires its preregistration ledger and id"
            )
        campaign_preregistration_record = campaign_preregistration_ledger.require(
            _required(campaign_preregistration_id, "campaign preregistration_id")
        )
        from core.research.conservative_baseline_campaign import (
            ACQUISITION_END,
            ACQUISITION_START,
            CAMPAIGN_POLICY_VERSION,
            SPLITS,
            approved_evaluation_protocol,
            approved_execution_policy,
            approved_execution_policy_sha256,
            approved_execution_profile,
        )
        from core.research.conservative_baseline_strategy import (
            POLICY_VERSION as STRATEGY_VERSION,
            ConservativeBaselineStrategy,
            conservative_baseline_parameters,
        )

        campaign = approved_execution_policy()
        campaign_hash = approved_execution_policy_sha256()
        evaluation = campaign_preregistration_record.get("evaluation_protocol")
        if not isinstance(evaluation, Mapping):
            raise ValueError("verified campaign preregistration lacks evaluation protocol")
        expected_evaluation = approved_evaluation_protocol()
        expected_evaluation["success_thresholds_canonical_json"] = _canonical_json(
            expected_evaluation.pop("success_thresholds")
        )
        expected_entrypoint = (
            "core.research.conservative_baseline_strategy:"
            f"{ConservativeBaselineStrategy.__name__}"
        )
        if (
            campaign_preregistration_record.get("acquisition_start")
            != ACQUISITION_START
            or campaign_preregistration_record.get("acquisition_end")
            != ACQUISITION_END
            or campaign_preregistration_record.get("splits")
            != [dict(value) for value in SPLITS]
            or campaign_preregistration_record.get("strategy_entrypoint")
            != expected_entrypoint
            or campaign_preregistration_record.get("strategy_source_path")
            != "core/research/conservative_baseline_strategy.py"
            or campaign_preregistration_record.get("strategy_version")
            != STRATEGY_VERSION
            or campaign_preregistration_record.get("parameter_space_canonical_json")
            != _canonical_json(conservative_baseline_parameters())
            or dict(evaluation) != expected_evaluation
            or evaluation.get("execution_policy_version") != CAMPAIGN_POLICY_VERSION
            or _hash(
                evaluation.get("execution_policy_sha256"),
                "campaign execution_policy_sha256",
            )
            != campaign_hash
            or policy_record.get("execution_policy_id") != campaign["cost_policy_id"]
            or policy_record.get("execution_policy_version")
            != campaign["cost_policy_version"]
            or scenarios != campaign["scenarios"]
        ):
            raise ValueError(
                "verified replay policy does not match the approved campaign contract"
            )
        approved = approved_execution_profile(resolved_scenario)
        config = approved.config
        profile_version = CAMPAIGN_PROFILE_VERSION
        campaign_identifiers = {
            "campaign_preregistration_id": _required(
                campaign_preregistration_record.get("preregistration_id"),
                "campaign preregistration_id",
            ),
            "campaign_preregistration_record_hash": _hash(
                campaign_preregistration_record.get("record_hash"),
                "campaign preregistration_record_hash",
            ),
            "campaign_execution_policy_sha256": campaign_hash,
        }
    policy_record_id = _required(
        policy_record.get("replay_execution_policy_record_id"),
        "replay_execution_policy_record_id",
    )
    fee_schedule = ExchangeFeeSchedule(
        f"{policy_record_id}-{resolved_scenario}-COMMISSION",
        (
            ExchangeFeeTier(
                None,
                _decimal(
                    values.get("commission_bps_per_side"),
                    "commission_bps_per_side",
                ),
                Decimal("0"),
            ),
        ),
    )
    canonical = canonical_engine_configuration(config, fee_schedule)
    return AuthenticatedExecutionProfile(
        profile_version=profile_version,
        replay_plan_id=_required(policy_record.get("replay_plan_id"), "replay_plan_id"),
        replay_plan_record_hash=_hash(
            policy_record.get("replay_plan_record_hash"), "replay_plan_record_hash"
        ),
        replay_execution_policy_record_id=policy_record_id,
        replay_execution_policy_record_hash=_hash(
            policy_record.get("record_hash"), "replay_execution_policy_record_hash"
        ),
        execution_policy_id=_required(
            policy_record.get("execution_policy_id"), "execution_policy_id"
        ),
        execution_policy_version=_required(
            policy_record.get("execution_policy_version"), "execution_policy_version"
        ),
        scenario=resolved_scenario,
        config=config,
        fee_schedule=fee_schedule,
        engine_config_canonical_json=canonical,
        engine_config_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        **campaign_identifiers,
    )
