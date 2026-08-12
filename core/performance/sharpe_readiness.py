from __future__ import annotations

"""Fail-closed paired-evidence gate before a Sharpe ratio may exist."""

import hashlib
from typing import Any, Mapping

from core.performance.daily_risk_free_return import DailyRiskFreeReturnLedger
from core.performance.metric_readiness import PerformanceMetricReadinessGate
from core.performance.portfolio_valuation import _as_datetime, _canonical_json


SHARPE_READINESS_POLICY_VERSION = "sharpe-paired-daily-return-sofr-v1"
POLICY = {
    "portfolio_return_series": "AUTHORITATIVE_DAILY_RETURN_LEDGER_V2_READY",
    "risk_free_series": "EXACTLY_ONE_MATCHED_FINAL_SOFR_INDEX_RETURN_PER_DAILY_RETURN",
    "pairing_key": "DAILY_PORTFOLIO_RETURN_ID_AND_RECORD_HASH",
    "date_policy": "EXACT_PREVIOUS_AND_CURRENT_MARKET_SESSION_DATES",
    "extra_or_duplicate_support_allowed": False,
    "future_formula": "sqrt(252) * sample_mean(daily_return - risk_free_return) / sample_stddev(daily_return - risk_free_return)",
}


def _snapshot_hash(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


class SharpeMetricReadinessGate:
    """Prove complete daily/risk-free pairing without calculating Sharpe."""

    def __init__(
        self,
        metric_readiness_gate: PerformanceMetricReadinessGate,
        risk_free_return_ledger: DailyRiskFreeReturnLedger,
    ) -> None:
        self.metric_readiness_gate = metric_readiness_gate
        self.risk_free_return_ledger = risk_free_return_ledger

    def assess(self, *, portfolio_version: str, through_horizon: str) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        horizon = str(through_horizon or "").upper()
        base = self.metric_readiness_gate.assess(
            portfolio_version=version, through_horizon=horizon
        )
        reasons = []
        daily_status = base["metrics"]["VOLATILITY"]
        if daily_status["status"] != "EVIDENCE_READY":
            reasons.extend(daily_status["reasons"])
        target = next(
            (
                item
                for item in self.metric_readiness_gate.valuation_ledger.verify()
                if item.get("portfolio_version") == version
                and item.get("horizon") == horizon
            ),
            None,
        )
        daily_returns = []
        if target is not None and self.metric_readiness_gate.daily_return_ledger is not None:
            target_at = _as_datetime(target["outcome_asset_price_effective_at"])
            daily_returns = sorted(
                (
                    item
                    for item in self.metric_readiness_gate.daily_return_ledger.verify()
                    if item.get("portfolio_version") == version
                    and item.get("daily_return_calculated") is True
                    and _as_datetime(item["current_effective_at"]) <= target_at
                ),
                key=lambda item: _as_datetime(item["current_effective_at"]),
            )
        cutoff_date = (
            daily_returns[-1].get("current_market_session_date")
            if daily_returns
            else None
        )
        risk_free_returns = [
            item
            for item in self.risk_free_return_ledger.verify()
            if item.get("portfolio_version") == version
            and item.get("daily_risk_free_return_calculated") is True
            and cutoff_date is not None
            and str(item.get("current_market_session_date") or "") <= cutoff_date
        ]
        by_daily_id = {}
        duplicate = False
        for item in risk_free_returns:
            daily_id = item.get("daily_portfolio_return_id")
            if daily_id in by_daily_id:
                duplicate = True
            by_daily_id[daily_id] = item
        if duplicate:
            reasons.append("Risk-free support contains duplicate daily-return pairings.")
        expected_ids = {item.get("result_id") for item in daily_returns}
        if set(by_daily_id) != expected_ids:
            reasons.append(
                "Risk-free support must match the authoritative daily-return interval exactly."
            )
        identity_fields = ("strategy_version", "model_versions", "git_revision")
        paired = []
        for daily_return in daily_returns:
            risk_free = by_daily_id.get(daily_return.get("result_id"))
            if risk_free is None:
                continue
            valid = (
                risk_free.get("daily_portfolio_return_record_hash")
                == daily_return.get("record_hash")
                and risk_free.get("previous_market_session_date")
                == daily_return.get("previous_market_session_date")
                and risk_free.get("current_market_session_date")
                == daily_return.get("current_market_session_date")
                and all(
                    risk_free.get(field) == daily_return.get(field)
                    for field in identity_fields
                )
            )
            if not valid:
                reasons.append(
                    "Every risk-free result must pin the same daily return, dates and identity."
                )
                break
            paired.append((daily_return, risk_free))
        if len(paired) != len(daily_returns):
            reasons.append("Every daily return requires one valid matched risk-free return.")
        reasons = sorted(set(reasons))
        snapshot = {
            "policy_version": SHARPE_READINESS_POLICY_VERSION,
            "policy": POLICY,
            "base_readiness_policy_version": base["policy_version"],
            "base_evidence_snapshot_sha256": base["evidence_snapshot_sha256"],
            "daily_return_ids": [item[0]["result_id"] for item in paired],
            "daily_return_record_hashes": [item[0]["record_hash"] for item in paired],
            "risk_free_return_ids": [item[1]["result_id"] for item in paired],
            "risk_free_return_record_hashes": [item[1]["record_hash"] for item in paired],
        }
        ready = not reasons and bool(daily_returns)
        return {
            "policy_version": SHARPE_READINESS_POLICY_VERSION,
            "policy": POLICY,
            "status": "EVIDENCE_READY" if ready else "BLOCKED",
            "simulation_only": True,
            "portfolio_version": version,
            "through_horizon": horizon,
            "reasons": reasons,
            "base_readiness_policy_version": base["policy_version"],
            "base_evidence_snapshot_sha256": base["evidence_snapshot_sha256"],
            "daily_return_observation_count": len(daily_returns),
            "risk_free_return_observation_count": len(risk_free_returns),
            "matched_pair_count": len(paired),
            "complete_pairing": ready,
            "evidence_snapshot_sha256": _snapshot_hash(snapshot),
            "sharpe_calculated": False,
            "sortino_calculated": False,
            "risk_adjusted_metric_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
        }
