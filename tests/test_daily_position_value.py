from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker import LocalPaperExecutionLedger, PaperOrderProposalLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import (
    CorporateActionLedger,
    DailyMarketObservationLedger,
    DailyPositionValueLedger,
)


def events(**dividend_overrides):
    dividend = {
        "event_type": "CASH_DIVIDEND",
        "source_event_id": "div-1",
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


def chain(tmp_path, *, side="BUY", action_events=(), completeness="COMPLETE", reasons=()):
    proposals = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    proposals.propose(
        decision_id="DEC-001",
        portfolio_version="PORT-001",
        ticker="AAA",
        side=side,
        quantity=2,
        reference_price=100,
        target_weight=0.2,
        strategy_version="strategy-v1",
        model_versions=[{"component": "portfolio", "version": "1.0"}],
        created_at="2025-01-02T15:00:00+00:00",
        git_revision="abc123",
        order_id="PORD-001",
    )
    executions = LocalPaperExecutionLedger(tmp_path / "fills.jsonl", proposals)
    fill = executions.simulate_full_fill(
        order_id="PORD-001", fill_price=101, fees=2, filled_at="2025-01-02T15:01:00+00:00"
    )
    closes = DailyMarketObservationLedger(tmp_path / "closes.jsonl", executions)
    close = closes.observe(
        fill_id=fill["fill_id"],
        market_session_date="2025-02-03",
        close_price="60",
        price_effective_at="2025-02-03T21:00:00+00:00",
        retrieved_at="2025-02-03T21:01:00+00:00",
        recorded_at="2025-02-03T21:02:00+00:00",
        provider="TEST",
        source_version="v1",
        source_uri="https://provider.example/AAA/2025-02-03",
        source_input_sha256="a" * 64,
    )
    actions = CorporateActionLedger(tmp_path / "actions.jsonl", executions)
    evidence = actions.record(
        fill_id=fill["fill_id"],
        covers_from_at=fill["filled_at"],
        through_at="2025-02-03T21:00:00+00:00",
        retrieved_at="2025-02-03T21:03:00+00:00",
        data_source="TEST",
        source_version="v1",
        source_input_sha256="b" * 64,
        events=action_events,
        completeness_status=completeness,
        uncertainty_reasons=reasons,
    )
    values = DailyPositionValueLedger(tmp_path / "values.jsonl", closes, actions)
    return values, closes, actions, fill, close, evidence


def calculate(values, close, **overrides):
    inputs = {
        "observation_id": close["observation_id"],
        "calculated_at": "2025-02-03T21:04:00+00:00",
    }
    inputs.update(overrides)
    return values.calculate(**inputs)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import daily_position_value as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_exact_split_and_paid_dividend_position_value(tmp_path):
    values, _, _, fill, close, evidence = chain(tmp_path, action_events=events())
    result = calculate(values, close)
    assert result["scope"] == "SIMULATED_CORPORATE_ACTION_COMPLETE_DAILY_POSITION_VALUE"
    assert result["initial_quantity"] == "2"
    assert result["split_adjusted_quantity"] == "4"
    assert result["official_unadjusted_close"] == "60"
    assert result["position_market_value"] == "240"
    assert result["cumulative_gross_dividend_cash"] == "6"
    assert result["gross_holding_value"] == "246"
    assert result["exact_fractions"]["gross_holding_value"] == {"numerator": "246", "denominator": "1"}
    assert result["execution_record_hash"] == fill["record_hash"]
    assert result["observation_record_hash"] == close["record_hash"]
    assert result["corporate_action_record_hash"] == evidence["record_hash"]
    assert result["daily_portfolio_valuation_calculated"] is False
    assert result["performance_metric_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert values.verify() == [result]


def test_no_event_value_uses_original_quantity(tmp_path):
    values, _, _, _, close, _ = chain(tmp_path)
    result = calculate(values, close)
    assert result["split_adjusted_quantity"] == "2"
    assert result["position_market_value"] == "120"
    assert result["cumulative_gross_dividend_cash"] == "0"
    assert result["gross_holding_value"] == "120"


@pytest.mark.parametrize(
    "action_events,completeness,reasons,fragment",
    [
        ((), "UNCERTAIN", ["provider incomplete"], "COMPLETE"),
        ([{"event_type": "OTHER", "source_event_id": "spin", "effective_at": "2025-01-20T00:00:00+00:00", "description": "Spin-off"}], "COMPLETE", (), "unsupported"),
        (events(payment_at=None), "COMPLETE", (), "payment_at"),
        (events(payment_at="2025-02-10T00:00:00+00:00"), "COMPLETE", (), "not paid"),
        (events(currency="GBP"), "COMPLETE", (), "USD"),
        (events(ex_at="2025-01-15T00:00:00+00:00"), "COMPLETE", (), "ordering"),
    ],
)
def test_uncertain_or_unsupported_accounting_fails_closed(
    tmp_path, action_events, completeness, reasons, fragment
):
    values, _, _, _, close, _ = chain(
        tmp_path, action_events=action_events, completeness=completeness, reasons=reasons
    )
    result = calculate(values, close)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert values.records() == []


def test_sell_fill_is_not_silently_treated_as_long_position(tmp_path):
    values, _, _, _, close, _ = chain(tmp_path, side="SELL")
    result = calculate(values, close)
    assert result["status"] == "NOT_CALCULABLE"
    assert "long BUY" in " ".join(result["reasons"])


def test_missing_close_observation_fails_closed(tmp_path):
    values, _, _, _, _, _ = chain(tmp_path)
    result = values.calculate(observation_id="MISSING")
    assert result["status"] == "NOT_CALCULABLE"
    assert "daily close" in " ".join(result["reasons"])


def test_identical_concurrent_retries_create_one_record(tmp_path):
    values, _, _, _, close, _ = chain(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(values, close), range(2)))
    assert first == second
    assert len(values.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"position_market_value": "999"},
        {"daily_position_value_calculated": False},
        {"daily_portfolio_valuation_calculated": True},
        {"performance_metric_calculated": True},
        {"recommendation_provided": True},
        {"risk_adjusted": True},
        {"annualized": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"observation_record_hash": "forged"},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    values, _, _, _, close, _ = chain(tmp_path)
    calculate(values, close)
    rewrite_with_valid_hash(values.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        values.verify()


def test_supporting_close_tampering_is_detected(tmp_path):
    values, closes, _, _, close, _ = chain(tmp_path)
    calculate(values, close)
    records = closes.path.read_text().splitlines()
    item = json.loads(records[0])
    item["record_hash"] = "changed"
    closes.path.write_text(json.dumps(item) + "\n")
    with pytest.raises(LedgerIntegrityError):
        values.verify()


def test_supporting_corporate_action_tampering_is_detected(tmp_path):
    values, _, actions, _, close, _ = chain(tmp_path, action_events=events())
    calculate(values, close)
    records = actions.path.read_text().splitlines()
    item = json.loads(records[0])
    item["record_hash"] = "changed-corporate-action-hash"
    actions.path.write_text(json.dumps(item) + "\n")
    with pytest.raises(LedgerIntegrityError):
        values.verify()


def test_later_consistent_action_coverage_does_not_invalidate_history(tmp_path):
    values, _, actions, fill, close, _ = chain(tmp_path, action_events=events())
    result = calculate(values, close)
    actions.record(
        fill_id=fill["fill_id"],
        covers_from_at=fill["filled_at"],
        through_at="2025-03-03T21:00:00+00:00",
        retrieved_at="2025-03-03T21:03:00+00:00",
        data_source="TEST",
        source_version="v1",
        source_input_sha256="c" * 64,
        events=events(),
    )
    assert values.verify() == [result]


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    values, _, _, _, close, _ = chain(tmp_path)
    result = calculate(values, close)
    with values.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        values.verify()
    backup = values.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert values.verify() == [result]
