from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from fractions import Fraction
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import SortinoRatioLedger
from tests.test_sortino_readiness import assess, evidence, fraction


def ledger(tmp_path, **overrides):
    gate, policy, daily, risk_free = evidence(tmp_path, **overrides)
    for item in daily:
        item["calculated_at"] = item["current_effective_at"]
    return SortinoRatioLedger(tmp_path / "sortino.jsonl", gate), policy, daily, risk_free


def calculate(item, policy, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "through_horizon": "12_MONTHS",
        "downside_policy_id": policy["policy_id"],
        "calculated_at": "2025-01-03T00:00:00+00:00",
    }
    values.update(overrides)
    return item.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import sortino_ratio as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_calculates_zero_target_sortino_with_declared_total_count_denominator(tmp_path):
    item, policy, daily, _ = ledger(tmp_path)
    result = calculate(item, policy)
    returns = [
        Fraction(
            int(entry["exact_fractions"]["daily_portfolio_return"]["numerator"]),
            int(entry["exact_fractions"]["daily_portfolio_return"]["denominator"]),
        )
        for entry in daily
    ]
    mean = sum(returns, Fraction(0)) / len(returns)
    downside_sum = sum((min(Fraction(0), value) ** 2 for value in returns), Fraction(0))
    variance = downside_sum / len(returns)
    assert result["scope"] == "SIMULATED_GROSS_PRE_TAX_ANNUALIZED_SORTINO_RATIO"
    assert result["target_basis"] == "ZERO_DAILY_RETURN"
    assert result["observation_count"] == len(returns)
    assert result["downside_observation_count"] == 30
    assert result["exact_fractions"]["mean_target_relative_daily_return"] == fraction(mean)
    assert result["exact_fractions"]["downside_sum_of_squares"] == fraction(downside_sum)
    assert result["exact_fractions"]["downside_variance"] == fraction(variance)
    assert Decimal(result["downside_deviation"]) > 0
    assert result["sortino_calculated"] is True
    assert result["learning_eligible"] is False
    assert result["live_trading_enabled"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert item.verify() == [result]


def test_inadequate_downside_sample_fails_closed(tmp_path):
    item, policy, *_ = ledger(tmp_path, downside_count=29)
    result = calculate(item, policy)
    assert result["status"] == "NOT_CALCULABLE"
    assert result["sortino_calculated"] is False
    assert item.records() == []


def test_unknown_policy_fails_closed(tmp_path):
    item, policy, *_ = ledger(tmp_path)
    result = item.calculate(
        portfolio_version="PORT-001",
        through_horizon="12_MONTHS",
        downside_policy_id="UNKNOWN",
    )
    assert result["status"] == "NOT_CALCULABLE"
    assert item.records() == []


def test_calculation_time_guards_fail_closed(tmp_path):
    item, policy, *_ = ledger(tmp_path)
    before = calculate(item, policy, calculated_at="2024-01-01T00:00:00+00:00")
    future = calculate(item, policy, calculated_at="2099-01-01T00:00:00+00:00")
    assert before["status"] == "NOT_CALCULABLE"
    assert future["status"] == "NOT_CALCULABLE"


def test_identical_concurrent_retries_append_once(tmp_path):
    item, policy, *_ = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(item, policy), range(2)))
    assert first == second
    assert len(item.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"annualized_sortino_ratio": "9"},
        {"downside_deviation": "9"},
        {"downside_observation_count": 1},
        {"target_basis": "MATCHED_DAILY_SOFR"},
        {"sortino_calculated": False},
        {"annualized": False},
        {"risk_adjusted": False},
        {"sharpe_calculated": True},
        {"alpha_calculated": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    item, policy, *_ = ledger(tmp_path)
    calculate(item, policy)
    rewrite_with_valid_hash(item.path, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        item.verify()


def test_changed_support_is_detected(tmp_path):
    item, policy, daily, _ = ledger(tmp_path)
    calculate(item, policy)
    daily[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError):
        item.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    item, policy, *_ = ledger(tmp_path)
    result = calculate(item, policy)
    with item.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        item.verify()
    backup = item.repair_incomplete_tail()
    assert backup is not None and backup.read_bytes() == b'{"partial"'
    assert item.verify() == [result]
