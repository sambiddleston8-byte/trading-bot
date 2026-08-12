from __future__ import annotations

"""Fail-closed evidence gates before portfolio performance metrics exist."""

from datetime import datetime
import hashlib
from typing import Any, Mapping, Sequence

from core.performance.portfolio_return import TimeWeightedPortfolioReturnLedger
from core.performance.portfolio_valuation import (
    SimulatedPortfolioValuationLedger,
    _as_datetime,
    _canonical_json,
)


METRIC_READINESS_POLICY_VERSION = "portfolio-metric-readiness-v1"
SECONDS_PER_DAY = 86_400
MIN_CAGR_ELAPSED_SECONDS = 365 * SECONDS_PER_DAY
MIN_DAILY_VALUATIONS = 253
MIN_DAILY_SERIES_SPAN_SECONDS = 365 * SECONDS_PER_DAY
MAX_DAILY_CALENDAR_GAP_SECONDS = 4 * SECONDS_PER_DAY
POLICY = {
    "cagr": {
        "minimum_elapsed_days": 365,
        "verified_time_weighted_return_required": True,
        "future_formula": "(1 + verified_twr) ** (365.2425 / elapsed_days) - 1",
    },
    "daily_return_series": {
        "minimum_valuation_observations": MIN_DAILY_VALUATIONS,
        "minimum_calendar_span_days": 365,
        "maximum_calendar_gap_days": 4,
        "duplicate_effective_times_allowed": False,
    },
    "sharpe": "DAILY_RETURN_SERIES_PLUS_MATCHED_RISK_FREE_SERIES_REQUIRED",
    "sortino": (
        "DAILY_RETURN_SERIES_PLUS_PREDECLARED_MINIMUM_ACCEPTABLE_RETURN_"
        "AND_DOWNSIDE_SAMPLE_REQUIRED"
    ),
    "hit_rate": "INDEPENDENT_FIXED_HORIZON_OUTCOME_COHORT_REQUIRED",
    "turnover": "VERIFIED_REBALANCE_AND_EXECUTION_HISTORY_REQUIRED",
    "prediction_calibration": (
        "PREDECLARED_BUCKETS_AND_INDEPENDENT_FIXED_HORIZON_OUTCOMES_REQUIRED"
    ),
}


def _blocked(*reasons: str) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "reasons": [reason for reason in reasons if reason],
        "metric_calculated": False,
    }


def _ready() -> dict[str, Any]:
    return {"status": "EVIDENCE_READY", "reasons": [], "metric_calculated": False}


def _snapshot_hash(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


class PerformanceMetricReadinessGate:
    """Assess verified evidence without calculating or annualizing a metric."""

    def __init__(
        self,
        valuation_ledger: SimulatedPortfolioValuationLedger,
        portfolio_return_ledger: TimeWeightedPortfolioReturnLedger,
    ) -> None:
        self.valuation_ledger = valuation_ledger
        self.portfolio_return_ledger = portfolio_return_ledger

    def assess(self, *, portfolio_version: str, through_horizon: str) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        horizon = str(through_horizon or "").upper()
        valuations = [
            item
            for item in self.valuation_ledger.verify()
            if item.get("portfolio_version") == version
        ]
        target = next(
            (item for item in valuations if item.get("horizon") == horizon), None
        )
        funding = self.valuation_ledger.funding_ledger.funding_for(version)
        general_reasons = []
        if horizon == "ENTRY":
            general_reasons.append("ENTRY is a funding baseline, not a metric horizon.")
        if funding is None:
            general_reasons.append("Verified initial funding evidence is missing.")
        if target is None:
            general_reasons.append("Verified through-horizon valuation is missing.")

        selected: list[Mapping[str, Any]] = []
        if target is not None:
            target_at = _as_datetime(target["outcome_asset_price_effective_at"])
            selected = sorted(
                (
                    item
                    for item in valuations
                    if _as_datetime(item["outcome_asset_price_effective_at"])
                    <= target_at
                ),
                key=lambda item: _as_datetime(
                    item["outcome_asset_price_effective_at"]
                ),
            )
        identity_fields = ("strategy_version", "model_versions", "git_revision")
        if selected and any(
            any(item.get(field) != selected[0].get(field) for field in identity_fields)
            for item in selected
        ):
            general_reasons.append(
                "Valuation observations do not share strategy, model and Git identity."
            )

        times = [
            _as_datetime(item["outcome_asset_price_effective_at"])
            for item in selected
        ]
        unique_times = len(times) == len(set(times))
        gaps = [
            int((current - previous).total_seconds())
            for previous, current in zip(times, times[1:])
        ]
        max_gap = max(gaps) if gaps else None
        series_span = int((times[-1] - times[0]).total_seconds()) if len(times) >= 2 else 0
        initial_delay = (
            int((times[0] - _as_datetime(funding["effective_at"])).total_seconds())
            if funding is not None and times
            else None
        )
        if initial_delay is not None and initial_delay < 0:
            general_reasons.append("A valuation predates initial funding.")

        daily_reasons = []
        if len(selected) < MIN_DAILY_VALUATIONS:
            daily_reasons.append(
                f"Daily-series metrics require at least {MIN_DAILY_VALUATIONS} verified valuations."
            )
        if series_span < MIN_DAILY_SERIES_SPAN_SECONDS:
            daily_reasons.append("Daily-series metrics require at least 365 calendar days of observations.")
        if not unique_times:
            daily_reasons.append("Daily-series metrics reject duplicate valuation effective times.")
        if initial_delay is None or initial_delay > MAX_DAILY_CALENDAR_GAP_SECONDS:
            daily_reasons.append("The first valuation must be within four calendar days of funding.")
        if max_gap is None or max_gap > MAX_DAILY_CALENDAR_GAP_SECONDS:
            daily_reasons.append("Consecutive valuations cannot be more than four calendar days apart.")
        daily_ready = not general_reasons and not daily_reasons

        all_returns = self.portfolio_return_ledger.verify()
        matching_returns = [
            item
            for item in all_returns
            if item.get("portfolio_version") == version
            and item.get("through_horizon") == horizon
            and item.get("portfolio_return_calculated") is True
        ]
        twr = matching_returns[0] if len(matching_returns) == 1 else None
        if twr is not None and (
            twr.get("supporting_valuation_ids")
            != [item.get("valuation_id") for item in selected]
            or twr.get("supporting_valuation_hashes")
            != [item.get("record_hash") for item in selected]
        ):
            twr = None
        elapsed = (
            int((times[-1] - _as_datetime(funding["effective_at"])).total_seconds())
            if funding is not None and times
            else 0
        )
        cagr_reasons = list(general_reasons)
        if elapsed < MIN_CAGR_ELAPSED_SECONDS:
            cagr_reasons.append("CAGR requires at least 365 elapsed calendar days.")
        if twr is None:
            cagr_reasons.append(
                "CAGR requires exactly one verified time-weighted return pinned to the same valuations."
            )

        metrics = {
            "CAGR": _blocked(*cagr_reasons) if cagr_reasons else _ready(),
            "VOLATILITY": _ready() if daily_ready else _blocked(*general_reasons, *daily_reasons),
            "MAXIMUM_DRAWDOWN": _ready() if daily_ready else _blocked(*general_reasons, *daily_reasons),
            "SHARPE_RATIO": _blocked(
                *general_reasons,
                *daily_reasons,
                "A point-in-time risk-free return series matched to every period is not implemented.",
            ),
            "SORTINO_RATIO": _blocked(
                *general_reasons,
                *daily_reasons,
                "A predeclared minimum acceptable return and adequate downside sample are not implemented.",
            ),
            "HIT_RATE": _blocked(
                "A preregistered success rule and independent fixed-horizon outcome cohort are not implemented."
            ),
            "TURNOVER": _blocked(
                "Verified rebalancing, trade and execution history is not implemented."
            ),
            "PREDICTION_CALIBRATION": _blocked(
                "Predeclared confidence/expected-return buckets and independent outcomes are not implemented."
            ),
        }
        snapshot = {
            "policy_version": METRIC_READINESS_POLICY_VERSION,
            "policy": POLICY,
            "portfolio_version": version,
            "through_horizon": horizon,
            "funding_id": funding.get("funding_id") if funding else None,
            "funding_record_hash": funding.get("record_hash") if funding else None,
            "valuation_ids": [item.get("valuation_id") for item in selected],
            "valuation_record_hashes": [item.get("record_hash") for item in selected],
            "portfolio_return_ids": [item.get("result_id") for item in matching_returns],
            "portfolio_return_record_hashes": [item.get("record_hash") for item in matching_returns],
        }
        return {
            "policy_version": METRIC_READINESS_POLICY_VERSION,
            "status": "NOT_ASSESSABLE" if general_reasons else "ASSESSED",
            "simulation_only": True,
            "portfolio_version": version,
            "through_horizon": horizon,
            "general_reasons": general_reasons,
            "valuation_observation_count": len(selected),
            "periodic_return_observation_count": max(0, len(selected) - 1),
            "unique_effective_times": unique_times,
            "elapsed_from_funding_seconds": elapsed,
            "daily_series_span_seconds": series_span,
            "initial_observation_delay_seconds": initial_delay,
            "maximum_observation_gap_seconds": max_gap,
            "daily_cadence_ready": daily_ready,
            "verified_time_weighted_return_id": twr.get("result_id") if twr else None,
            "metrics": metrics,
            "evidence_snapshot_sha256": _snapshot_hash(snapshot),
            "policy": POLICY,
            "performance_metric_calculated": False,
            "annualized_result_calculated": False,
            "risk_adjusted_result_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
        }
