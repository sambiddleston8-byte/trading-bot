from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import PortfolioRelativeReturnLedger


IDENTITY = {
    "portfolio_version": "PORT-001",
    "through_horizon": "1_MONTH",
    "through_horizon_label": "1 month",
    "currency": "USD",
    "funding_id": "PFUND-001",
    "funding_record_hash": "funding-hash",
    "supporting_cash_flow_ids": ["PCFLOW-001"],
    "supporting_cash_flow_hashes": ["cash-flow-hash"],
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "portfolio", "version": "1.0"}],
    "git_revision": "abc123",
}


def fraction(numerator, denominator=1):
    return {"numerator": str(numerator), "denominator": str(denominator)}


class ReturnLedgerStub:
    def __init__(self, values):
        self.values = values

    def verify(self):
        return self.values


def supporting_records():
    asset = {
        "result_id": "PRET-001",
        "record_hash": "asset-return-hash",
        "calculated_at": "2025-02-03T17:04:00+00:00",
        "supporting_valuation_ids": ["PVAL-1-WEEK", "PVAL-1-MONTH"],
        "supporting_valuation_hashes": ["asset-one-hash", "asset-two-hash"],
        "time_weighted_portfolio_return": "0.23",
        "exact_fractions": {
            "time_weighted_portfolio_return": fraction(23, 100)
        },
        **IDENTITY,
    }
    benchmark = {
        "result_id": "PBRET-001",
        "record_hash": "benchmark-return-hash",
        "calculated_at": "2025-02-03T17:05:00+00:00",
        "benchmark_family": "S&P 500",
        "benchmark_ticker": "^GSPC",
        "supporting_asset_valuation_ids": ["PVAL-1-WEEK", "PVAL-1-MONTH"],
        "supporting_asset_valuation_hashes": ["asset-one-hash", "asset-two-hash"],
        "supporting_benchmark_valuation_ids": ["PBVAL-1-WEEK", "PBVAL-1-MONTH"],
        "supporting_benchmark_valuation_hashes": [
            "benchmark-one-hash",
            "benchmark-two-hash",
        ],
        "time_weighted_benchmark_portfolio_return": (
            "0.1139130434782608695652173913"
        ),
        "exact_fractions": {
            "time_weighted_benchmark_portfolio_return": fraction(131, 1150)
        },
        **IDENTITY,
    }
    return asset, benchmark


def ledgers(tmp_path):
    asset, benchmark = supporting_records()
    assets = ReturnLedgerStub([asset])
    benchmarks = ReturnLedgerStub([benchmark])
    relative = PortfolioRelativeReturnLedger(
        tmp_path / "portfolio_relative_returns.jsonl", assets, benchmarks
    )
    return relative, assets, benchmarks, asset, benchmark


def calculate(relative, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "through_horizon": "1_MONTH",
        "calculated_at": "2025-02-03T17:06:00+00:00",
    }
    values.update(overrides)
    return relative.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import portfolio_relative_return as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_calculates_exact_portfolio_benchmark_relative_return(tmp_path):
    relative, _, _, asset, benchmark = ledgers(tmp_path)
    result = calculate(relative)

    expected = Fraction(23, 100) - Fraction(131, 1150)
    assert result["scope"] == (
        "SIMULATED_PORTFOLIO_BENCHMARK_RELATIVE_TIME_WEIGHTED_RETURN"
    )
    assert result["comparison_method"] == (
        "ARITHMETIC_DIFFERENCE_NOT_RISK_ADJUSTED_ALPHA"
    )
    assert result["asset_portfolio_return_result_hash"] == asset["record_hash"]
    assert result["benchmark_portfolio_return_result_hash"] == benchmark["record_hash"]
    assert result["exact_fractions"]["portfolio_benchmark_relative_return"] == {
        "numerator": str(expected.numerator),
        "denominator": str(expected.denominator),
    }
    assert result["portfolio_benchmark_relative_return"] == (
        "0.1160869565217391304347826087"
    )
    assert result["supporting_asset_valuation_ids"] == [
        "PVAL-1-WEEK",
        "PVAL-1-MONTH",
    ]
    assert result["supporting_benchmark_valuation_ids"] == [
        "PBVAL-1-WEEK",
        "PBVAL-1-MONTH",
    ]
    assert result["simulation_only"] is True
    assert result["portfolio_level_only"] is True
    assert result["relative_portfolio_return_calculated"] is True
    assert result["alpha_calculated"] is False
    assert result["risk_adjusted"] is False
    assert result["annualized"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert relative.verify() == [result]


@pytest.mark.parametrize(
    "remove_side,fragment",
    [
        ("asset", "asset portfolio-return"),
        ("benchmark", "benchmark portfolio-return"),
    ],
)
def test_missing_supporting_result_fails_closed(tmp_path, remove_side, fragment):
    relative, assets, benchmarks, _, _ = ledgers(tmp_path)
    if remove_side == "asset":
        assets.values = []
    else:
        benchmarks.values = []
    result = calculate(relative)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert relative.records() == []


@pytest.mark.parametrize(
    "side,field,value",
    [
        ("benchmark", "funding_record_hash", "changed"),
        ("benchmark", "supporting_asset_valuation_hashes", ["changed"]),
        ("benchmark", "supporting_cash_flow_hashes", ["changed"]),
        ("benchmark", "through_horizon_label", "different"),
        ("asset", "model_versions", [{"component": "portfolio", "version": "2"}]),
    ],
)
def test_misaligned_evidence_fails_closed(tmp_path, side, field, value):
    relative, assets, benchmarks, _, _ = ledgers(tmp_path)
    target = assets.values[0] if side == "asset" else benchmarks.values[0]
    target[field] = value
    result = calculate(relative)
    assert result["status"] == "NOT_CALCULABLE"
    assert "exact funding" in " ".join(result["reasons"])
    assert relative.records() == []


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"through_horizon": "ENTRY"}, "baseline"),
        ({"through_horizon": "3_MONTHS"}, "missing"),
        (
            {"calculated_at": "2025-02-03T17:04:59+00:00"},
            "predate supporting",
        ),
        ({"calculated_at": "2999-01-01T00:00:00+00:00"}, "future"),
    ],
)
def test_invalid_request_fails_closed(tmp_path, overrides, fragment):
    relative, _, _, _, _ = ledgers(tmp_path)
    result = calculate(relative, **overrides)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert relative.records() == []


def test_identical_concurrent_retries_create_one_record(tmp_path):
    relative, _, _, _, _ = ledgers(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(relative), range(2)))
    assert first == second
    assert len(relative.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"relative_portfolio_return_calculated": False},
        {"alpha_calculated": True},
        {"risk_adjusted": True},
        {"annualized": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"portfolio_benchmark_relative_return": "999"},
        {"asset_portfolio_return_result_hash": "forged"},
        {"supporting_asset_valuation_hashes": ["forged"]},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    relative, _, _, _, _ = ledgers(tmp_path)
    calculate(relative)
    rewrite_with_valid_hash(relative.path, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        relative.verify()


def test_supporting_return_tampering_is_detected(tmp_path):
    relative, assets, _, _, _ = ledgers(tmp_path)
    calculate(relative)
    assets.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        relative.verify()


def test_invalid_exact_fraction_fails_closed(tmp_path):
    relative, _, benchmarks, _, _ = ledgers(tmp_path)
    benchmarks.values[0]["exact_fractions"][
        "time_weighted_benchmark_portfolio_return"
    ] = fraction(1, 0)
    result = calculate(relative)
    assert result["status"] == "NOT_CALCULABLE"
    assert "exact fraction" in " ".join(result["reasons"])


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    relative, _, _, _, _ = ledgers(tmp_path)
    result = calculate(relative)
    with relative.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        relative.verify()
    backup = relative.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert relative.verify() == [result]
