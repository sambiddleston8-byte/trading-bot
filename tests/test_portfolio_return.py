from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import PortfolioCashFlowLedger, TimeWeightedPortfolioReturnLedger


IDENTITY = {
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "portfolio", "version": "1.0"}],
    "git_revision": "abc123",
}


def fraction(numerator, denominator=1):
    return {"numerator": str(numerator), "denominator": str(denominator)}


class FundingLedgerStub:
    def __init__(self):
        self.record = {
            "funding_id": "PFUND-001",
            "record_hash": "funding-hash",
            "portfolio_version": "PORT-001",
            "effective_at": "2025-01-02T14:58:00+00:00",
            "recorded_at": "2025-01-02T14:59:00+00:00",
            "amount": "1000",
            "exact_amount": fraction(1000),
            **IDENTITY,
        }

    def funding_for(self, portfolio_version):
        return self.record if portfolio_version == "PORT-001" else None


class ValuationLedgerStub:
    def __init__(self):
        self.funding_ledger = FundingLedgerStub()
        self.values = [
            {
                "valuation_id": "PVAL-1-WEEK",
                "record_hash": "valuation-one-hash",
                "portfolio_version": "PORT-001",
                "horizon": "1_WEEK",
                "horizon_label": "1 week",
                "outcome_asset_price_effective_at": "2025-01-09T16:00:00+00:00",
                "calculated_at": "2025-01-09T17:01:00+00:00",
                "remaining_cash": "600",
                "total_equity": "1100",
                "exact_fractions": {
                    "remaining_cash": fraction(600),
                    "total_equity": fraction(1100),
                },
                **IDENTITY,
            },
            {
                "valuation_id": "PVAL-1-MONTH",
                "record_hash": "valuation-two-hash",
                "portfolio_version": "PORT-001",
                "horizon": "1_MONTH",
                "horizon_label": "1 month",
                "outcome_asset_price_effective_at": "2025-02-03T16:00:00+00:00",
                "calculated_at": "2025-02-03T17:01:00+00:00",
                "remaining_cash": "600",
                "total_equity": "1210",
                "exact_fractions": {
                    "remaining_cash": fraction(600),
                    "total_equity": fraction(1210),
                },
                **IDENTITY,
            },
        ]

    def verify(self):
        return self.values


def ledgers(tmp_path):
    valuations = ValuationLedgerStub()
    flows = PortfolioCashFlowLedger(tmp_path / "cash_flows.jsonl", valuations)
    returns = TimeWeightedPortfolioReturnLedger(
        tmp_path / "portfolio_returns.jsonl", valuations, flows
    )
    return valuations, flows, returns


def record_contribution(flows, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "horizon": "1_WEEK",
        "flow_type": "CONTRIBUTION",
        "amount": "100",
        "recorded_at": "2025-01-09T17:02:00+00:00",
    }
    values.update(overrides)
    return flows.record(**values)


def calculate(returns, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "through_horizon": "1_MONTH",
        "calculated_at": "2025-02-03T17:02:00+00:00",
    }
    values.update(overrides)
    return returns.calculate(**values)


def rewrite_with_valid_hash(path, module, **changes):
    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_records_exact_contribution_after_verified_valuation(tmp_path):
    _, flows, _ = ledgers(tmp_path)
    result = record_contribution(flows)
    assert result["record_type"] == "SIMULATED_EXTERNAL_PORTFOLIO_CASH_FLOW"
    assert result["flow_type"] == "CONTRIBUTION"
    assert result["amount"] == "100"
    assert result["signed_amount"] == "100"
    assert result["cash_after_flow"] == "700"
    assert result["effective_at"] == "2025-01-09T16:00:00+00:00"
    assert result["timing_policy"] == "AFTER_MARKET_VALUATION_END_OF_SUBPERIOD"
    assert result["portfolio_return_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert flows.verify() == [result]


def test_withdrawal_cannot_exceed_available_cash(tmp_path):
    _, flows, _ = ledgers(tmp_path)
    with pytest.raises(ValueError, match="exceeds simulated cash"):
        record_contribution(flows, flow_type="WITHDRAWAL", amount="601")


def test_midperiod_or_missing_boundary_is_not_representable(tmp_path):
    _, flows, _ = ledgers(tmp_path)
    with pytest.raises(ValueError, match="verified portfolio valuation"):
        record_contribution(flows, horizon="3_MONTHS")


def test_cash_flow_boundaries_must_be_strictly_increasing(tmp_path):
    _, flows, _ = ledgers(tmp_path)
    flows.record(
        portfolio_version="PORT-001",
        horizon="1_MONTH",
        flow_type="CONTRIBUTION",
        amount=100,
        recorded_at="2025-02-03T17:02:00+00:00",
    )
    with pytest.raises(LedgerIntegrityError, match="strictly increasing"):
        record_contribution(flows)


def test_time_weighted_return_neutralizes_boundary_contribution(tmp_path):
    _, flows, returns = ledgers(tmp_path)
    contribution = record_contribution(flows)
    result = calculate(returns)

    assert result["scope"] == (
        "SIMULATED_CASH_FLOW_NEUTRAL_TIME_WEIGHTED_PORTFOLIO_RETURN"
    )
    assert result["subperiod_count"] == 2
    assert result["cumulative_external_cash_flow"] == "100"
    assert result["supporting_cash_flow_ids"] == [contribution["flow_id"]]
    first, second = result["subperiods"]
    assert first["exact_fractions"]["subperiod_return"] == fraction(1, 10)
    assert first["pre_flow_equity"] == "1100"
    assert first["post_flow_equity"] == "1200"
    assert second["base_portfolio_total_equity"] == "1210"
    assert second["cumulative_prior_external_cash_flow"] == "100"
    assert second["pre_flow_equity"] == "1310"
    assert second["exact_fractions"]["subperiod_return"] == fraction(11, 120)
    assert result["exact_fractions"]["time_weighted_portfolio_return"] == fraction(
        241, 1200
    )
    assert result["time_weighted_portfolio_return"] == (
        "0.2008333333333333333333333333"
    )
    assert result["portfolio_return_calculated"] is True
    assert result["relative_portfolio_return_calculated"] is False
    assert result["annualized"] is False
    assert result["alpha_calculated"] is False
    assert result["risk_adjusted"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert returns.verify() == [result]


def test_end_boundary_withdrawal_does_not_change_return_through_that_boundary(tmp_path):
    _, flows, returns = ledgers(tmp_path)
    flows.record(
        portfolio_version="PORT-001",
        horizon="1_MONTH",
        flow_type="WITHDRAWAL",
        amount=50,
        recorded_at="2025-02-03T17:02:00+00:00",
    )
    result = calculate(returns, calculated_at="2025-02-03T17:03:00+00:00")
    assert result["time_weighted_portfolio_return"] == "0.21"
    assert result["ending_pre_flow_equity"] == "1210"
    assert result["ending_post_flow_equity"] == "1160"


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"through_horizon": "ENTRY"}, "baseline"),
        ({"through_horizon": "3_MONTHS"}, "valuation is missing"),
        (
            {"calculated_at": "2025-02-03T17:00:59+00:00"},
            "predate supporting",
        ),
        ({"calculated_at": "2999-01-01T00:00:00+00:00"}, "future"),
    ],
)
def test_invalid_return_request_fails_closed(tmp_path, overrides, fragment):
    _, _, returns = ledgers(tmp_path)
    result = calculate(returns, **overrides)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert returns.records() == []


def test_identical_concurrent_cash_flow_retries_create_one_record(tmp_path):
    _, flows, _ = ledgers(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(
            executor.map(lambda _: record_contribution(flows), range(2))
        )
    assert first == second
    assert len(flows.verify()) == 1


def test_identical_concurrent_return_retries_create_one_record(tmp_path):
    _, _, returns = ledgers(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(returns), range(2)))
    assert first == second
    assert len(returns.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"portfolio_return_calculated": True},
        {"timing_policy": "MIDPERIOD"},
        {"cash_after_flow": "999"},
        {"valuation_record_hash": "forged"},
        {"exact_amount": {"numerator": "200", "denominator": "2"}},
    ],
)
def test_rehashed_cash_flow_tampering_is_detected(tmp_path, changes):
    from core.performance import portfolio_cash_flow as module

    _, flows, _ = ledgers(tmp_path)
    record_contribution(flows)
    rewrite_with_valid_hash(flows.path, module, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        flows.verify()


@pytest.mark.parametrize(
    "changes",
    [
        {"alpha_calculated": True},
        {"annualized": True},
        {"learning_eligible": True},
        {"time_weighted_portfolio_return": "999"},
        {"funding_record_hash": "forged"},
    ],
)
def test_rehashed_portfolio_return_tampering_is_detected(tmp_path, changes):
    from core.performance import portfolio_return as module

    _, _, returns = ledgers(tmp_path)
    calculate(returns)
    rewrite_with_valid_hash(returns.path, module, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        returns.verify()


def test_incomplete_return_tail_requires_explicit_repair(tmp_path):
    _, _, returns = ledgers(tmp_path)
    result = calculate(returns)
    with returns.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        returns.verify()
    backup = returns.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert returns.verify() == [result]
