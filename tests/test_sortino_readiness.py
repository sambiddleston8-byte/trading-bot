from datetime import datetime, timedelta, timezone
from fractions import Fraction

import pytest

from core.performance import PerformanceMetricReadinessGate, SortinoMetricReadinessGate


IDENTITY = {
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "portfolio", "version": "1.0"}],
    "git_revision": "abc123",
}


def fraction(value):
    value = Fraction(value)
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


class Stub:
    def __init__(self, values=()):
        self.values = list(values)

    def verify(self):
        return self.values


class FundingStub:
    def funding_for(self, version):
        if version != "PORT-001":
            return None
        return {
            "funding_id": "FUND-1",
            "record_hash": "funding-hash",
            "effective_at": "2024-01-01T21:00:00+00:00",
        }


class MilestoneStub(Stub):
    funding_ledger = FundingStub()


def evidence(tmp_path, *, target_basis="ZERO_DAILY_RETURN", downside_count=30):
    target = {
        "valuation_id": "PVAL-12M",
        "record_hash": "milestone-hash",
        "portfolio_version": "PORT-001",
        "horizon": "12_MONTHS",
        "outcome_asset_price_effective_at": "2025-01-02T23:00:00+00:00",
        **IDENTITY,
    }
    values = []
    current = datetime(2024, 1, 2, 21, tzinfo=timezone.utc)
    end = datetime(2025, 1, 2, 21, tzinfo=timezone.utc)
    while current <= end:
        if current.weekday() < 5:
            index = len(values) + 1
            values.append(
                {
                    "valuation_id": f"DVAL-{index:03d}",
                    "record_hash": f"dval-hash-{index:03d}",
                    "portfolio_version": "PORT-001",
                    "effective_at": current.isoformat(),
                    **IDENTITY,
                }
            )
        current += timedelta(days=1)
    daily_returns = []
    risk_free_returns = []
    for index, (previous, current_value) in enumerate(zip(values, values[1:]), start=1):
        portfolio_return = Fraction(-1, 1000) if index <= downside_count else Fraction(1, 1000)
        daily = {
            "result_id": f"DRET-{index:03d}",
            "record_hash": f"dret-hash-{index:03d}",
            "portfolio_version": "PORT-001",
            "previous_valuation_id": previous["valuation_id"],
            "previous_valuation_record_hash": previous["record_hash"],
            "current_valuation_id": current_value["valuation_id"],
            "current_valuation_record_hash": current_value["record_hash"],
            "previous_market_session_date": previous["effective_at"][:10],
            "current_market_session_date": current_value["effective_at"][:10],
            "current_effective_at": current_value["effective_at"],
            "daily_return_calculated": True,
            "exact_fractions": {"daily_portfolio_return": fraction(portfolio_return)},
            **IDENTITY,
        }
        daily_returns.append(daily)
        risk_free_returns.append(
            {
                "result_id": f"DRF-{index:03d}",
                "record_hash": f"drf-hash-{index:03d}",
                "portfolio_version": "PORT-001",
                "daily_portfolio_return_id": daily["result_id"],
                "daily_portfolio_return_record_hash": daily["record_hash"],
                "previous_market_session_date": daily["previous_market_session_date"],
                "current_market_session_date": daily["current_market_session_date"],
                "calculated_at": current_value["effective_at"],
                "daily_risk_free_return_calculated": True,
                "exact_fractions": {"daily_risk_free_return": fraction(Fraction(1, 10000))},
                **IDENTITY,
            }
        )
    from core.performance import DownsideTargetPolicyLedger

    policy_ledger = DownsideTargetPolicyLedger(tmp_path / "downside-policy.jsonl")
    policy = policy_ledger.preregister(
        portfolio_version="PORT-001",
        target_basis=target_basis,
        evaluation_not_before="2024-01-02T00:00:00+00:00",
        recorded_at="2024-01-01T00:00:00+00:00",
        decided_by="Sam Biddleston",
        decision_reference="user-approved-zero-sortino-target-2026-08-13",
        human_decision_confirmed=True,
        strategy_version=IDENTITY["strategy_version"],
        model_versions=IDENTITY["model_versions"],
        git_revision=IDENTITY["git_revision"],
    )
    base = PerformanceMetricReadinessGate(
        MilestoneStub([target]), Stub([]), Stub(values), Stub(daily_returns)
    )
    gate = SortinoMetricReadinessGate(base, policy_ledger, Stub(risk_free_returns))
    return gate, policy, daily_returns, risk_free_returns


def assess(gate, policy):
    return gate.assess(
        portfolio_version="PORT-001",
        through_horizon="12_MONTHS",
        downside_policy_id=policy["policy_id"],
    )


def test_zero_target_with_complete_future_sample_is_ready(tmp_path):
    gate, policy, *_ = evidence(tmp_path)
    result = assess(gate, policy)
    assert result["status"] == "EVIDENCE_READY"
    assert result["target_basis"] == "ZERO_DAILY_RETURN"
    assert result["daily_return_observation_count"] >= 252
    assert result["downside_observation_count"] == 30
    assert result["sortino_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["live_trading_enabled"] is False


def test_too_few_downside_observations_blocks(tmp_path):
    gate, policy, *_ = evidence(tmp_path, downside_count=29)
    result = assess(gate, policy)
    assert result["status"] == "BLOCKED"
    assert "At least 30" in " ".join(result["reasons"])


@pytest.mark.parametrize("field", ["strategy_version", "git_revision"])
def test_policy_identity_mismatch_blocks(tmp_path, field):
    gate, policy, daily, _ = evidence(tmp_path)
    daily[0][field] = "changed-after-preregistration"
    result = assess(gate, policy)
    assert result["status"] == "BLOCKED"
    assert "preregistered strategy" in " ".join(result["reasons"])


def test_unknown_policy_blocks_without_using_an_implicit_target(tmp_path):
    gate, policy, *_ = evidence(tmp_path)
    result = gate.assess(
        portfolio_version="PORT-001",
        through_horizon="12_MONTHS",
        downside_policy_id="UNKNOWN",
    )
    assert result["status"] == "BLOCKED"
    assert result["target_basis"] is None


def test_matched_sofr_target_requires_pinned_support(tmp_path):
    gate, policy, _, risk_free = evidence(tmp_path, target_basis="MATCHED_DAILY_SOFR")
    risk_free[0]["daily_portfolio_return_record_hash"] = "wrong"
    result = assess(gate, policy)
    assert result["status"] == "BLOCKED"
    assert "same daily return" in " ".join(result["reasons"])


def test_matched_sofr_target_requires_exact_dates_and_identity(tmp_path):
    gate, policy, _, risk_free = evidence(tmp_path, target_basis="MATCHED_DAILY_SOFR")
    risk_free[0]["current_market_session_date"] = "1900-01-01"
    result = assess(gate, policy)
    assert result["status"] == "BLOCKED"
    assert "same daily return" in " ".join(result["reasons"])


def test_readiness_snapshot_changes_with_evidence(tmp_path):
    gate, policy, daily, _ = evidence(tmp_path)
    first = assess(gate, policy)
    daily[0]["record_hash"] = "new-authentic-hash"
    second = assess(gate, policy)
    assert first["evidence_snapshot_sha256"] != second["evidence_snapshot_sha256"]
