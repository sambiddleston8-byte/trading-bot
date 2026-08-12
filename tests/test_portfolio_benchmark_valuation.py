from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import SimulatedPortfolioBenchmarkValuationLedger


IDENTITY = {
    "portfolio_version": "PORT-001",
    "horizon": "1_MONTH",
    "horizon_label": "1 month",
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "portfolio", "version": "1.0"}],
    "git_revision": "abc123",
}


def fraction(numerator, denominator=1):
    return {"numerator": str(numerator), "denominator": str(denominator)}


def position_identity(ticker, suffix):
    return {
        "fill_id": f"SFILL-{suffix}",
        "order_id": f"PORD-{suffix}",
        "decision_id": f"DEC-{suffix}",
        "ticker": ticker,
        "entry_observation_hash": f"entry-{suffix}",
        "outcome_observation_hash": f"outcome-{suffix}",
        **IDENTITY,
    }


class FundingStub:
    def funding_for(self, portfolio_version):
        if portfolio_version != "PORT-001":
            return None
        return {
            "funding_id": "PFUND-001",
            "record_hash": "funding-hash",
            "exact_amount": fraction(1000),
            **IDENTITY,
        }


class TotalReturnStub:
    def __init__(self):
        self.values = [
            {
                "result_id": "ATR-NVDA",
                "record_hash": "asset-return-nvda",
                **position_identity("NVDA", "NVDA"),
            },
            {
                "result_id": "ATR-AAPL",
                "record_hash": "asset-return-aapl",
                **position_identity("AAPL", "AAPL"),
            },
        ]

    def verify(self):
        return self.values


class AssetValuationStub:
    def __init__(self):
        self.funding_ledger = FundingStub()
        self.total_return_ledger = TotalReturnStub()
        self.value = {
            "valuation_id": "PVAL-001",
            "record_hash": "asset-valuation-hash",
            "calculated_at": "2025-02-03T17:01:00+00:00",
            "outcome_asset_price_effective_at": "2025-02-03T16:00:00+00:00",
            "outcome_benchmark_price_effective_at": "2025-02-03T16:00:00+00:00",
            "positions": [
                {
                    "ticker": "NVDA",
                    "order_id": "PORD-NVDA",
                    "fill_id": "SFILL-NVDA",
                    "decision_id": "DEC-NVDA",
                    "total_return_result_id": "ATR-NVDA",
                    "total_return_result_hash": "asset-return-nvda",
                    "exact_fractions": {"recorded_entry_cost": fraction(204)},
                },
                {
                    "ticker": "AAPL",
                    "order_id": "PORD-AAPL",
                    "fill_id": "SFILL-AAPL",
                    "decision_id": "DEC-AAPL",
                    "total_return_result_id": "ATR-AAPL",
                    "total_return_result_hash": "asset-return-aapl",
                    "exact_fractions": {"recorded_entry_cost": fraction(154)},
                },
            ],
            "exact_fractions": {"initial_funding": fraction(1000)},
            **IDENTITY,
        }

    def verify(self):
        return [self.value]


class BenchmarkReturnStub:
    def __init__(self):
        self.values = [
            {
                "result_id": "BTR-NVDA",
                "record_hash": "benchmark-return-nvda",
                "calculated_at": "2025-02-03T17:01:00+00:00",
                "exact_fractions": {
                    "benchmark_gross_cash_total_return": fraction(1, 10)
                },
                **position_identity("NVDA", "NVDA"),
            },
            {
                "result_id": "BTR-AAPL",
                "record_hash": "benchmark-return-aapl",
                "calculated_at": "2025-02-03T17:01:00+00:00",
                "exact_fractions": {
                    "benchmark_gross_cash_total_return": fraction(1, 5)
                },
                **position_identity("AAPL", "AAPL"),
            },
        ]

    def verify(self):
        return self.values


def ledger(tmp_path):
    assets = AssetValuationStub()
    benchmarks = BenchmarkReturnStub()
    return (
        SimulatedPortfolioBenchmarkValuationLedger(
            tmp_path / "portfolio_benchmark_valuations.jsonl", assets, benchmarks
        ),
        assets,
        benchmarks,
    )


def calculate(value_ledger, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "horizon": "1_MONTH",
        "calculated_at": "2025-02-03T17:02:00+00:00",
    }
    values.update(overrides)
    return value_ledger.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import portfolio_benchmark_valuation as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_values_matched_capital_sp500_counterfactual_exactly(tmp_path):
    value_ledger, _, _ = ledger(tmp_path)
    result = calculate(value_ledger)

    assert result["scope"] == (
        "SIMULATED_LIKE_FOR_LIKE_SP500_PORTFOLIO_BENCHMARK_VALUATION"
    )
    assert result["benchmark_family"] == "S&P 500"
    assert result["benchmark_ticker"] == "^GSPC"
    assert result["initial_funding"] == "1000"
    assert result["total_matched_benchmark_capital"] == "358"
    assert result["benchmark_cash_reserve"] == "642"
    assert result["total_benchmark_position_ending_value"] == "409.2"
    assert result["benchmark_total_equity"] == "1051.2"
    assert result["exact_fractions"]["benchmark_total_equity"] == fraction(5256, 5)
    assert result["exact_fractions"]["benchmark_weight_total"] == fraction(1)
    positions = {item["ticker"]: item for item in result["positions"]}
    assert positions["NVDA"]["matched_benchmark_capital"] == "204"
    assert positions["NVDA"]["benchmark_position_ending_value"] == "224.4"
    assert positions["AAPL"]["matched_benchmark_capital"] == "154"
    assert positions["AAPL"]["benchmark_position_ending_value"] == "184.8"
    assert result["benchmark_portfolio_valuation_calculated"] is True
    assert result["benchmark_portfolio_return_calculated"] is False
    assert result["relative_portfolio_return_calculated"] is False
    assert result["alpha_calculated"] is False
    assert result["annualized"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert value_ledger.verify() == [result]


def test_benchmark_gets_same_fee_inclusive_capital_but_no_invented_cost(tmp_path):
    value_ledger, _, _ = ledger(tmp_path)
    result = calculate(value_ledger)
    assert result["formula"]["capital_policy"] == (
        "MATCH_ASSET_RECORDED_ENTRY_COST_INCLUDING_ENTRY_FEE_BASIS"
    )
    assert result["formula"]["benchmark_cost_policy"] == (
        "NO_BENCHMARK_TRANSACTION_COST_INVENTED"
    )


def test_missing_benchmark_position_fails_closed(tmp_path):
    value_ledger, _, benchmarks = ledger(tmp_path)
    benchmarks.values.pop()
    result = calculate(value_ledger)
    assert result["status"] == "NOT_CALCULABLE"
    assert "Every asset portfolio position" in " ".join(result["reasons"])
    assert value_ledger.records() == []


def test_mismatched_position_evidence_fails_closed(tmp_path):
    value_ledger, _, benchmarks = ledger(tmp_path)
    benchmarks.values[0]["entry_observation_hash"] = "wrong-entry"
    result = calculate(value_ledger)
    assert result["status"] == "NOT_CALCULABLE"
    assert "exact identity" in " ".join(result["reasons"])
    assert value_ledger.records() == []


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"horizon": "ENTRY"}, "baseline"),
        ({"horizon": "3_MONTHS"}, "valuation is missing"),
        (
            {"calculated_at": "2025-02-03T17:00:59+00:00"},
            "predate supporting",
        ),
        ({"calculated_at": "2999-01-01T00:00:00+00:00"}, "future"),
    ],
)
def test_invalid_request_fails_closed(tmp_path, overrides, fragment):
    value_ledger, _, _ = ledger(tmp_path)
    result = calculate(value_ledger, **overrides)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert value_ledger.records() == []


def test_identical_concurrent_retries_create_one_record(tmp_path):
    value_ledger, _, _ = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(value_ledger), range(2)))
    assert first == second
    assert len(value_ledger.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"benchmark_portfolio_return_calculated": True},
        {"relative_portfolio_return_calculated": True},
        {"alpha_calculated": True},
        {"benchmark_total_equity": "999"},
        {"asset_portfolio_valuation_hash": "forged"},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    value_ledger, _, _ = ledger(tmp_path)
    calculate(value_ledger)
    rewrite_with_valid_hash(value_ledger.path, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        value_ledger.verify()


def test_supporting_benchmark_tampering_is_detected(tmp_path):
    value_ledger, _, benchmarks = ledger(tmp_path)
    calculate(value_ledger)
    benchmarks.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        value_ledger.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    value_ledger, _, _ = ledger(tmp_path)
    result = calculate(value_ledger)
    with value_ledger.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        value_ledger.verify()
    backup = value_ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert value_ledger.verify() == [result]
