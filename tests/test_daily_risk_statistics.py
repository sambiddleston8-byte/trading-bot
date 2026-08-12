from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import DailyRiskStatisticsLedger, PerformanceMetricReadinessGate


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


def evidence():
    milestone = {
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
                    "market_session_date": current.date().isoformat(),
                    "effective_at": current.isoformat(),
                    **IDENTITY,
                }
            )
        current += timedelta(days=1)
    returns = []
    for index, (previous, current_value) in enumerate(zip(values, values[1:]), start=1):
        daily_return = Fraction(-1, 10) if index == len(values) - 1 else Fraction(1, 100)
        returns.append(
            {
                "result_id": f"DRET-{index:03d}",
                "record_hash": f"dret-hash-{index:03d}",
                "portfolio_version": "PORT-001",
                "previous_valuation_id": previous["valuation_id"],
                "previous_valuation_record_hash": previous["record_hash"],
                "current_valuation_id": current_value["valuation_id"],
                "current_valuation_record_hash": current_value["record_hash"],
                "previous_market_session_date": previous["market_session_date"],
                "current_market_session_date": current_value["market_session_date"],
                "current_effective_at": current_value["effective_at"],
                "calculated_at": current_value["effective_at"],
                "daily_return_calculated": True,
                "exact_fractions": {"daily_portfolio_return": fraction(daily_return)},
                **IDENTITY,
            }
        )
    return milestone, values, returns


def ledger(tmp_path, *, truncate_returns=0):
    milestone, values, returns = evidence()
    if truncate_returns:
        returns = returns[:-truncate_returns]
    daily_values = Stub(values)
    daily_returns = Stub(returns)
    gate = PerformanceMetricReadinessGate(
        MilestoneStub([milestone]), Stub([]), daily_values, daily_returns
    )
    risk = DailyRiskStatisticsLedger(tmp_path / "risk.jsonl", gate)
    return risk, gate, returns


def calculate(risk, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "through_horizon": "12_MONTHS",
        "calculated_at": "2025-01-02T23:30:00+00:00",
    }
    values.update(overrides)
    return risk.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import daily_risk_statistics as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_calculates_sample_volatility_and_cash_flow_neutral_wealth_drawdown(tmp_path):
    risk, _, returns = ledger(tmp_path)
    result = calculate(risk)
    raw = [Fraction(1, 100)] * (len(returns) - 1) + [Fraction(-1, 10)]
    mean = sum(raw, Fraction(0)) / len(raw)
    variance = sum(((value - mean) ** 2 for value in raw), Fraction(0)) / (len(raw) - 1)
    assert result["scope"] == "SIMULATED_GROSS_PRE_TAX_DAILY_VOLATILITY_AND_DRAWDOWN"
    assert result["observation_count"] == len(returns)
    assert result["exact_fractions"]["daily_mean_return"] == fraction(mean)
    assert result["exact_fractions"]["sample_daily_variance"] == fraction(variance)
    assert Decimal(result["sample_daily_volatility"]) > 0
    assert Decimal(result["annualized_volatility"]) > Decimal(result["sample_daily_volatility"])
    assert result["exact_fractions"]["maximum_drawdown"] == fraction(Fraction(-1, 10))
    assert result["maximum_drawdown_recovered_by_end"] is False
    assert result["sharpe_calculated"] is False
    assert result["sortino_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert risk.verify() == [result]


def test_gate_blocks_incomplete_daily_return_chain(tmp_path):
    risk, _, _ = ledger(tmp_path, truncate_returns=1)
    result = calculate(risk)
    assert result["status"] == "NOT_CALCULABLE"
    assert result["volatility_calculated"] is False
    assert "pinned daily return" in " ".join(result["reasons"])
    assert risk.records() == []


def test_identical_concurrent_retries_create_one_result(tmp_path):
    risk, _, _ = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(risk), range(2)))
    assert first == second
    assert len(risk.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"sample_daily_volatility": "9"},
        {"annualized_volatility": "9"},
        {"maximum_drawdown": "0"},
        {"volatility_calculated": False},
        {"maximum_drawdown_calculated": False},
        {"sharpe_calculated": True},
        {"sortino_calculated": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"readiness_evidence_snapshot_sha256": "forged"},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    risk, _, _ = ledger(tmp_path)
    calculate(risk)
    rewrite_with_valid_hash(risk.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        risk.verify()


def test_supporting_daily_return_tampering_is_detected(tmp_path):
    risk, gate, _ = ledger(tmp_path)
    calculate(risk)
    gate.daily_return_ledger.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError):
        risk.verify()


def test_mixed_return_identity_fails_closed(tmp_path):
    risk, gate, _ = ledger(tmp_path)
    gate.daily_return_ledger.values[100]["git_revision"] = "different"
    result = calculate(risk)
    assert result["status"] == "NOT_CALCULABLE"
    assert "multiple strategy" in " ".join(result["reasons"])


def test_catastrophic_return_fails_closed(tmp_path):
    risk, gate, _ = ledger(tmp_path)
    gate.daily_return_ledger.values[-1]["exact_fractions"]["daily_portfolio_return"] = fraction(-1)
    result = calculate(risk)
    assert result["status"] == "NOT_CALCULABLE"
    assert "wealth growth" in " ".join(result["reasons"])


def test_drawdown_recovery_and_worst_episode_are_identified(tmp_path):
    risk, gate, _ = ledger(tmp_path)
    series = gate.daily_return_ledger.values
    for item in series:
        item["exact_fractions"]["daily_portfolio_return"] = fraction(0)
    series[-5]["exact_fractions"]["daily_portfolio_return"] = fraction(Fraction(-1, 20))
    series[-4]["exact_fractions"]["daily_portfolio_return"] = fraction(Fraction(1, 10))
    series[-3]["exact_fractions"]["daily_portfolio_return"] = fraction(Fraction(-1, 5))
    series[-2]["exact_fractions"]["daily_portfolio_return"] = fraction(Fraction(1, 4))
    result = calculate(risk)
    assert result["maximum_drawdown"] == "-0.2"
    assert result["maximum_drawdown_trough_date"] == series[-3]["current_market_session_date"]
    assert result["maximum_drawdown_recovered_by_end"] is True
