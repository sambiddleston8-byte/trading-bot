from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import (
    PerformanceMetricReadinessGate,
    SharpeMetricReadinessGate,
    SharpeRatioLedger,
)


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


def evidence(*, constant_excess=False, truncate_risk_free=0, negative=False):
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
        risk_free_value = Fraction(1, 10_000)
        if constant_excess:
            portfolio_value = Fraction(11, 10_000)
        elif negative:
            portfolio_value = Fraction(-20 if index % 2 else 0, 10_000)
        else:
            portfolio_value = Fraction(20 if index % 2 else 0, 10_000)
        previous_date = previous["effective_at"][:10]
        current_date = current_value["effective_at"][:10]
        daily = {
            "result_id": f"DRET-{index:03d}",
            "record_hash": f"dret-hash-{index:03d}",
            "portfolio_version": "PORT-001",
            "previous_valuation_id": previous["valuation_id"],
            "previous_valuation_record_hash": previous["record_hash"],
            "current_valuation_id": current_value["valuation_id"],
            "current_valuation_record_hash": current_value["record_hash"],
            "previous_market_session_date": previous_date,
            "current_market_session_date": current_date,
            "current_effective_at": current_value["effective_at"],
            "calculated_at": current_value["effective_at"],
            "daily_return_calculated": True,
            "exact_fractions": {"daily_portfolio_return": fraction(portfolio_value)},
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
                "previous_market_session_date": previous_date,
                "current_market_session_date": current_date,
                "calculated_at": current_value["effective_at"],
                "daily_risk_free_return_calculated": True,
                "source_backfilled": False,
                "exact_fractions": {
                    "daily_risk_free_return": fraction(risk_free_value)
                },
                **IDENTITY,
            }
        )
    if truncate_risk_free:
        risk_free_returns = risk_free_returns[:-truncate_risk_free]
    daily_ledger = Stub(daily_returns)
    risk_free_ledger = Stub(risk_free_returns)
    base = PerformanceMetricReadinessGate(
        MilestoneStub([target]), Stub([]), Stub(values), daily_ledger
    )
    readiness = SharpeMetricReadinessGate(base, risk_free_ledger)
    return readiness, daily_ledger, risk_free_ledger, daily_returns, risk_free_returns


def ledger(tmp_path, **evidence_overrides):
    readiness, daily_ledger, risk_free_ledger, daily, risk_free = evidence(
        **evidence_overrides
    )
    return (
        SharpeRatioLedger(tmp_path / "sharpe.jsonl", readiness),
        daily_ledger,
        risk_free_ledger,
        daily,
        risk_free,
    )


def calculate(item, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "through_horizon": "12_MONTHS",
        "calculated_at": "2025-01-03T00:00:00+00:00",
    }
    values.update(overrides)
    return item.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import sharpe_ratio as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_calculates_exact_excess_statistics_and_annualized_sharpe(tmp_path):
    item, _, _, daily, risk_free = ledger(tmp_path)
    result = calculate(item)
    excess = [
        Fraction(int(entry["exact_fractions"]["daily_portfolio_return"]["numerator"]),
                 int(entry["exact_fractions"]["daily_portfolio_return"]["denominator"]))
        - Fraction(int(risk["exact_fractions"]["daily_risk_free_return"]["numerator"]),
                   int(risk["exact_fractions"]["daily_risk_free_return"]["denominator"]))
        for entry, risk in zip(daily, risk_free)
    ]
    mean = sum(excess, Fraction(0)) / len(excess)
    variance = sum(((value - mean) ** 2 for value in excess), Fraction(0)) / (
        len(excess) - 1
    )
    assert result["scope"] == "SIMULATED_GROSS_PRE_TAX_ANNUALIZED_SHARPE_RATIO"
    assert result["observation_count"] == len(excess)
    assert result["exact_fractions"]["sample_mean_daily_excess_return"] == fraction(mean)
    assert result["exact_fractions"]["sample_daily_excess_variance"] == fraction(variance)
    assert Decimal(result["sample_daily_excess_volatility"]) > 0
    assert Decimal(result["annualized_sharpe_ratio"]) > 0
    assert result["sharpe_calculated"] is True
    assert result["annualized"] is True
    assert result["risk_adjusted"] is True
    assert result["sortino_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["live_trading_enabled"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert item.verify() == [result]


def test_negative_mean_excess_produces_negative_sharpe(tmp_path):
    item, *_ = ledger(tmp_path, negative=True)
    result = calculate(item)
    assert Decimal(result["annualized_sharpe_ratio"]) < 0


def test_zero_excess_variance_fails_closed(tmp_path):
    item, *_ = ledger(tmp_path, constant_excess=True)
    result = calculate(item)
    assert result["status"] == "NOT_CALCULABLE"
    assert result["sharpe_calculated"] is False
    assert "variance is zero" in " ".join(result["reasons"])
    assert item.records() == []


def test_incomplete_pairing_fails_closed(tmp_path):
    item, *_ = ledger(tmp_path, truncate_risk_free=1)
    result = calculate(item)
    assert result["status"] == "NOT_CALCULABLE"
    assert "readiness" in " ".join(result["reasons"])


def test_calculation_time_guards_fail_closed(tmp_path):
    item, *_ = ledger(tmp_path)
    before = calculate(item, calculated_at="2024-01-01T00:00:00+00:00")
    future = calculate(item, calculated_at="2099-01-01T00:00:00+00:00")
    assert before["status"] == "NOT_CALCULABLE"
    assert future["status"] == "NOT_CALCULABLE"


def test_identical_concurrent_retries_append_once(tmp_path):
    item, *_ = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(item), range(2)))
    assert first == second
    assert len(item.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"annualized_sharpe_ratio": "9"},
        {"sample_daily_excess_volatility": "9"},
        {"observation_count": 1},
        {"source_backfilled": True},
        {"sharpe_calculated": False},
        {"annualized": False},
        {"risk_adjusted": False},
        {"sortino_calculated": True},
        {"alpha_calculated": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    item, *_ = ledger(tmp_path)
    calculate(item)
    rewrite_with_valid_hash(item.path, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        item.verify()


def test_changed_support_is_detected(tmp_path):
    item, daily_ledger, risk_free_ledger, *_ = ledger(tmp_path)
    calculate(item)
    daily_ledger.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError):
        item.verify()

    item, daily_ledger, risk_free_ledger, *_ = ledger(tmp_path / "risk-free")
    calculate(item)
    risk_free_ledger.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError):
        item.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    item, *_ = ledger(tmp_path)
    result = calculate(item)
    with item.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        item.verify()
    backup = item.repair_incomplete_tail()
    assert backup is not None and backup.read_bytes() == b'{"partial"'
    assert item.verify() == [result]
