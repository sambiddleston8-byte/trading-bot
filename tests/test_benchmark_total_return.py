from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker import LocalPaperExecutionLedger, PaperOrderProposalLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import (
    BenchmarkDistributionLedger,
    BenchmarkTotalReturnLedger,
    OutcomeObservationLedger,
)


METHODOLOGY_URI = (
    "https://www.spglobal.com/spdji/en/documents/methodologies/"
    "methodology-index-math.pdf"
)


def complete_chain(
    tmp_path,
    *,
    dividend_points="7.25",
    completeness="COMPLETE",
    reasons=(),
    include_distribution=True,
    outcome_benchmark_price=6_018,
):
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
        benchmark_price=outcome_benchmark_price,
        asset_price_effective_at="2025-02-03T16:00:00+00:00",
        benchmark_price_effective_at="2025-02-03T16:00:00+00:00",
        retrieved_at="2025-02-03T17:00:00+00:00",
        data_source="TEST_FIXTURE",
        source_version="fixture-v1",
        market_price_basis="UNADJUSTED_CLOSE",
    )
    distributions = BenchmarkDistributionLedger(
        tmp_path / "distributions.jsonl", observations
    )
    evidence = None
    if include_distribution:
        evidence = distributions.observe(
            fill_id=fill["fill_id"],
            horizon="1_MONTH",
            gross_dividend_points=(
                dividend_points if completeness == "COMPLETE" else None
            ),
            completeness_status=completeness,
            uncertainty_reasons=reasons,
            retrieved_at="2025-02-03T17:30:00+00:00",
            data_source="TEST_DIVIDEND_POINT_FIXTURE",
            source_version="fixture-v1",
            source_input_sha256="a" * 64,
            methodology_name=(
                "S&P Dow Jones Indices Index Mathematics Methodology"
            ),
            methodology_version="2026-04",
            methodology_uri=METHODOLOGY_URI,
            methodology_sha256="b" * 64,
        )
    ledger = BenchmarkTotalReturnLedger(
        tmp_path / "benchmark_returns.jsonl", observations, distributions
    )
    return ledger, fill, evidence


def calculate(ledger, fill, **overrides):
    values = {
        "fill_id": fill["fill_id"],
        "horizon": "1_MONTH",
        "accept_current_index_composition": True,
        "calculated_at": "2025-02-03T17:31:00+00:00",
    }
    values.update(overrides)
    return ledger.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import benchmark_total_return as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_calculates_exact_sp500_gross_cash_total_return(tmp_path):
    ledger, fill, evidence = complete_chain(tmp_path)
    result = calculate(ledger, fill)

    assert result["scope"] == "S&P_500_GROSS_CASH_TOTAL_RETURN"
    assert result["entry_benchmark_price"] == "5900"
    assert result["outcome_benchmark_price"] == "6018"
    assert result["gross_dividend_points"] == "7.25"
    assert result["gross_cash_ending_value"] == "6025.25"
    assert result["benchmark_price_return_context"] == "0.02"
    assert result["benchmark_distribution_return_component"] == (
        "0.001228813559322033898305084745762712"
    )
    assert result["benchmark_gross_cash_total_return"] == (
        "0.02122881355932203389830508474576271"
    )
    assert result["exact_fractions"]["benchmark_gross_cash_total_return"] == {
        "numerator": "501",
        "denominator": "23600",
    }
    assert result["composition_approximation_accepted"] is True
    assert result["benchmark_distribution_evidence_hash"] == evidence["record_hash"]
    assert result["benchmark_total_return_calculated"] is True
    assert result["relative_total_return_calculated"] is False
    assert result["alpha_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert ledger.verify() == [result]


def test_zero_dividend_points_reduce_to_price_return(tmp_path):
    ledger, fill, _ = complete_chain(tmp_path, dividend_points="0")
    result = calculate(ledger, fill)
    assert result["gross_dividend_points"] == "0"
    assert result["benchmark_distribution_return_component"] == "0"
    assert result["benchmark_gross_cash_total_return"] == "0.02"


def test_composition_approximation_requires_explicit_acceptance(tmp_path):
    ledger, fill, _ = complete_chain(tmp_path)
    result = calculate(ledger, fill, accept_current_index_composition=False)
    assert result["status"] == "NOT_CALCULABLE"
    assert result["benchmark_total_return_calculated"] is False
    assert "explicitly accepted" in " ".join(result["reasons"])
    assert ledger.records() == []


@pytest.mark.parametrize(
    "kwargs,fragment",
    [
        ({"include_distribution": False}, "evidence is missing"),
        (
            {
                "completeness": "UNCERTAIN",
                "reasons": ["provider coverage incomplete"],
            },
            "evidence is UNCERTAIN",
        ),
    ],
)
def test_missing_or_uncertain_distribution_evidence_fails_closed(
    tmp_path, kwargs, fragment
):
    ledger, fill, _ = complete_chain(tmp_path, **kwargs)
    result = calculate(ledger, fill)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert ledger.records() == []


def test_distribution_from_different_price_records_fails_closed(tmp_path):
    first, fill, _ = complete_chain(tmp_path / "first")
    second, _, _ = complete_chain(
        tmp_path / "second", outcome_benchmark_price=6_019
    )
    mixed = BenchmarkTotalReturnLedger(
        tmp_path / "mixed.jsonl",
        first.observation_ledger,
        second.distribution_ledger,
    )
    result = calculate(mixed, fill)
    assert result["status"] == "NOT_CALCULABLE"
    assert "exact price observations" in " ".join(result["reasons"])
    assert mixed.records() == []


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"horizon": "ENTRY"}, "baseline"),
        ({"horizon": "3_MONTHS"}, "observation is missing"),
        (
            {"calculated_at": "2025-02-03T17:29:59+00:00"},
            "predate supporting evidence",
        ),
        ({"calculated_at": "2999-01-01T00:00:00+00:00"}, "future"),
    ],
)
def test_invalid_calculation_request_does_not_append(
    tmp_path, overrides, fragment
):
    ledger, fill, _ = complete_chain(tmp_path)
    result = calculate(ledger, fill, **overrides)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert ledger.records() == []


def test_identical_concurrent_retries_create_one_record(tmp_path):
    ledger, fill, _ = complete_chain(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(ledger, fill), range(2)))
    assert first == second
    assert len(ledger.verify()) == 1


def test_concurrent_non_idempotent_retries_fail_closed(tmp_path):
    ledger, fill, _ = complete_chain(tmp_path)

    def attempt(_):
        try:
            return calculate(ledger, fill, allow_existing=False)
        except LedgerIntegrityError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, range(2)))
    assert sum(isinstance(item, dict) for item in results) == 1
    errors = [item for item in results if isinstance(item, LedgerIntegrityError)]
    assert len(errors) == 1
    assert "already exists" in str(errors[0])
    assert len(ledger.verify()) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("benchmark_gross_cash_total_return", "9"),
        ("gross_dividend_points", "725"),
        ("composition_approximation_accepted", False),
        ("relative_total_return_calculated", True),
        ("alpha_calculated", True),
        ("learning_eligible", True),
        ("entry_observation_hash", "0" * 64),
        ("benchmark_distribution_evidence_hash", "0" * 64),
        ("benchmark_definition_sha256", "0" * 64),
        ("formula", {}),
        ("decision_id", "DEC-FORGED"),
    ],
)
def test_rehashed_result_cannot_change_economics_or_boundary(
    tmp_path, field, value
):
    ledger, fill, _ = complete_chain(tmp_path)
    calculate(ledger, fill)
    rewrite_with_valid_hash(ledger.path, **{field: value})
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        ledger.verify()


def test_supporting_evidence_tamper_is_detected(tmp_path):
    ledger, fill, _ = complete_chain(tmp_path)
    calculate(ledger, fill)
    record = json.loads(ledger.distribution_ledger.path.read_text())
    record["gross_dividend_points"] = "99"
    ledger.distribution_ledger.path.write_text(json.dumps(record) + "\n")
    with pytest.raises(LedgerIntegrityError, match="modified"):
        ledger.verify()


def test_explicit_tail_repair_preserves_partial_bytes(tmp_path):
    ledger, fill, _ = complete_chain(tmp_path)
    first = calculate(ledger, fill)
    with ledger.path.open("ab") as target:
        target.write(b'{"partial":')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial":'
    assert ledger.verify() == [first]
