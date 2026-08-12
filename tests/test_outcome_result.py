from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker import LocalPaperExecutionLedger, PaperOrderProposalLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import OutcomeObservationLedger, OutcomeResultLedger


def complete_chain(tmp_path, *, side="BUY", basis="UNADJUSTED_CLOSE"):
    proposals = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    proposals.propose(
        decision_id="DEC-001",
        portfolio_version="PORT-001",
        ticker="NVDA",
        side=side,
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
    observations = OutcomeObservationLedger(
        tmp_path / "observations.jsonl", executions
    )
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
    if basis == "UNADJUSTED_CLOSE":
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
    return OutcomeResultLedger(tmp_path / "results.jsonl", observations), fill


def action_check(**overrides):
    values = {
        "status": "NO_EVENTS",
        "data_source": "TEST_CORPORATE_ACTION_FIXTURE",
        "source_version": "fixture-v1",
        "covers_from_at": "2025-01-02T15:01:00+00:00",
        "through_at": "2025-02-03T16:00:00+00:00",
        "checked_at": "2025-02-03T17:00:00+00:00",
    }
    values.update(overrides)
    return values


def calculate(ledger, fill, **overrides):
    values = {
        "fill_id": fill["fill_id"],
        "horizon": "1_MONTH",
        "corporate_action_check": action_check(),
        "calculated_at": "2025-02-03T17:01:00+00:00",
    }
    values.update(overrides)
    return ledger.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import outcome_result as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_calculates_disclosed_decimal_price_returns_and_recorded_fee_effect(tmp_path):
    ledger, fill = complete_chain(tmp_path)
    record = calculate(ledger, fill)

    assert record["scope"] == "PRICE_RETURN_ONLY"
    assert record["return_unit"] == "DECIMAL"
    assert record["portfolio_performance_claim"] is False
    assert record["total_return_claim"] is False
    assert record["learning_eligible"] is False
    assert record["asset_price_return"] == pytest.approx(111 / 101 - 1)
    assert record["benchmark_price_return"] == pytest.approx(6_018 / 5_900 - 1)
    assert record["benchmark_relative_price_return"] == pytest.approx(
        (111 / 101 - 1) - (6_018 / 5_900 - 1)
    )
    assert record["recorded_entry_cost"] == 204
    assert record["gross_outcome_value"] == 222
    assert record["benchmark_ticker"] == "^GSPC"
    assert record["entry_fee_adjusted_long_return_excl_exit"] == pytest.approx(18 / 204)
    assert record["formula"]["exit_fee_policy"] == "NOT_INCLUDED_NO_EXIT_EXECUTION_RECORDED"
    assert record["formula"]["slippage_policy"] == "ENTRY_SLIPPAGE_ALREADY_EMBEDDED_IN_FILL_PRICE"
    assert record["previous_hash"] == GENESIS_HASH
    assert ledger.verify() == [record]


def test_missing_horizon_returns_not_calculable_without_appending(tmp_path):
    ledger, fill = complete_chain(tmp_path, basis="ADJUSTED_CLOSE")
    result = calculate(ledger, fill)
    assert result["status"] == "NOT_CALCULABLE"
    assert result["record_appended"] is False
    assert "missing" in " ".join(result["reasons"]).lower()
    assert ledger.records() == []


def test_entry_is_never_an_outcome_horizon(tmp_path):
    ledger, fill = complete_chain(tmp_path)
    result = calculate(ledger, fill, horizon="ENTRY")
    assert result["status"] == "NOT_CALCULABLE"
    assert "baseline" in " ".join(result["reasons"])


def test_sell_fill_is_not_misrepresented_as_long_return(tmp_path):
    ledger, fill = complete_chain(tmp_path, side="SELL")
    result = calculate(ledger, fill)
    assert result["status"] == "NOT_CALCULABLE"
    assert "BUY" in " ".join(result["reasons"])


@pytest.mark.parametrize(
    "check,fragment",
    [
        (action_check(status="EVENTS_PRESENT"), "NO_EVENTS"),
        (action_check(covers_from_at="2025-01-03T00:00:00+00:00"), "entry"),
        (action_check(through_at="2025-02-01T00:00:00+00:00"), "cover"),
        (action_check(checked_at="2025-02-01T00:00:00+00:00"), "predate"),
        (action_check(data_source=""), "data_source"),
    ],
)
def test_corporate_action_uncertainty_blocks_calculation(tmp_path, check, fragment):
    ledger, fill = complete_chain(tmp_path)
    result = calculate(ledger, fill, corporate_action_check=check)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert ledger.records() == []


def test_identical_and_concurrent_retries_create_one_result(tmp_path):
    ledger, fill = complete_chain(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(ledger, fill), range(2)))
    assert first == second
    assert len(ledger.verify()) == 1


def test_conflicting_retry_fails_closed(tmp_path):
    ledger, fill = complete_chain(tmp_path)
    calculate(ledger, fill)
    with pytest.raises(LedgerIntegrityError, match="exists"):
        calculate(
            ledger,
            fill,
            corporate_action_check=action_check(source_version="fixture-v2"),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", "TOTAL_RETURN"),
        ("portfolio_performance_claim", True),
        ("learning_eligible", True),
        ("asset_price_return", 99.0),
        ("decision_id", "DEC-FORGED"),
        ("entry_price", 1.0),
        ("recorded_entry_fee", 0.0),
        ("recorded_entry_slippage_bps", 999.0),
        ("benchmark_ticker", "QQQ"),
        ("formula", {}),
    ],
)
def test_rehashed_result_cannot_change_boundary_or_calculations(
    tmp_path, field, value
):
    ledger, fill = complete_chain(tmp_path)
    calculate(ledger, fill)
    rewrite_with_valid_hash(ledger.path, **{field: value})
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        ledger.verify()


def test_calculation_time_cannot_predate_evidence_or_be_in_future(tmp_path):
    ledger, fill = complete_chain(tmp_path)
    before = calculate(ledger, fill, calculated_at="2025-02-03T16:30:00+00:00")
    assert before["status"] == "NOT_CALCULABLE"
    assert "predate" in " ".join(before["reasons"])

    future = calculate(ledger, fill, calculated_at="2030-01-01T00:00:00+00:00")
    assert future["status"] == "NOT_CALCULABLE"
    assert "future" in " ".join(future["reasons"])


def test_explicit_tail_repair_preserves_partial_bytes(tmp_path):
    ledger, fill = complete_chain(tmp_path)
    first = calculate(ledger, fill)
    with ledger.path.open("ab") as target:
        target.write(b'{"partial":')

    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial":'
    assert ledger.verify() == [first]
