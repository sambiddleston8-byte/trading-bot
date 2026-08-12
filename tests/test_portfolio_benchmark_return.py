from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import (
    PortfolioCashFlowLedger,
    TimeWeightedPortfolioBenchmarkReturnLedger,
)


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
            "funded_at": "2025-01-02T14:59:00+00:00",
            "amount": "1000",
            "exact_amount": fraction(1000),
            **IDENTITY,
        }

    def funding_for(self, portfolio_version):
        return self.record if portfolio_version == "PORT-001" else None


class AssetValuationLedgerStub:
    def __init__(self):
        self.funding_ledger = FundingLedgerStub()
        self.values = [
            {
                "valuation_id": "PVAL-1-WEEK",
                "record_hash": "asset-valuation-one-hash",
                "portfolio_version": "PORT-001",
                "horizon": "1_WEEK",
                "horizon_label": "1 week",
                "outcome_asset_price_effective_at": "2025-01-09T16:00:00+00:00",
                "outcome_benchmark_price_effective_at": "2025-01-09T16:00:00+00:00",
                "calculated_at": "2025-01-09T17:01:00+00:00",
                "remaining_cash": "600",
                "exact_fractions": {
                    "remaining_cash": fraction(600),
                    "total_equity": fraction(1100),
                },
                **IDENTITY,
            },
            {
                "valuation_id": "PVAL-1-MONTH",
                "record_hash": "asset-valuation-two-hash",
                "portfolio_version": "PORT-001",
                "horizon": "1_MONTH",
                "horizon_label": "1 month",
                "outcome_asset_price_effective_at": "2025-02-03T16:00:00+00:00",
                "outcome_benchmark_price_effective_at": "2025-02-03T16:00:00+00:00",
                "calculated_at": "2025-02-03T17:01:00+00:00",
                "remaining_cash": "600",
                "exact_fractions": {
                    "remaining_cash": fraction(600),
                    "total_equity": fraction(1210),
                },
                **IDENTITY,
            },
        ]

    def verify(self):
        return self.values


class BenchmarkValuationLedgerStub:
    def __init__(self, assets):
        self.asset_valuation_ledger = assets
        self.funding_ledger = assets.funding_ledger
        self.values = [
            {
                "valuation_id": "PBVAL-1-WEEK",
                "record_hash": "benchmark-valuation-one-hash",
                "portfolio_version": "PORT-001",
                "horizon": "1_WEEK",
                "horizon_label": "1 week",
                "outcome_asset_price_effective_at": "2025-01-09T16:00:00+00:00",
                "outcome_benchmark_price_effective_at": "2025-01-09T16:00:00+00:00",
                "asset_portfolio_valuation_id": "PVAL-1-WEEK",
                "asset_portfolio_valuation_hash": "asset-valuation-one-hash",
                "calculated_at": "2025-01-09T17:02:00+00:00",
                "benchmark_family": "S&P 500",
                "benchmark_ticker": "^GSPC",
                "exact_fractions": {"benchmark_total_equity": fraction(1050)},
                **IDENTITY,
            },
            {
                "valuation_id": "PBVAL-1-MONTH",
                "record_hash": "benchmark-valuation-two-hash",
                "portfolio_version": "PORT-001",
                "horizon": "1_MONTH",
                "horizon_label": "1 month",
                "outcome_asset_price_effective_at": "2025-02-03T16:00:00+00:00",
                "outcome_benchmark_price_effective_at": "2025-02-03T16:00:00+00:00",
                "asset_portfolio_valuation_id": "PVAL-1-MONTH",
                "asset_portfolio_valuation_hash": "asset-valuation-two-hash",
                "calculated_at": "2025-02-03T17:02:00+00:00",
                "benchmark_family": "S&P 500",
                "benchmark_ticker": "^GSPC",
                "exact_fractions": {"benchmark_total_equity": fraction(1120)},
                **IDENTITY,
            },
        ]

    def verify(self):
        return self.values


def ledgers(tmp_path):
    assets = AssetValuationLedgerStub()
    benchmarks = BenchmarkValuationLedgerStub(assets)
    flows = PortfolioCashFlowLedger(tmp_path / "cash_flows.jsonl", assets)
    returns = TimeWeightedPortfolioBenchmarkReturnLedger(
        tmp_path / "portfolio_benchmark_returns.jsonl", benchmarks, flows
    )
    return assets, benchmarks, flows, returns


def record_contribution(flows, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "horizon": "1_WEEK",
        "flow_type": "CONTRIBUTION",
        "amount": "100",
        "recorded_at": "2025-01-09T17:03:00+00:00",
    }
    values.update(overrides)
    return flows.record(**values)


def calculate(returns, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "through_horizon": "1_MONTH",
        "calculated_at": "2025-02-03T17:03:00+00:00",
    }
    values.update(overrides)
    return returns.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import portfolio_benchmark_return as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_links_matched_benchmark_return_and_neutralizes_contribution(tmp_path):
    _, _, flows, returns = ledgers(tmp_path)
    contribution = record_contribution(flows)
    result = calculate(returns)

    assert result["scope"] == (
        "SIMULATED_CASH_FLOW_NEUTRAL_TIME_WEIGHTED_SP500_"
        "PORTFOLIO_BENCHMARK_RETURN"
    )
    assert result["benchmark_family"] == "S&P 500"
    assert result["benchmark_ticker"] == "^GSPC"
    assert result["subperiod_count"] == 2
    assert result["cumulative_external_cash_flow"] == "100"
    assert result["supporting_cash_flow_ids"] == [contribution["flow_id"]]
    first, second = result["subperiods"]
    assert first["exact_fractions"]["benchmark_subperiod_return"] == fraction(1, 20)
    assert first["benchmark_pre_flow_equity"] == "1050"
    assert first["benchmark_post_flow_equity"] == "1150"
    assert second["base_benchmark_total_equity"] == "1120"
    assert second["cumulative_prior_external_cash_flow"] == "100"
    assert second["benchmark_pre_flow_equity"] == "1220"
    assert second["exact_fractions"]["benchmark_subperiod_return"] == fraction(7, 115)
    assert result["exact_fractions"][
        "time_weighted_benchmark_portfolio_return"
    ] == fraction(131, 1150)
    assert result["time_weighted_benchmark_portfolio_return"] == (
        "0.1139130434782608695652173913"
    )
    assert result["benchmark_portfolio_return_calculated"] is True
    assert result["relative_portfolio_return_calculated"] is False
    assert result["alpha_calculated"] is False
    assert result["annualized"] is False
    assert result["risk_adjusted"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert returns.verify() == [result]


def test_end_boundary_withdrawal_does_not_change_return_through_boundary(tmp_path):
    _, _, flows, returns = ledgers(tmp_path)
    flows.record(
        portfolio_version="PORT-001",
        horizon="1_MONTH",
        flow_type="WITHDRAWAL",
        amount=50,
        recorded_at="2025-02-03T17:03:00+00:00",
    )
    result = calculate(returns, calculated_at="2025-02-03T17:04:00+00:00")
    assert result["time_weighted_benchmark_portfolio_return"] == "0.12"
    assert result["ending_benchmark_pre_flow_equity"] == "1120"
    assert result["ending_benchmark_post_flow_equity"] == "1070"


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"through_horizon": "ENTRY"}, "baseline"),
        ({"through_horizon": "3_MONTHS"}, "valuation is missing"),
        (
            {"calculated_at": "2025-02-03T17:01:59+00:00"},
            "predate supporting",
        ),
        ({"calculated_at": "2999-01-01T00:00:00+00:00"}, "future"),
    ],
)
def test_invalid_request_fails_closed(tmp_path, overrides, fragment):
    _, _, _, returns = ledgers(tmp_path)
    result = calculate(returns, **overrides)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert returns.records() == []


def test_misaligned_asset_and_benchmark_boundaries_fail_closed(tmp_path):
    _, benchmarks, _, returns = ledgers(tmp_path)
    benchmarks.values[0]["outcome_benchmark_price_effective_at"] = (
        "2025-01-09T16:01:00+00:00"
    )
    result = calculate(returns)
    assert result["status"] == "NOT_CALCULABLE"
    assert "exact effective times" in " ".join(result["reasons"])


def test_missing_exact_asset_valuation_boundary_fails_closed(tmp_path):
    assets, _, _, returns = ledgers(tmp_path)
    assets.values[0]["record_hash"] = "changed"
    result = calculate(returns)
    assert result["status"] == "NOT_CALCULABLE"
    assert "exact asset valuation boundary" in " ".join(result["reasons"])


def test_identical_concurrent_retries_create_one_record(tmp_path):
    _, _, _, returns = ledgers(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(returns), range(2)))
    assert first == second
    assert len(returns.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"benchmark_portfolio_return_calculated": False},
        {"relative_portfolio_return_calculated": True},
        {"alpha_calculated": True},
        {"time_weighted_benchmark_portfolio_return": "999"},
        {"funding_record_hash": "forged"},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    _, _, _, returns = ledgers(tmp_path)
    calculate(returns)
    rewrite_with_valid_hash(returns.path, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        returns.verify()


def test_supporting_benchmark_valuation_tampering_is_detected(tmp_path):
    _, benchmarks, _, returns = ledgers(tmp_path)
    calculate(returns)
    benchmarks.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        returns.verify()


def test_later_boundary_cash_flow_does_not_invalidate_pinned_return(tmp_path):
    _, _, flows, returns = ledgers(tmp_path)
    result = calculate(returns)
    flows.record(
        portfolio_version="PORT-001",
        horizon="1_MONTH",
        flow_type="CONTRIBUTION",
        amount="100",
        recorded_at="2025-02-03T17:04:00+00:00",
    )
    assert returns.verify() == [result]


def test_later_intermediate_valuation_does_not_invalidate_pinned_return(tmp_path):
    assets, benchmarks, _, returns = ledgers(tmp_path)
    result = calculate(returns)
    asset = dict(assets.values[0])
    asset.update(
        {
            "valuation_id": "PVAL-2-WEEKS",
            "record_hash": "asset-valuation-inserted-hash",
            "horizon": "2_WEEKS",
            "horizon_label": "2 weeks",
            "outcome_asset_price_effective_at": "2025-01-16T16:00:00+00:00",
            "outcome_benchmark_price_effective_at": "2025-01-16T16:00:00+00:00",
            "calculated_at": "2025-02-03T17:04:00+00:00",
        }
    )
    benchmark = dict(benchmarks.values[0])
    benchmark.update(
        {
            "valuation_id": "PBVAL-2-WEEKS",
            "record_hash": "benchmark-valuation-inserted-hash",
            "horizon": "2_WEEKS",
            "horizon_label": "2 weeks",
            "outcome_asset_price_effective_at": "2025-01-16T16:00:00+00:00",
            "outcome_benchmark_price_effective_at": "2025-01-16T16:00:00+00:00",
            "asset_portfolio_valuation_id": "PVAL-2-WEEKS",
            "asset_portfolio_valuation_hash": "asset-valuation-inserted-hash",
            "calculated_at": "2025-02-03T17:04:00+00:00",
        }
    )
    assets.values.append(asset)
    benchmarks.values.append(benchmark)
    assert returns.verify() == [result]


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    _, _, _, returns = ledgers(tmp_path)
    result = calculate(returns)
    with returns.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        returns.verify()
    backup = returns.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert returns.verify() == [result]
