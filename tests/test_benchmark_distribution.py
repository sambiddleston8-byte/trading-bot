from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker import LocalPaperExecutionLedger, PaperOrderProposalLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import BenchmarkDistributionLedger, OutcomeObservationLedger


METHODOLOGY_URI = (
    "https://www.spglobal.com/spdji/en/documents/methodologies/"
    "methodology-index-math.pdf"
)


def complete_chain(tmp_path, *, basis="UNADJUSTED_CLOSE"):
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
        market_price_basis=basis,
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
        market_price_basis=basis,
    )
    return BenchmarkDistributionLedger(tmp_path / "distributions.jsonl", observations), fill


def observe(ledger, fill, **overrides):
    values = {
        "fill_id": fill["fill_id"],
        "horizon": "1_MONTH",
        "gross_dividend_points": "7.2500",
        "retrieved_at": "2025-02-03T17:30:00+00:00",
        "data_source": "TEST_DIVIDEND_POINT_FIXTURE",
        "source_version": "fixture-v1",
        "source_input_sha256": "a" * 64,
        "methodology_name": "S&P Dow Jones Indices Index Mathematics Methodology",
        "methodology_version": "2026-04",
        "methodology_uri": METHODOLOGY_URI,
        "methodology_sha256": "b" * 64,
    }
    values.update(overrides)
    return ledger.observe(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import benchmark_distribution as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_records_complete_like_for_like_dividend_point_evidence(tmp_path):
    ledger, fill = complete_chain(tmp_path)
    record = observe(ledger, fill)

    assert record["gross_dividend_points"] == "7.25"
    assert record["benchmark_family"] == "S&P 500"
    assert record["benchmark_price_ticker"] == "^GSPC"
    assert record["distribution_basis"] == "GROSS_ORDINARY_CASH_DIVIDEND_POINTS"
    assert record["withholding_tax_policy"] == "NO_WITHHOLDING_DEDUCTION"
    assert record["reinvestment_policy"] == "NOT_REINVESTED"
    assert record["basket_composition_policy"] == (
        "CURRENT_INDEX_WEIGHTS_NOT_FROZEN_AT_ENTRY"
    )
    assert record["interval_notation"] == "(START,END]"
    assert record["period_start_at"] == "2025-01-02T15:00:00+00:00"
    assert record["period_end_at"] == "2025-02-03T16:00:00+00:00"
    assert record["temporal_alignment_policy"] == (
        "SAME_UTC_MARKET_DATE_AT_ENTRY_AND_OUTCOME"
    )
    assert record["entry_alignment_seconds"] == "60"
    assert record["outcome_alignment_seconds"] == "0"
    assert record["performance_claim"] is False
    assert record["benchmark_total_return_calculated"] is False
    assert record["relative_return_calculated"] is False
    assert record["alpha_calculated"] is False
    assert record["learning_eligible"] is False
    assert record["previous_hash"] == GENESIS_HASH
    assert len(record["benchmark_definition_sha256"]) == 64
    assert len(record["source_evidence_sha256"]) == 64
    assert ledger.verify() == [record]
    assert ledger.complete_evidence(fill_id=fill["fill_id"], horizon="1_MONTH") == record


def test_zero_dividend_points_are_valid_complete_evidence(tmp_path):
    ledger, fill = complete_chain(tmp_path)
    record = observe(ledger, fill, gross_dividend_points=0)
    assert record["gross_dividend_points"] == "0"


def test_long_decimal_is_preserved_exactly(tmp_path):
    ledger, fill = complete_chain(tmp_path)
    value = "7.123456789012345678901234567890123"
    record = observe(ledger, fill, gross_dividend_points=value)
    assert record["gross_dividend_points"] == value


def test_uncertainty_is_preserved_and_not_complete_evidence(tmp_path):
    ledger, fill = complete_chain(tmp_path)
    record = observe(
        ledger,
        fill,
        gross_dividend_points=None,
        completeness_status="UNCERTAIN",
        uncertainty_reasons=["Provider coverage could not be reconciled"],
    )
    assert record["status"] == "UNCERTAIN"
    assert record["gross_dividend_points"] is None
    assert ledger.complete_evidence(fill_id=fill["fill_id"], horizon="1_MONTH") is None


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"horizon": "ENTRY"}, "baseline"),
        ({"gross_dividend_points": -1}, "non-negative"),
        ({"gross_dividend_points": None}, "non-negative"),
        ({"source_input_sha256": "bad"}, "SHA-256"),
        ({"methodology_sha256": "bad"}, "SHA-256"),
        ({"methodology_uri": "http://www.spglobal.com/methodology.pdf"}, "HTTPS"),
        ({"methodology_uri": "https://example.com/methodology.pdf"}, "spglobal"),
        (
            {
                "completeness_status": "UNCERTAIN",
                "gross_dividend_points": None,
            },
            "requires reasons",
        ),
        (
            {
                "completeness_status": "UNCERTAIN",
                "uncertainty_reasons": ["incomplete"],
            },
            "cannot assert",
        ),
    ],
)
def test_invalid_evidence_fails_closed(tmp_path, overrides, fragment):
    ledger, fill = complete_chain(tmp_path)
    with pytest.raises(ValueError, match=fragment):
        observe(ledger, fill, **overrides)


def test_adjusted_prices_are_rejected_as_not_like_for_like(tmp_path):
    ledger, fill = complete_chain(tmp_path, basis="ADJUSTED_CLOSE")
    with pytest.raises(ValueError, match="Unadjusted"):
        observe(ledger, fill)


@pytest.mark.parametrize(
    "field,value,fragment",
    [
        ("benchmark_price_effective_at", "2025-01-01T21:00:00+00:00", "Entry"),
        ("asset_price_effective_at", "2025-02-02T16:00:00+00:00", "Outcome"),
    ],
)
def test_different_asset_and_benchmark_market_dates_are_rejected(
    tmp_path, field, value, fragment
):
    ledger, fill = complete_chain(tmp_path)
    records = ledger.observation_ledger.records()
    target = records[0] if field == "benchmark_price_effective_at" else records[1]
    target[field] = value
    from core.performance import outcome_observation as observation_module

    target["source_observation_sha256"] = observation_module._source_observation_hash(
        target
    )
    material = {key: item for key, item in target.items() if key != "record_hash"}
    target["record_hash"] = observation_module._record_hash(material)
    if len(records) == 2:
        records[1]["previous_hash"] = records[0]["record_hash"]
        second_material = {
            key: item for key, item in records[1].items() if key != "record_hash"
        }
        records[1]["record_hash"] = observation_module._record_hash(second_material)
    ledger.observation_ledger.path.write_text(
        "".join(json.dumps(item) + "\n" for item in records)
    )
    with pytest.raises((ValueError, LedgerIntegrityError), match=fragment):
        observe(ledger, fill)


def test_missing_due_horizon_observation_is_rejected(tmp_path):
    ledger, fill = complete_chain(tmp_path)
    with pytest.raises(ValueError, match="required"):
        observe(ledger, fill, horizon="3_MONTHS")


def test_backfill_is_explicit(tmp_path):
    ledger, fill = complete_chain(tmp_path)
    record = observe(ledger, fill, retrieved_at="2025-04-01T00:00:00+00:00")
    assert record["retrieval_mode"] == "BACKFILLED"


def test_identical_concurrent_retries_create_one_record(tmp_path):
    ledger, fill = complete_chain(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: observe(ledger, fill), range(2)))
    assert first == second
    assert len(ledger.verify()) == 1


def test_conflicting_evidence_for_same_interval_fails_closed(tmp_path):
    ledger, fill = complete_chain(tmp_path)
    observe(ledger, fill)
    with pytest.raises(LedgerIntegrityError, match="already exists"):
        observe(ledger, fill, gross_dividend_points="8.25")


def test_concurrent_conflicting_evidence_fails_closed(tmp_path):
    ledger, fill = complete_chain(tmp_path)

    def attempt(points):
        try:
            return observe(ledger, fill, gross_dividend_points=points)
        except LedgerIntegrityError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ["7.25", "8.25"]))

    assert sum(isinstance(item, dict) for item in results) == 1
    errors = [item for item in results if isinstance(item, LedgerIntegrityError)]
    assert len(errors) == 1
    assert "already exists" in str(errors[0])
    assert len(ledger.verify()) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("performance_claim", True),
        ("alpha_calculated", True),
        ("gross_dividend_points", "725"),
        ("interval_notation", "[START,END]"),
        ("entry_alignment_seconds", "999"),
        ("reinvestment_policy", "REINVESTED"),
        ("methodology_uri", "https://www.spglobal.com/forged.pdf"),
        ("benchmark_definition_sha256", "0" * 64),
        ("source_evidence_sha256", "0" * 64),
        ("horizon_label", "24 months"),
        ("decision_id", "DEC-FORGED"),
    ],
)
def test_rehashed_record_cannot_change_boundary_or_evidence(
    tmp_path, field, value
):
    ledger, fill = complete_chain(tmp_path)
    observe(ledger, fill)
    rewrite_with_valid_hash(ledger.path, **{field: value})
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        ledger.verify()


def test_explicit_tail_repair_preserves_partial_bytes(tmp_path):
    ledger, fill = complete_chain(tmp_path)
    first = observe(ledger, fill)
    with ledger.path.open("ab") as target:
        target.write(b'{"partial":')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial":'
    assert ledger.verify() == [first]
