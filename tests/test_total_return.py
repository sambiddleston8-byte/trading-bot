from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker import LocalPaperExecutionLedger, PaperOrderProposalLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import (
    CorporateActionLedger,
    OutcomeObservationLedger,
    TotalReturnLedger,
)


def complete_chain(tmp_path, *, side="BUY", events=(), completeness="COMPLETE", reasons=()):
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
        asset_price=60,
        benchmark_price=6_018,
        asset_price_effective_at="2025-02-03T16:00:00+00:00",
        benchmark_price_effective_at="2025-02-03T16:00:00+00:00",
        retrieved_at="2025-02-03T17:00:00+00:00",
        data_source="TEST_FIXTURE",
        source_version="fixture-v1",
        market_price_basis="UNADJUSTED_CLOSE",
    )
    actions = CorporateActionLedger(tmp_path / "actions.jsonl", executions)
    evidence = actions.record(
        fill_id=fill["fill_id"],
        covers_from_at="2025-01-02T15:01:00+00:00",
        through_at="2025-02-03T16:00:00+00:00",
        retrieved_at="2025-02-03T17:00:00+00:00",
        data_source="TEST_CORPORATE_ACTION_FIXTURE",
        source_version="fixture-v1",
        source_input_sha256="a" * 64,
        events=events,
        completeness_status=completeness,
        uncertainty_reasons=reasons,
    )
    ledger = TotalReturnLedger(tmp_path / "total_returns.jsonl", observations, actions)
    return ledger, fill, evidence


def split_and_dividend_events(**dividend_overrides):
    dividend = {
        "event_type": "CASH_DIVIDEND",
        "source_event_id": "dividend-1",
        "ex_at": "2025-01-20T00:00:00+00:00",
        "payment_at": "2025-01-31T00:00:00+00:00",
        "amount_per_share": "1.5",
        "currency": "USD",
    }
    dividend.update(dividend_overrides)
    return [
        {
            "event_type": "STOCK_SPLIT",
            "source_event_id": "split-1",
            "effective_at": "2025-01-15T00:00:00+00:00",
            "numerator": "2",
            "denominator": "1",
        },
        dividend,
    ]


def calculate(ledger, fill, **overrides):
    values = {
        "fill_id": fill["fill_id"],
        "horizon": "1_MONTH",
        "calculated_at": "2025-02-03T17:01:00+00:00",
    }
    values.update(overrides)
    return ledger.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import total_return as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_calculates_exact_split_adjusted_gross_total_return(tmp_path):
    ledger, fill, evidence = complete_chain(
        tmp_path, events=split_and_dividend_events()
    )
    result = calculate(ledger, fill)

    assert result["scope"] == "SIMULATED_LONG_HOLDING_PERIOD_TOTAL_RETURN"
    assert result["return_unit"] == "DECIMAL_STRING"
    assert result["simulation_only"] is True
    assert result["portfolio_performance_claim"] is False
    assert result["track_record_claim"] is False
    assert result["learning_eligible"] is False
    assert result["relative_total_return_calculated"] is False
    assert result["alpha_calculated"] is False
    assert result["initial_quantity"] == "2"
    assert result["split_adjusted_quantity"] == "4"
    assert result["recorded_entry_cost"] == "204"
    assert result["outcome_position_value"] == "240"
    assert result["gross_dividend_cash"] == "6"
    assert result["gross_outcome_value"] == "246"
    assert result["gross_total_return_after_entry_fee_excl_exit"] == (
        "0.2058823529411764705882352941176471"
    )
    assert result["benchmark_price_return_context"] == "0.02"
    assert result["exact_fractions"]["gross_total_return_after_entry_fee_excl_exit"] == {
        "numerator": "7",
        "denominator": "34",
    }
    assert result["benchmark_comparison_status"] == "PRICE_RETURN_ONLY_NOT_LIKE_FOR_LIKE"
    assert result["corporate_action_evidence_hash"] == evidence["record_hash"]
    assert result["previous_hash"] == GENESIS_HASH
    assert result["formula"]["cash_in_lieu_policy"] == "NOT_INCLUDED"
    assert ledger.verify() == [result]


def test_no_event_total_return_is_still_fee_adjusted_and_simulated(tmp_path):
    ledger, fill, _ = complete_chain(tmp_path)
    result = calculate(ledger, fill)
    assert result["split_adjusted_quantity"] == "2"
    assert result["gross_dividend_cash"] == "0"
    assert result["gross_outcome_value"] == "120"
    assert result["gross_total_return_after_entry_fee_excl_exit"] == (
        "-0.4117647058823529411764705882352941"
    )


@pytest.mark.parametrize(
    "events,completeness,reasons,fragment",
    [
        ((), "UNCERTAIN", ["provider incomplete"], "COMPLETE"),
        (
            [
                {
                    "event_type": "OTHER",
                    "source_event_id": "spinoff-1",
                    "effective_at": "2025-01-20T00:00:00+00:00",
                    "description": "Spin-off",
                }
            ],
            "COMPLETE",
            (),
            "unsupported",
        ),
        (
            split_and_dividend_events(payment_at=None),
            "COMPLETE",
            (),
            "payment_at",
        ),
        (
            split_and_dividend_events(currency="GBP"),
            "COMPLETE",
            (),
            "USD",
        ),
        (
            split_and_dividend_events(payment_at="2025-03-01T00:00:00+00:00"),
            "COMPLETE",
            (),
            "outcome time",
        ),
    ],
)
def test_uncertain_or_unsupported_accounting_fails_closed(
    tmp_path, events, completeness, reasons, fragment
):
    ledger, fill, _ = complete_chain(
        tmp_path, events=events, completeness=completeness, reasons=reasons
    )
    result = calculate(ledger, fill)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert ledger.records() == []


def test_simultaneous_split_and_dividend_fails_closed(tmp_path):
    events = split_and_dividend_events(ex_at="2025-01-15T00:00:00+00:00")
    ledger, fill, _ = complete_chain(tmp_path, events=events)
    result = calculate(ledger, fill)
    assert result["status"] == "NOT_CALCULABLE"
    assert "ordering" in " ".join(result["reasons"])


def test_dividend_paid_after_outcome_is_not_added_using_future_information(tmp_path):
    events = split_and_dividend_events(
        payment_at="2025-02-10T00:00:00+00:00"
    )
    ledger, fill, _ = complete_chain(tmp_path, events=events)
    result = calculate(
        ledger, fill, calculated_at="2025-02-15T00:00:00+00:00"
    )
    assert result["status"] == "NOT_CALCULABLE"
    assert "outcome time" in " ".join(result["reasons"])
    assert ledger.records() == []


def test_reverse_split_retains_exact_fractional_simulated_quantity(tmp_path):
    events = [
        {
            "event_type": "STOCK_SPLIT",
            "source_event_id": "reverse-split-1",
            "effective_at": "2025-01-15T00:00:00+00:00",
            "numerator": "1",
            "denominator": "3",
        }
    ]
    ledger, fill, _ = complete_chain(tmp_path, events=events)
    result = calculate(ledger, fill)
    assert result["split_adjusted_quantity"].startswith("0.666666666666666")
    assert result["exact_fractions"]["split_adjusted_quantity"] == {
        "numerator": "2",
        "denominator": "3",
    }
    assert result["gross_dividend_cash"] == "0"
    assert result["formula"]["fractional_share_policy"].startswith("RETAIN_EXACT")


def test_sell_and_entry_horizon_are_not_misrepresented(tmp_path):
    sell_ledger, sell_fill, _ = complete_chain(tmp_path / "sell", side="SELL")
    sell = calculate(sell_ledger, sell_fill)
    assert sell["status"] == "NOT_CALCULABLE"
    assert "BUY" in " ".join(sell["reasons"])

    ledger, fill, _ = complete_chain(tmp_path / "entry")
    entry = calculate(ledger, fill, horizon="ENTRY")
    assert entry["status"] == "NOT_CALCULABLE"
    assert "baseline" in " ".join(entry["reasons"])


def test_calculation_time_cannot_predate_evidence_or_be_in_future(tmp_path):
    ledger, fill, _ = complete_chain(tmp_path)
    early = calculate(ledger, fill, calculated_at="2025-02-03T16:30:00+00:00")
    assert early["status"] == "NOT_CALCULABLE"
    assert "predate" in " ".join(early["reasons"])

    future = calculate(ledger, fill, calculated_at="2030-01-01T00:00:00+00:00")
    assert future["status"] == "NOT_CALCULABLE"
    assert "future" in " ".join(future["reasons"])


def test_identical_concurrent_retries_create_one_result(tmp_path):
    ledger, fill, _ = complete_chain(tmp_path, events=split_and_dividend_events())
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(ledger, fill), range(2)))
    assert first == second
    assert len(ledger.verify()) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", "LIVE_TRACK_RECORD"),
        ("simulation_only", False),
        ("learning_eligible", True),
        ("alpha_calculated", True),
        ("gross_dividend_cash", "600"),
        ("split_adjusted_quantity", "2"),
        ("exact_fractions", {}),
        ("horizon_label", "24 months"),
        ("decision_id", "DEC-FORGED"),
        ("corporate_action_evidence_hash", "0" * 64),
        ("formula", {}),
    ],
)
def test_rehashed_result_cannot_change_boundary_or_economics(
    tmp_path, field, value
):
    ledger, fill, _ = complete_chain(tmp_path, events=split_and_dividend_events())
    calculate(ledger, fill)
    rewrite_with_valid_hash(ledger.path, **{field: value})
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        ledger.verify()


def test_conflicting_retry_fails_closed(tmp_path):
    ledger, fill, _ = complete_chain(tmp_path)
    calculate(ledger, fill)
    with pytest.raises(LedgerIntegrityError, match="already exists"):
        calculate(ledger, fill, calculated_at="2025-02-03T18:00:00+00:00", allow_existing=False)


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
