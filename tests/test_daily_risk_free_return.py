from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import DailyRiskFreeReturnLedger


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


def daily_return():
    return {
        "result_id": "DPRET-1",
        "record_hash": "daily-return-hash",
        "portfolio_version": "PORT-001",
        "previous_market_session_date": "2025-02-07",
        "current_market_session_date": "2025-02-10",
        "calculated_at": "2025-02-10T21:01:00+00:00",
        "daily_return_calculated": True,
        "daily_portfolio_return": "0.01",
        "exact_fractions": {"daily_portfolio_return": fraction(Fraction(1, 100))},
        **IDENTITY,
    }


def observation(value_date, index_value, suffix, *, backfilled=False):
    return {
        "observation_id": f"RFOBS-{suffix}",
        "record_hash": f"risk-free-hash-{suffix}",
        "value_date": value_date,
        "index_value": str(index_value),
        "exact_index_value": fraction(index_value),
        "recorded_at": "2025-02-11T20:01:00+00:00",
        "availability": (
            "BACKFILLED_FINAL" if backfilled else "CONTEMPORANEOUS_FINAL_SAME_DAY"
        ),
    }


def evidence(*, missing_previous=False, missing_current=False, backfilled=False):
    returns = Stub([daily_return()])
    observations = []
    if not missing_previous:
        observations.append(observation("2025-02-07", Fraction(5, 4), "PREV"))
    if not missing_current:
        observations.append(
            observation("2025-02-10", Fraction(251, 200), "CURR", backfilled=backfilled)
        )
    return returns, Stub(observations)


def ledger(tmp_path, **evidence_overrides):
    returns, observations = evidence(**evidence_overrides)
    return (
        DailyRiskFreeReturnLedger(
            tmp_path / "daily_risk_free.jsonl", returns, observations
        ),
        returns,
        observations,
    )


def calculate(item, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "daily_portfolio_return_id": "DPRET-1",
        "calculated_at": "2025-02-11T20:02:00+00:00",
    }
    values.update(overrides)
    return item.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import daily_risk_free_return as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_matches_exact_sofr_index_ratio_to_portfolio_period(tmp_path):
    item, _, _ = ledger(tmp_path)
    result = calculate(item)
    assert result["scope"] == "SIMULATED_PORTFOLIO_PERIOD_MATCHED_SOFR_INDEX_RETURN"
    assert result["previous_market_session_date"] == "2025-02-07"
    assert result["current_market_session_date"] == "2025-02-10"
    assert result["elapsed_calendar_days"] == 3
    assert result["exact_fractions"]["risk_free_growth"] == fraction(
        Fraction(251, 250)
    )
    assert result["exact_fractions"]["daily_risk_free_return"] == fraction(
        Fraction(1, 250)
    )
    assert result["daily_risk_free_return"] == "0.004"
    assert result["source_backfilled"] is False
    assert result["daily_risk_free_return_calculated"] is True
    assert result["excess_return_calculated"] is False
    assert result["sharpe_calculated"] is False
    assert result["sortino_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["live_trading_enabled"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert item.verify() == [result]


@pytest.mark.parametrize(
    "evidence_overrides,fragment",
    [
        ({"missing_previous": True}, "previous period date"),
        ({"missing_current": True}, "current period date"),
    ],
)
def test_missing_endpoint_evidence_fails_closed(tmp_path, evidence_overrides, fragment):
    item, _, _ = ledger(tmp_path, **evidence_overrides)
    result = calculate(item)
    assert result["status"] == "NOT_CALCULABLE"
    assert result["daily_risk_free_return_calculated"] is False
    assert fragment in " ".join(result["reasons"])
    assert item.records() == []


def test_wrong_portfolio_or_return_id_fails_closed(tmp_path):
    item, _, _ = ledger(tmp_path)
    wrong_portfolio = calculate(item, portfolio_version="OTHER")
    wrong_id = calculate(item, daily_portfolio_return_id="UNKNOWN")
    assert wrong_portfolio["status"] == "NOT_CALCULABLE"
    assert wrong_id["status"] == "NOT_CALCULABLE"


def test_backfilled_endpoint_is_disclosed(tmp_path):
    item, _, _ = ledger(tmp_path, backfilled=True)
    result = calculate(item)
    assert result["source_backfilled"] is True


def test_calculation_time_guards_fail_closed(tmp_path):
    item, _, _ = ledger(tmp_path)
    before = calculate(item, calculated_at="2025-02-10T20:00:00+00:00")
    future = calculate(item, calculated_at="2099-01-01T00:00:00+00:00")
    assert before["status"] == "NOT_CALCULABLE"
    assert "predate" in " ".join(before["reasons"])
    assert future["status"] == "NOT_CALCULABLE"
    assert "future" in " ".join(future["reasons"])


def test_identical_concurrent_retries_append_once(tmp_path):
    item, _, _ = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(item), range(2)))
    assert first == second
    assert len(item.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"daily_risk_free_return": "9"},
        {"elapsed_calendar_days": 1},
        {"source_backfilled": True},
        {"daily_risk_free_return_calculated": False},
        {"excess_return_calculated": True},
        {"sharpe_calculated": True},
        {"sortino_calculated": True},
        {"annualized": True},
        {"risk_adjusted_metric_calculated": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    item, _, _ = ledger(tmp_path)
    calculate(item)
    rewrite_with_valid_hash(item.path, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        item.verify()


def test_changed_daily_return_or_index_support_is_detected(tmp_path):
    item, returns, observations = ledger(tmp_path)
    calculate(item)
    returns.values[0]["record_hash"] = "changed-return"
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        item.verify()

    item, returns, observations = ledger(tmp_path / "index")
    calculate(item)
    observations.values[0]["record_hash"] = "changed-index"
    with pytest.raises(LedgerIntegrityError, match="lost supporting evidence"):
        item.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    item, _, _ = ledger(tmp_path)
    result = calculate(item)
    with item.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        item.verify()
    backup = item.repair_incomplete_tail()
    assert backup is not None and backup.read_bytes() == b'{"partial"'
    assert item.verify() == [result]
