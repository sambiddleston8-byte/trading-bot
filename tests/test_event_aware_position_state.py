from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker import LocalPaperExecutionLedger, PaperOrderProposalLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import CorporateActionLedger, EventAwarePaperPositionStateLedger


IDENTITY = {
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "portfolio", "version": "1.0"}],
    "git_revision": "abc123",
}


def fraction(numerator, denominator=1):
    return {"numerator": str(numerator), "denominator": str(denominator)}


def chain(tmp_path, *, first_events=(), second_events=(), include_second=True):
    proposals = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    inputs = [
        ("PORD-BUY-1", "DEC-BUY-1", "BUY", 2, 100, "2025-01-02T14:59:00+00:00"),
    ]
    if include_second:
        inputs.extend(
            [
                ("PORD-BUY-2", "DEC-BUY-2", "BUY", 2, 110, "2025-01-11T14:59:00+00:00"),
                ("PORD-SELL", "DEC-SELL", "SELL", 3, 120, "2025-01-12T14:59:00+00:00"),
            ]
        )
    for order_id, decision_id, side, quantity, price, created_at in inputs:
        proposals.propose(
            decision_id=decision_id,
            portfolio_version="PORT-001",
            ticker="AAA",
            side=side,
            quantity=quantity,
            reference_price=price,
            target_weight=0.3,
            created_at=created_at,
            order_id=order_id,
            **IDENTITY,
        )
    executions = LocalPaperExecutionLedger(tmp_path / "fills.jsonl", proposals)
    fills = [
        executions.simulate_full_fill(
            order_id="PORD-BUY-1",
            fill_price=100,
            fees=1,
            filled_at="2025-01-02T15:00:00+00:00",
        )
    ]
    if include_second:
        fills.extend(
            [
                executions.simulate_full_fill(
                    order_id="PORD-BUY-2",
                    fill_price=110,
                    fees=2,
                    filled_at="2025-01-11T15:00:00+00:00",
                ),
                executions.simulate_full_fill(
                    order_id="PORD-SELL",
                    fill_price=120,
                    fees=3,
                    filled_at="2025-01-12T15:00:00+00:00",
                ),
            ]
        )
    actions = CorporateActionLedger(tmp_path / "actions.jsonl", executions)
    actions.record(
        fill_id=fills[0]["fill_id"],
        covers_from_at=fills[0]["filled_at"],
        through_at="2025-02-01T21:00:00+00:00",
        retrieved_at="2025-02-01T21:00:00+00:00",
        data_source="TEST",
        source_version="v1",
        source_input_sha256="a" * 64,
        events=first_events,
    )
    if include_second:
        actions.record(
            fill_id=fills[1]["fill_id"],
            covers_from_at=fills[1]["filled_at"],
            through_at="2025-02-01T21:00:00+00:00",
            retrieved_at="2025-02-01T21:00:00+00:00",
            data_source="TEST",
            source_version="v1",
            source_input_sha256="b" * 64,
            events=second_events,
        )
    target = EventAwarePaperPositionStateLedger(
        tmp_path / "event_positions.jsonl", executions, actions
    )
    return executions, actions, target, fills


def calculate(target, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "as_of": "2025-02-01T21:00:00+00:00",
        "calculated_at": "2025-02-01T21:01:00+00:00",
    }
    values.update(overrides)
    return target.calculate(**values)


def dividend(identifier="DIV-1", ex_at="2025-01-05T00:00:00+00:00", amount="1"):
    return {
        "event_type": "CASH_DIVIDEND",
        "source_event_id": identifier,
        "ex_at": ex_at,
        "payment_at": "2025-01-06T00:00:00+00:00",
        "amount_per_share": amount,
        "currency": "USD",
    }


def split(identifier="SPLIT-1", effective_at="2025-01-10T00:00:00+00:00"):
    return {
        "event_type": "STOCK_SPLIT",
        "source_event_id": identifier,
        "effective_at": effective_at,
        "numerator": "2",
        "denominator": "1",
    }


def rewrite_with_valid_hash(path, **changes):
    from core.performance import event_aware_position_state as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def append_buy(executions, actions, *, suffix, filled_at, with_evidence):
    created_at = filled_at.replace("15:00:00", "14:59:00")
    order_id = f"PORD-{suffix}"
    executions.proposal_ledger.propose(
        decision_id=f"DEC-{suffix}",
        portfolio_version="PORT-001",
        ticker="BBB",
        side="BUY",
        quantity=1,
        reference_price=50,
        target_weight=0.1,
        created_at=created_at,
        order_id=order_id,
        **IDENTITY,
    )
    item = executions.simulate_full_fill(
        order_id=order_id,
        fill_price=50,
        fees=0,
        filled_at=filled_at,
    )
    if with_evidence:
        actions.record(
            fill_id=item["fill_id"],
            covers_from_at=item["filled_at"],
            through_at="2025-02-01T21:00:00+00:00",
            retrieved_at="2025-02-01T21:00:00+00:00",
            data_source="TEST",
            source_version="v1",
            source_input_sha256="c" * 64,
        )
    return item


def test_processes_dividend_split_second_buy_and_fifo_sale_exactly(tmp_path):
    _, _, target, fills = chain(tmp_path, first_events=[dividend(), split()])
    result = calculate(target)
    position = result["open_positions"][0]
    assert position["open_quantity"] == "3"
    assert position["exact_open_quantity"] == fraction(3)
    assert [lot["buy_fill_id"] for lot in position["supporting_open_lots"]] == [
        fills[0]["fill_id"],
        fills[1]["fill_id"],
    ]
    assert [lot["remaining_quantity"] for lot in position["supporting_open_lots"]] == [
        "1",
        "2",
    ]
    assert result["net_trade_cash_flow"] == "-66"
    assert result["cumulative_paid_gross_usd_dividend_cash"] == "2"
    assert result["applied_corporate_actions"][0]["exact_entitled_quantity"] == fraction(2)
    assert result["applied_corporate_actions"][1]["exact_open_quantity_after"] == fraction(4)
    assert result["broker_cash_reconciled"] is False
    assert result["realized_profit_calculated"] is False
    assert result["performance_metric_calculated"] is False
    assert result["order_submission_enabled"] is False
    assert result["live_trading_enabled"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert target.verify() == [result]


def test_dividend_after_sale_uses_only_shares_open_at_ex_date(tmp_path):
    after_sale = dividend(ex_at="2025-01-15T00:00:00+00:00")
    after_sale["payment_at"] = "2025-01-20T00:00:00+00:00"
    _, _, target, _ = chain(
        tmp_path,
        first_events=[split(), after_sale],
        second_events=[after_sale],
    )
    result = calculate(target)
    assert result["open_positions"][0]["open_quantity"] == "3"
    assert result["cumulative_paid_gross_usd_dividend_cash"] == "3"
    dividend_event = result["applied_corporate_actions"][1]
    assert dividend_event["exact_entitled_quantity"] == fraction(3)


def test_duplicate_event_across_buy_evidence_is_applied_once(tmp_path):
    shared = dividend(ex_at="2025-01-15T00:00:00+00:00")
    shared["payment_at"] = "2025-01-20T00:00:00+00:00"
    _, _, target, _ = chain(tmp_path, first_events=[shared], second_events=[shared])
    result = calculate(target)
    assert result["cumulative_paid_gross_usd_dividend_cash"] == "1"
    assert len(result["applied_corporate_actions"]) == 1


def test_two_dividends_at_same_ex_time_are_both_allowed(tmp_path):
    events = [dividend("DIV-A", amount="1"), dividend("DIV-B", amount="0.5")]
    _, _, target, _ = chain(tmp_path, first_events=events, include_second=False)
    result = calculate(target)
    assert result["cumulative_paid_gross_usd_dividend_cash"] == "3"
    assert len(result["applied_corporate_actions"]) == 2


def test_split_simultaneous_with_another_action_fails_closed(tmp_path):
    events = [split(effective_at="2025-01-05T00:00:00+00:00"), dividend()]
    _, _, target, _ = chain(tmp_path, first_events=events, include_second=False)
    with pytest.raises(ValueError, match="simultaneous"):
        calculate(target)


def test_trade_at_exact_action_time_fails_closed(tmp_path):
    event = split(effective_at="2025-01-11T15:00:00+00:00")
    _, _, target, _ = chain(tmp_path, first_events=[event], second_events=[event])
    with pytest.raises(ValueError, match="same instant"):
        calculate(target)


@pytest.mark.parametrize(
    "mutation,fragment",
    [
        (lambda event: event.update(currency="GBP"), "USD"),
        (lambda event: event.update(payment_at=None), "paid"),
        (lambda event: event.update(payment_at="2025-03-01T00:00:00+00:00"), "paid"),
    ],
)
def test_unsupported_dividend_terms_fail_closed(tmp_path, mutation, fragment):
    event = dividend()
    mutation(event)
    _, _, target, _ = chain(tmp_path, first_events=[event], include_second=False)
    with pytest.raises(ValueError, match=fragment):
        calculate(target)


def test_missing_complete_evidence_fails_closed(tmp_path):
    executions, actions, _, fills = chain(tmp_path)
    actions.path.write_text(actions.path.read_text().splitlines()[0] + "\n")
    target = EventAwarePaperPositionStateLedger(
        tmp_path / "missing.jsonl", executions, actions
    )
    with pytest.raises(ValueError, match="missing"):
        calculate(target)
    assert fills[1]["side"] == "BUY"


def test_conflicting_same_source_event_across_buy_evidence_fails_closed(tmp_path):
    first = dividend("SHARED", ex_at="2025-01-15T00:00:00+00:00", amount="1")
    first["payment_at"] = "2025-01-20T00:00:00+00:00"
    second = dict(first, amount_per_share="2")
    _, _, target, _ = chain(tmp_path, first_events=[first], second_events=[second])
    with pytest.raises(ValueError, match="conflict"):
        calculate(target)


def test_missing_or_renamed_event_in_overlapping_buy_evidence_fails_closed(tmp_path):
    first = dividend("FIRST", ex_at="2025-01-15T00:00:00+00:00", amount="1")
    first["payment_at"] = "2025-01-20T00:00:00+00:00"
    renamed = dict(first, source_event_id="RENAMED", amount_per_share="2")
    _, _, target, _ = chain(tmp_path, first_events=[first], second_events=[renamed])
    with pytest.raises(ValueError, match="overlapping"):
        calculate(target)


def test_later_backdated_fill_creates_new_revision_without_rewriting_history(tmp_path):
    executions, actions, target, _ = chain(tmp_path, include_second=False)
    original = calculate(target)
    backdated = append_buy(
        executions,
        actions,
        suffix="BACKDATED",
        filled_at="2025-01-20T15:00:00+00:00",
        with_evidence=True,
    )
    assert target.verify() == [original]
    revised = calculate(target, calculated_at="2025-02-01T21:02:00+00:00")
    assert revised["snapshot_id"] != original["snapshot_id"]
    assert backdated["fill_id"] in revised["supporting_fill_ids"]
    assert [position["ticker"] for position in revised["open_positions"]] == ["AAA", "BBB"]
    assert target.verify() == [original, revised]


def test_later_future_fill_does_not_create_needless_as_of_revision(tmp_path):
    executions, actions, target, _ = chain(tmp_path, include_second=False)
    original = calculate(target)
    append_buy(
        executions,
        actions,
        suffix="FUTURE",
        filled_at="2025-02-02T15:00:00+00:00",
        with_evidence=False,
    )
    retry = calculate(target, calculated_at="2025-02-01T21:02:00+00:00")
    assert retry == original
    assert target.verify() == [original]


def test_changed_pinned_fill_blocks_verification(tmp_path):
    from core.broker import local_paper_execution as execution_module
    from core.performance import corporate_action as action_module

    executions, actions, target, _ = chain(tmp_path, include_second=False)
    calculate(target)
    fill = json.loads(executions.path.read_text())
    fill["source_revision_note"] = "validly rehashed later source revision"
    fill_material = {key: value for key, value in fill.items() if key != "record_hash"}
    fill["record_hash"] = execution_module._record_hash(fill_material)
    executions.path.write_text(json.dumps(fill) + "\n")

    evidence = json.loads(actions.path.read_text())
    evidence["execution_record_hash"] = fill["record_hash"]
    evidence_material = {
        key: value for key, value in evidence.items() if key != "record_hash"
    }
    evidence["record_hash"] = action_module._record_hash(evidence_material)
    actions.path.write_text(json.dumps(evidence) + "\n")

    assert executions.verify() == [fill]
    assert actions.verify() == [evidence]
    with pytest.raises(LedgerIntegrityError, match="source prefix"):
        target.verify()


@pytest.mark.parametrize(
    "changes",
    [
        {"open_position_count": 99},
        {"source_fill_count": 99},
        {"cumulative_paid_gross_usd_dividend_cash": "999"},
        {"supporting_corporate_action_record_hashes": ["forged"]},
        {"broker_cash_reconciled": True},
        {"tax_and_withholding_modelled": True},
        {"realized_profit_calculated": True},
        {"performance_metric_calculated": True},
        {"recommendation_provided": True},
        {"order_submission_enabled": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    _, _, target, _ = chain(tmp_path, first_events=[dividend(), split()])
    calculate(target)
    rewrite_with_valid_hash(target.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_identical_concurrent_retries_create_one_snapshot(tmp_path):
    _, _, target, _ = chain(tmp_path, first_events=[dividend(), split()])
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(target), range(2)))
    assert first == second
    assert len(target.verify()) == 1


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    _, _, target, _ = chain(tmp_path, first_events=[dividend(), split()])
    result = calculate(target)
    with target.path.open("ab") as output:
        output.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        target.verify()
    backup = target.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert target.verify() == [result]
