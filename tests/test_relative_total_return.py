from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import json

import pytest

from core.broker import LocalPaperExecutionLedger, PaperOrderProposalLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import (
    BenchmarkDistributionLedger,
    BenchmarkTotalReturnLedger,
    CorporateActionLedger,
    OutcomeObservationLedger,
    RelativeTotalReturnLedger,
    TotalReturnLedger,
)


METHODOLOGY_URI = (
    "https://www.spglobal.com/spdji/en/documents/methodologies/"
    "methodology-index-math.pdf"
)


def complete_chain(tmp_path):
    proposals = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    proposals.propose(
        decision_id="DEC-001",
        portfolio_version="PORT-001",
        ticker="NVDA",
        side="BUY",
        quantity=2,
        reference_price=100,
        target_weight=0.1,
        strategy_version="strategy-v1",
        model_versions=[{"component": "portfolio", "version": "1.0"}],
        created_at="2025-01-02T15:00:00+00:00",
        git_revision="abc123",
        order_id="PORD-001",
    )
    executions = LocalPaperExecutionLedger(tmp_path / "fills.jsonl", proposals)
    fill = executions.simulate_full_fill(
        order_id="PORD-001",
        fill_price=101,
        fees=2,
        filled_at="2025-01-02T15:01:00+00:00",
    )
    observations = OutcomeObservationLedger(tmp_path / "observations.jsonl", executions)
    observations.observe(
        fill_id=fill["fill_id"],
        horizon="ENTRY",
        benchmark_price=5_900,
        benchmark_price_effective_at="2025-01-02T15:00:00+00:00",
        retrieved_at="2025-01-02T15:02:00+00:00",
        data_source="TEST_FIXTURE",
        source_version="fixture-v1",
        market_price_basis="UNADJUSTED_CLOSE",
    )
    observations.observe(
        fill_id=fill["fill_id"],
        horizon="1_MONTH",
        asset_price=111,
        benchmark_price=6_018,
        asset_price_effective_at="2025-02-03T16:00:00+00:00",
        benchmark_price_effective_at="2025-02-03T16:00:00+00:00",
        retrieved_at="2025-02-03T17:00:00+00:00",
        data_source="TEST_FIXTURE",
        source_version="fixture-v1",
        market_price_basis="UNADJUSTED_CLOSE",
    )

    actions = CorporateActionLedger(tmp_path / "actions.jsonl", executions)
    actions.record(
        fill_id=fill["fill_id"],
        covers_from_at="2025-01-02T15:01:00+00:00",
        through_at="2025-02-03T16:00:00+00:00",
        retrieved_at="2025-02-03T17:00:00+00:00",
        data_source="TEST_CORPORATE_ACTION_FIXTURE",
        source_version="fixture-v1",
        source_input_sha256="a" * 64,
        events=(),
        completeness_status="COMPLETE",
        uncertainty_reasons=(),
    )
    asset_returns = TotalReturnLedger(
        tmp_path / "asset_returns.jsonl", observations, actions
    )

    distributions = BenchmarkDistributionLedger(
        tmp_path / "distributions.jsonl", observations
    )
    distributions.observe(
        fill_id=fill["fill_id"],
        horizon="1_MONTH",
        gross_dividend_points="7.25",
        completeness_status="COMPLETE",
        uncertainty_reasons=(),
        retrieved_at="2025-02-03T17:30:00+00:00",
        data_source="TEST_DIVIDEND_POINT_FIXTURE",
        source_version="fixture-v1",
        source_input_sha256="b" * 64,
        methodology_name="S&P Dow Jones Indices Index Mathematics Methodology",
        methodology_version="2026-04",
        methodology_uri=METHODOLOGY_URI,
        methodology_sha256="c" * 64,
    )
    benchmark_returns = BenchmarkTotalReturnLedger(
        tmp_path / "benchmark_returns.jsonl", observations, distributions
    )
    relative_returns = RelativeTotalReturnLedger(
        tmp_path / "relative_returns.jsonl", asset_returns, benchmark_returns
    )
    return relative_returns, asset_returns, benchmark_returns, fill


def calculate_supporting_returns(asset_returns, benchmark_returns, fill):
    asset = asset_returns.calculate(
        fill_id=fill["fill_id"],
        horizon="1_MONTH",
        calculated_at="2025-02-03T17:31:00+00:00",
    )
    benchmark = benchmark_returns.calculate(
        fill_id=fill["fill_id"],
        horizon="1_MONTH",
        accept_current_index_composition=True,
        calculated_at="2025-02-03T17:31:00+00:00",
    )
    return asset, benchmark


def calculate(relative_returns, fill, **overrides):
    values = {
        "fill_id": fill["fill_id"],
        "horizon": "1_MONTH",
        "calculated_at": "2025-02-03T17:32:00+00:00",
    }
    values.update(overrides)
    return relative_returns.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import relative_total_return as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_calculates_exact_position_relative_total_return(tmp_path):
    relative, asset_returns, benchmark_returns, fill = complete_chain(tmp_path)
    asset, benchmark = calculate_supporting_returns(
        asset_returns, benchmark_returns, fill
    )
    result = calculate(relative, fill)

    expected = Fraction(3, 34) - Fraction(501, 23600)
    assert result["scope"] == "SIMULATED_POSITION_BENCHMARK_RELATIVE_TOTAL_RETURN"
    assert result["comparison_method"] == (
        "ARITHMETIC_DIFFERENCE_NOT_RISK_ADJUSTED_ALPHA"
    )
    assert result["asset_total_return_result_hash"] == asset["record_hash"]
    assert result["benchmark_total_return_result_hash"] == benchmark["record_hash"]
    assert result["exact_fractions"]["position_relative_total_return"] == {
        "numerator": str(expected.numerator),
        "denominator": str(expected.denominator),
    }
    assert result["position_relative_total_return"] == (
        "0.06700648055832502492522432702"
    )
    assert result["simulation_only"] is True
    assert result["position_level_only"] is True
    assert result["relative_total_return_calculated"] is True
    assert result["alpha_calculated"] is False
    assert result["risk_adjusted"] is False
    assert result["portfolio_performance_claim"] is False
    assert result["track_record_claim"] is False
    assert result["learning_eligible"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert relative.verify() == [result]


def test_missing_supporting_results_fail_closed_without_append(tmp_path):
    relative, asset_returns, benchmark_returns, fill = complete_chain(tmp_path)
    asset_returns.calculate(
        fill_id=fill["fill_id"],
        horizon="1_MONTH",
        calculated_at="2025-02-03T17:31:00+00:00",
    )

    result = calculate(relative, fill)
    assert result["status"] == "NOT_CALCULABLE"
    assert "benchmark" in " ".join(result["reasons"])
    assert relative.records() == []


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"horizon": "ENTRY"}, "baseline"),
        ({"horizon": "3_MONTHS"}, "missing"),
        (
            {"calculated_at": "2025-02-03T17:30:59+00:00"},
            "predate supporting return",
        ),
        ({"calculated_at": "2999-01-01T00:00:00+00:00"}, "future"),
    ],
)
def test_invalid_request_fails_closed_without_append(tmp_path, overrides, fragment):
    relative, asset_returns, benchmark_returns, fill = complete_chain(tmp_path)
    calculate_supporting_returns(asset_returns, benchmark_returns, fill)

    result = calculate(relative, fill, **overrides)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert relative.records() == []


def test_identical_concurrent_retries_create_one_record(tmp_path):
    relative, asset_returns, benchmark_returns, fill = complete_chain(tmp_path)
    calculate_supporting_returns(asset_returns, benchmark_returns, fill)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(
            executor.map(lambda _: calculate(relative, fill), range(2))
        )
    assert first == second
    assert len(relative.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"alpha_calculated": True},
        {"risk_adjusted": True},
        {"learning_eligible": True},
        {"position_relative_total_return": "999"},
        {"asset_total_return_result_hash": "d" * 64},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    relative, asset_returns, benchmark_returns, fill = complete_chain(tmp_path)
    calculate_supporting_returns(asset_returns, benchmark_returns, fill)
    calculate(relative, fill)

    rewrite_with_valid_hash(relative.path, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        relative.verify()


def test_supporting_return_tampering_is_detected(tmp_path):
    relative, asset_returns, benchmark_returns, fill = complete_chain(tmp_path)
    calculate_supporting_returns(asset_returns, benchmark_returns, fill)
    calculate(relative, fill)

    asset_record = json.loads(asset_returns.path.read_text())
    asset_record["gross_total_return_after_entry_fee_excl_exit"] = "999"
    asset_returns.path.write_text(json.dumps(asset_record) + "\n")
    with pytest.raises(LedgerIntegrityError):
        relative.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    relative, asset_returns, benchmark_returns, fill = complete_chain(tmp_path)
    calculate_supporting_returns(asset_returns, benchmark_returns, fill)
    result = calculate(relative, fill)
    with relative.path.open("ab") as target:
        target.write(b'{"partial"')

    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        relative.verify()
    backup = relative.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert relative.verify() == [result]
