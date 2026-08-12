from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import DailyPortfolioReturnLedger


IDENTITY = {
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "portfolio", "version": "1.0"}],
    "git_revision": "abc123",
}


def fraction(numerator, denominator=1):
    return {"numerator": str(numerator), "denominator": str(denominator)}


def valuation(identifier, session, effective, equity, flow_ids=(), flow_hashes=()):
    return {
        "valuation_id": identifier,
        "record_hash": f"hash-{identifier}",
        "portfolio_version": "PORT-001",
        "market_session_date": session,
        "effective_at": effective,
        "calculated_at": effective,
        "supporting_cash_flow_ids": list(flow_ids),
        "supporting_cash_flow_hashes": list(flow_hashes),
        "exact_fractions": {"total_equity": fraction(equity)},
        **IDENTITY,
    }


def flow(amount=100, **overrides):
    result = {
        "flow_id": "FLOW-1",
        "record_hash": "flow-hash",
        "portfolio_version": "PORT-001",
        "effective_at": "2025-01-03T21:00:00+00:00",
        "recorded_at": "2025-01-03T21:01:00+00:00",
        "exact_signed_amount": fraction(amount),
        **IDENTITY,
    }
    result.update(overrides)
    return result


class Stub:
    def __init__(self, values):
        self.values = list(values)

    def verify(self):
        return self.values


def ledgers(tmp_path, *, contribution=True):
    current_flows = [flow()] if contribution else []
    previous = valuation(
        "VAL-1", "2025-01-02", "2025-01-02T21:00:00+00:00", 1000
    )
    current = valuation(
        "VAL-2",
        "2025-01-03",
        "2025-01-03T21:00:00+00:00",
        1210 if contribution else 1100,
        ["FLOW-1"] if contribution else [],
        ["flow-hash"] if contribution else [],
    )
    valuations = Stub([previous, current])
    flows = Stub(current_flows)
    returns = DailyPortfolioReturnLedger(tmp_path / "returns.jsonl", valuations, flows)
    return valuations, flows, returns, current


def calculate(returns, current, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "current_valuation_id": current["valuation_id"],
        "calculated_at": "2025-01-03T21:02:00+00:00",
    }
    values.update(overrides)
    return returns.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import daily_portfolio_return as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_contribution_is_removed_before_measuring_daily_return(tmp_path):
    _, _, returns, current = ledgers(tmp_path)
    result = calculate(returns, current)
    assert result["scope"] == "SIMULATED_ONE_PERIOD_CASH_FLOW_NEUTRAL_DAILY_RETURN"
    assert result["previous_post_flow_equity"] == "1000"
    assert result["current_boundary_external_cash_flow"] == "100"
    assert result["current_pre_flow_equity"] == "1110"
    assert result["current_post_flow_equity"] == "1210"
    assert result["exact_fractions"]["daily_portfolio_return"] == fraction(11, 100)
    assert result["gross_pre_tax_basis"] is True
    assert result["broker_cash_reconciled"] is False
    assert result["annualized"] is False
    assert result["performance_metric_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert returns.verify() == [result]


def test_no_flow_daily_return_uses_consecutive_equity(tmp_path):
    _, _, returns, current = ledgers(tmp_path, contribution=False)
    result = calculate(returns, current)
    assert result["current_boundary_external_cash_flow"] == "0"
    assert result["exact_fractions"]["daily_portfolio_return"] == fraction(1, 10)


def test_first_valuation_has_no_return_predecessor(tmp_path):
    valuations, flows, returns, _ = ledgers(tmp_path)
    result = returns.calculate(portfolio_version="PORT-001", current_valuation_id="VAL-1")
    assert result["status"] == "NOT_CALCULABLE"
    assert "previous" in " ".join(result["reasons"]).lower()


def test_flow_support_mismatch_fails_closed(tmp_path):
    valuations, _, returns, current = ledgers(tmp_path)
    current["supporting_cash_flow_ids"] = []
    current["supporting_cash_flow_hashes"] = []
    result = calculate(returns, current)
    assert result["status"] == "NOT_CALCULABLE"
    assert "Boundary cash flows" in " ".join(result["reasons"])


def test_intraday_flow_is_not_mislabelled_as_close_boundary_flow(tmp_path):
    _, flows, returns, current = ledgers(tmp_path)
    flows.values[0]["effective_at"] = "2025-01-03T15:00:00+00:00"
    result = calculate(returns, current)
    assert result["status"] == "NOT_CALCULABLE"
    assert "exactly at the current valuation close" in " ".join(result["reasons"])


@pytest.mark.parametrize(
    "previous_session,current_session,expected_status",
    [
        ("2025-01-02", "2025-01-03", "CALCULATED"),
        ("2025-01-03", "2025-01-06", "CALCULATED"),
        ("2025-01-02", "2025-01-06", "NOT_CALCULABLE"),
        ("2025-01-17", "2025-01-21", "NOT_CALCULABLE"),
    ],
)
def test_only_adjacent_weekday_sessions_are_treated_as_daily(
    tmp_path, previous_session, current_session, expected_status
):
    previous = valuation(
        "VAL-1", previous_session, f"{previous_session}T21:00:00+00:00", 1000
    )
    current = valuation(
        "VAL-2", current_session, f"{current_session}T21:00:00+00:00", 1100
    )
    returns = DailyPortfolioReturnLedger(
        tmp_path / "sessions.jsonl", Stub([previous, current]), Stub([])
    )
    result = calculate(
        returns,
        current,
        calculated_at=f"{current_session}T21:02:00+00:00",
    )
    assert result["status"] == expected_status
    if expected_status == "CALCULATED":
        assert result["regular_session_interval_verified"] is True
        assert result["exchange_holiday_calendar_evidence_used"] is False
    else:
        assert "calendar evidence" in " ".join(result["reasons"])


def test_identity_mismatch_fails_closed(tmp_path):
    valuations, _, returns, current = ledgers(tmp_path)
    current["git_revision"] = "different"
    result = calculate(returns, current)
    assert result["status"] == "NOT_CALCULABLE"
    assert "strategy, model and Git identity" in " ".join(result["reasons"])


def test_identical_concurrent_retries_create_one_record(tmp_path):
    _, _, returns, current = ledgers(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(returns, current), range(2)))
    assert first == second
    assert len(returns.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"daily_portfolio_return": "9"},
        {"daily_return_calculated": False},
        {"regular_session_interval_verified": False},
        {"exchange_holiday_calendar_evidence_used": True},
        {"annualized": True},
        {"risk_adjusted": True},
        {"performance_metric_calculated": True},
        {"recommendation_provided": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"current_valuation_record_hash": "forged"},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    _, _, returns, current = ledgers(tmp_path)
    calculate(returns, current)
    rewrite_with_valid_hash(returns.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        returns.verify()


def test_later_cash_flow_does_not_invalidate_pinned_history(tmp_path):
    _, flows, returns, current = ledgers(tmp_path, contribution=False)
    result = calculate(returns, current)
    flows.values.append(
        flow(
            flow_id="FLOW-LATER",
            record_hash="later-hash",
            effective_at="2025-01-06T21:00:00+00:00",
            recorded_at="2025-01-06T21:01:00+00:00",
        )
    )
    assert returns.verify() == [result]


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    _, _, returns, current = ledgers(tmp_path)
    result = calculate(returns, current)
    with returns.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        returns.verify()
    backup = returns.repair_incomplete_tail()
    assert backup is not None
    assert returns.verify() == [result]
