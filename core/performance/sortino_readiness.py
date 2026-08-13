from __future__ import annotations

"""Fail-closed evidence gate before a preregistered Sortino ratio may exist."""

from fractions import Fraction
import hashlib
from typing import Any, Mapping

from core.decision_ledger import normalize_model_version
from core.performance.downside_target_policy import DownsideTargetPolicyLedger
from core.performance.metric_readiness import PerformanceMetricReadinessGate
from core.performance.portfolio_valuation import _as_datetime, _canonical_json, _fraction


SORTINO_READINESS_POLICY_VERSION = "preregistered-daily-downside-v1"


def _snapshot_hash(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


class SortinoMetricReadinessGate:
    """Prove a complete future-window downside sample without calculating Sortino."""

    def __init__(
        self,
        metric_readiness_gate: PerformanceMetricReadinessGate,
        downside_policy_ledger: DownsideTargetPolicyLedger,
        risk_free_return_ledger: Any | None = None,
    ) -> None:
        self.metric_readiness_gate = metric_readiness_gate
        self.downside_policy_ledger = downside_policy_ledger
        self.risk_free_return_ledger = risk_free_return_ledger

    def assess(
        self,
        *,
        portfolio_version: str,
        through_horizon: str,
        downside_policy_id: str,
    ) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        horizon = str(through_horizon or "").upper()
        policy_id = str(downside_policy_id or "").strip()
        base = self.metric_readiness_gate.assess(
            portfolio_version=version, through_horizon=horizon
        )
        reasons: list[str] = []
        daily_status = base["metrics"]["VOLATILITY"]
        if daily_status["status"] != "EVIDENCE_READY":
            reasons.extend(daily_status["reasons"])

        policies = [
            item
            for item in self.downside_policy_ledger.verify()
            if item.get("policy_id") == policy_id
        ]
        policy = policies[0] if len(policies) == 1 else None
        if policy is None:
            reasons.append("Exactly one requested preregistered downside policy is required.")
        elif policy.get("portfolio_version") != version:
            reasons.append("The downside policy must belong to the assessed portfolio version.")

        target = next(
            (
                item
                for item in self.metric_readiness_gate.valuation_ledger.verify()
                if item.get("portfolio_version") == version
                and item.get("horizon") == horizon
            ),
            None,
        )
        daily_returns: list[dict[str, Any]] = []
        if (
            target is not None
            and policy is not None
            and self.metric_readiness_gate.daily_return_ledger is not None
        ):
            start = _as_datetime(policy["evaluation_not_before"])
            end = _as_datetime(target["outcome_asset_price_effective_at"])
            daily_returns = sorted(
                (
                    item
                    for item in self.metric_readiness_gate.daily_return_ledger.verify()
                    if item.get("portfolio_version") == version
                    and item.get("daily_return_calculated") is True
                    and start <= _as_datetime(item["current_effective_at"]) <= end
                ),
                key=lambda item: _as_datetime(item["current_effective_at"]),
            )

        if policy is not None and daily_returns:
            first_observation = _as_datetime(daily_returns[0]["current_effective_at"])
            if _as_datetime(policy["recorded_at"]) >= first_observation:
                reasons.append("The downside policy must predate every evaluated return.")
            policy_models = [
                normalize_model_version(item) for item in policy.get("model_versions", [])
            ]
            if any(
                item.get("strategy_version") != policy.get("strategy_version")
                or [
                    normalize_model_version(model)
                    for model in item.get("model_versions", [])
                ]
                != policy_models
                or item.get("git_revision") != policy.get("git_revision")
                for item in daily_returns
            ):
                reasons.append(
                    "Every evaluated return must use the preregistered strategy, model and Git identity."
                )

        risk_free_by_daily_id: dict[str, dict[str, Any]] = {}
        if policy is not None and policy.get("target_basis") == "MATCHED_DAILY_SOFR":
            if self.risk_free_return_ledger is None:
                reasons.append("Matched SOFR evidence is required by the downside policy.")
            else:
                expected = {item.get("result_id") for item in daily_returns}
                for item in self.risk_free_return_ledger.verify():
                    if (
                        item.get("portfolio_version") != version
                        or item.get("daily_portfolio_return_id") not in expected
                    ):
                        continue
                    daily_id = item.get("daily_portfolio_return_id")
                    if daily_id in risk_free_by_daily_id:
                        reasons.append("Matched SOFR evidence contains a duplicate pairing.")
                    risk_free_by_daily_id[daily_id] = item
                if set(risk_free_by_daily_id) != expected:
                    reasons.append("Every evaluated return requires matched SOFR evidence.")
                for daily in daily_returns:
                    risk_free = risk_free_by_daily_id.get(daily.get("result_id"))
                    if risk_free is None:
                        continue
                    if not (
                        risk_free.get("daily_risk_free_return_calculated") is True
                        and risk_free.get("daily_portfolio_return_record_hash")
                        == daily.get("record_hash")
                        and risk_free.get("previous_market_session_date")
                        == daily.get("previous_market_session_date")
                        and risk_free.get("current_market_session_date")
                        == daily.get("current_market_session_date")
                        and all(
                            risk_free.get(field) == daily.get(field)
                            for field in (
                                "strategy_version",
                                "model_versions",
                                "git_revision",
                            )
                        )
                    ):
                        reasons.append(
                            "Every SOFR result must pin the same daily return, dates and identity."
                        )
                        break

        downside_count = 0
        for daily in daily_returns:
            try:
                daily_value = _fraction(
                    daily["exact_fractions"]["daily_portfolio_return"],
                    "daily portfolio return",
                )
                target_value = Fraction(0)
                if policy is not None and policy.get("target_basis") == "MATCHED_DAILY_SOFR":
                    risk_free = risk_free_by_daily_id[daily["result_id"]]
                    if (
                        risk_free.get("daily_portfolio_return_record_hash")
                        != daily.get("record_hash")
                    ):
                        raise ValueError("SOFR evidence is not pinned to its daily return")
                    target_value = _fraction(
                        risk_free["exact_fractions"]["daily_risk_free_return"],
                        "daily risk-free return",
                    )
                if daily_value < target_value:
                    downside_count += 1
            except (KeyError, TypeError, ValueError) as error:
                reasons.append(str(error))
                break

        minimum_total = int(policy.get("minimum_total_observations", 0)) if policy else 0
        minimum_downside = (
            int(policy.get("minimum_downside_observations", 0)) if policy else 0
        )
        if len(daily_returns) < minimum_total:
            reasons.append(
                f"At least {minimum_total} policy-window daily observations are required."
            )
        if downside_count < minimum_downside:
            reasons.append(
                f"At least {minimum_downside} policy-window downside observations are required."
            )

        reasons = sorted(set(reasons))
        snapshot = {
            "policy_version": SORTINO_READINESS_POLICY_VERSION,
            "base_evidence_snapshot_sha256": base["evidence_snapshot_sha256"],
            "downside_policy_id": policy_id,
            "downside_policy_record_hash": policy.get("record_hash") if policy else None,
            "daily_return_ids": [item.get("result_id") for item in daily_returns],
            "daily_return_record_hashes": [item.get("record_hash") for item in daily_returns],
            "matched_risk_free_record_hashes": [
                risk_free_by_daily_id[item["result_id"]]["record_hash"]
                for item in daily_returns
                if item["result_id"] in risk_free_by_daily_id
            ],
        }
        ready = not reasons and bool(daily_returns)
        return {
            "policy_version": SORTINO_READINESS_POLICY_VERSION,
            "status": "EVIDENCE_READY" if ready else "BLOCKED",
            "simulation_only": True,
            "portfolio_version": version,
            "through_horizon": horizon,
            "downside_policy_id": policy_id,
            "target_basis": policy.get("target_basis") if policy else None,
            "reasons": reasons,
            "daily_return_observation_count": len(daily_returns),
            "downside_observation_count": downside_count,
            "minimum_total_observations": minimum_total,
            "minimum_downside_observations": minimum_downside,
            "evidence_snapshot_sha256": _snapshot_hash(snapshot),
            "sortino_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
        }
