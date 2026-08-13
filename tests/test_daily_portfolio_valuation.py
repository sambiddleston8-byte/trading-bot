from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker import LocalPaperExecutionLedger, PaperOrderProposalLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import (
    CorporateActionLedger,
    DailyMarketObservationLedger,
    DailyPortfolioValuationLedger,
    DailyPositionValueLedger,
    PaperPositionStateLedger,
    PortfolioFundingLedger,
)


IDENTITY = {
    "strategy_version": "strategy-v1",
    "model_versions": [
        {
            "component": "portfolio",
            "model": None,
            "parameters": {},
            "prompt_version": None,
            "provider": "rules-based",
            "version": "1.0",
        }
    ],
    "git_revision": "abc123",
}


def fraction(numerator, denominator=1):
    return {"numerator": str(numerator), "denominator": str(denominator)}


class CashFlowStub:
    def __init__(self, values=()):
        self.values = list(values)

    def verify(self):
        return self.values


def cash_flow(amount=100, **overrides):
    value = {
        "flow_id": "PCF-1",
        "record_hash": "flow-hash",
        "portfolio_version": "PORT-001",
        "valuation_id": "PVAL-BOUNDARY",
        "effective_at": "2025-02-03T20:00:00+00:00",
        "recorded_at": "2025-02-03T20:01:00+00:00",
        "exact_signed_amount": fraction(amount),
        **IDENTITY,
    }
    value.update(overrides)
    return value


def chain(
    tmp_path,
    *,
    funding_amount="1000",
    include_bbb_value=True,
    second_side="BUY",
    second_filled_at="2025-01-02T15:02:00+00:00",
    flows=(),
    include_gross_dividend=False,
    sell_aaa_quantity=None,
):
    proposals = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    proposal_inputs = [
        ("PORD-AAA", "AAA", 2, 100, 0.2, "BUY"),
        ("PORD-BBB", "BBB", 3, 50, 0.15, second_side),
    ]
    if sell_aaa_quantity is not None:
        proposal_inputs.append(
            ("PORD-AAA-SELL", "AAA", sell_aaa_quantity, 115, 0.1, "SELL")
        )
    for order, ticker, quantity, price, weight, side in proposal_inputs:
        proposals.propose(
            decision_id=f"DEC-{ticker}",
            portfolio_version="PORT-001",
            ticker=ticker,
            side=side,
            quantity=quantity,
            reference_price=price,
            target_weight=weight,
            created_at="2025-01-02T15:00:00+00:00",
            order_id=order,
            **IDENTITY,
        )
    funding = PortfolioFundingLedger(tmp_path / "funding.jsonl", proposals)
    funding.record_initial_funding(
        portfolio_version="PORT-001",
        amount=funding_amount,
        effective_at="2025-01-02T14:58:00+00:00",
        recorded_at="2025-01-02T14:59:00+00:00",
    )
    executions = LocalPaperExecutionLedger(tmp_path / "fills.jsonl", proposals)
    fill_records = [
        executions.simulate_full_fill(
            order_id="PORD-AAA", fill_price=101, fees=2, filled_at="2025-01-02T15:01:00+00:00"
        ),
        executions.simulate_full_fill(
            order_id="PORD-BBB", fill_price=51, fees=1, filled_at=second_filled_at
        ),
    ]
    if sell_aaa_quantity is not None:
        fill_records.append(
            executions.simulate_full_fill(
                order_id="PORD-AAA-SELL",
                fill_price=116,
                fees=1,
                filled_at="2025-01-10T15:00:00+00:00",
            )
        )
    closes = DailyMarketObservationLedger(tmp_path / "closes.jsonl", executions)
    actions = CorporateActionLedger(tmp_path / "actions.jsonl", executions)
    position_values = DailyPositionValueLedger(tmp_path / "position_values.jsonl", closes, actions)
    prices = {"AAA": "110", "BBB": "55"}
    created_values = []
    for index, fill in enumerate(fill_records):
        if fill["side"] != "BUY":
            continue
        if fill["ticker"] == "BBB" and not include_bbb_value:
            continue
        close = closes.observe(
            fill_id=fill["fill_id"],
            market_session_date="2025-02-03",
            close_price=prices[fill["ticker"]],
            price_effective_at="2025-02-03T21:00:00+00:00",
            retrieved_at="2025-02-03T21:01:00+00:00",
            recorded_at="2025-02-03T21:02:00+00:00",
            provider="TEST",
            source_version="v1",
            source_uri=f"https://provider.example/{fill['ticker']}/2025-02-03",
            source_input_sha256=("a" if index == 0 else "b") * 64,
        )
        action_events = (
            [
                {
                    "event_type": "CASH_DIVIDEND",
                    "source_event_id": "div-aaa",
                    "ex_at": "2025-01-20T00:00:00+00:00",
                    "payment_at": "2025-01-31T00:00:00+00:00",
                    "amount_per_share": "1",
                    "currency": "USD",
                }
            ]
            if include_gross_dividend and fill["ticker"] == "AAA"
            else []
        )
        actions.record(
            fill_id=fill["fill_id"],
            covers_from_at=fill["filled_at"],
            through_at="2025-02-03T21:00:00+00:00",
            retrieved_at="2025-02-03T21:03:00+00:00",
            data_source="TEST",
            source_version="v1",
            source_input_sha256=("c" if index == 0 else "d") * 64,
            events=action_events,
        )
        created_values.append(
            position_values.calculate(
                observation_id=close["observation_id"],
                calculated_at="2025-02-03T21:04:00+00:00",
            )
        )
    valuations = DailyPortfolioValuationLedger(
        tmp_path / "daily_portfolio.jsonl",
        position_values,
        funding,
        CashFlowStub(flows),
        PaperPositionStateLedger(tmp_path / "position_state.jsonl", executions),
    )
    return valuations, position_values, funding, fill_records, created_values


def calculate(valuations, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "market_session_date": "2025-02-03",
        "calculated_at": "2025-02-03T21:05:00+00:00",
    }
    values.update(overrides)
    return valuations.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import daily_portfolio_valuation as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_reconciles_exact_daily_portfolio_value_and_cash(tmp_path):
    valuations, _, funding, fills, values = chain(tmp_path)
    result = calculate(valuations)
    assert result["scope"] == "SIMULATED_GROSS_PRE_TAX_DAILY_PORTFOLIO_VALUATION"
    assert result["initial_funding"] == "1000"
    assert result["total_recorded_entry_cost"] == "358"
    assert result["total_recorded_entry_fees"] == "3"
    assert result["remaining_cash"] == "642"
    assert result["total_position_market_value"] == "385"
    assert result["total_equity"] == "1027"
    assert result["exact_fractions"]["cash_weight"] == fraction(642, 1027)
    assert result["exact_fractions"]["weight_total"] == fraction(1)
    assert result["funding_record_hash"] == funding.funding_for("PORT-001")["record_hash"]
    assert result["supporting_fill_ids"] == sorted(item["fill_id"] for item in fills)
    assert result["supporting_position_value_ids"] == sorted(
        (item["result_id"] for item in values),
        key=lambda item: next(v["fill_id"] for v in values if v["result_id"] == item),
    )
    assert result["daily_portfolio_valuation_calculated"] is True
    assert result["broker_cash_reconciled"] is False
    assert result["tax_and_withholding_modelled"] is False
    assert result["portfolio_return_calculated"] is False
    assert result["performance_metric_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["fill_inclusion_policy"] == "FILLED_AT_OR_BEFORE_EXACT_SESSION_CLOSE"
    assert result["previous_hash"] == GENESIS_HASH
    assert valuations.verify() == [result]


def test_external_contribution_changes_cash_and_equity_only(tmp_path):
    valuations, _, _, _, _ = chain(tmp_path, flows=[cash_flow(100)])
    result = calculate(valuations)
    assert result["remaining_cash"] == "742"
    assert result["total_position_market_value"] == "385"
    assert result["total_equity"] == "1127"
    assert result["cumulative_external_cash_flow"] == "100"


def test_gross_pre_tax_dividend_is_explicitly_reconciled_without_net_cash_claim(tmp_path):
    valuations, _, _, _, _ = chain(tmp_path, include_gross_dividend=True)
    result = calculate(valuations)
    assert result["cumulative_gross_usd_dividend_cash_pre_tax"] == "2"
    assert result["remaining_cash"] == "644"
    assert result["total_position_market_value"] == "385"
    assert result["total_equity"] == "1029"
    assert result["broker_cash_reconciled"] is False
    assert result["tax_and_withholding_modelled"] is False
    assert valuations.verify() == [result]


def test_missing_position_value_fails_closed(tmp_path):
    valuations, _, _, _, _ = chain(tmp_path, include_bbb_value=False)
    result = calculate(valuations)
    assert result["status"] == "NOT_CALCULABLE"
    assert "every historical buy fill" in " ".join(result["reasons"]).lower()


def test_oversell_without_an_open_position_fails_closed(tmp_path):
    valuations, _, _, _, _ = chain(tmp_path, second_side="SELL")
    result = calculate(valuations)
    assert result["status"] == "NOT_CALCULABLE"
    assert "exceeds the cumulative open paper position" in " ".join(result["reasons"])


def test_partial_sell_reconciles_remaining_quantity_value_and_cash(tmp_path):
    valuations, _, _, fills, _ = chain(tmp_path, sell_aaa_quantity=1)
    result = calculate(valuations)
    assert result["status"] == "CALCULATED"
    aaa = next(item for item in result["positions"] if item["ticker"] == "AAA")
    assert aaa["remaining_quantity"] == "1"
    assert aaa["position_market_value"] == "110"
    assert result["position_state_net_trade_cash_flow"] == "-243"
    assert result["remaining_cash"] == "757"
    assert result["total_position_market_value"] == "275"
    assert result["total_equity"] == "1032"
    sell = next(item for item in fills if item["side"] == "SELL")
    assert sell["fill_id"] in result["supporting_fill_ids"]
    assert valuations.verify() == [result]


def test_sell_with_any_historical_dividend_event_fails_closed(tmp_path):
    valuations, _, _, _, _ = chain(
        tmp_path,
        sell_aaa_quantity=1,
        include_gross_dividend=True,
    )
    result = calculate(valuations)
    assert result["status"] == "NOT_CALCULABLE"
    assert "event-aware lot accounting" in " ".join(result["reasons"])


@pytest.mark.parametrize("future_side", ["BUY", "SELL"])
def test_future_fill_cannot_change_or_block_prior_daily_valuation(tmp_path, future_side):
    valuations, _, _, fills, values = chain(
        tmp_path,
        include_bbb_value=False,
        second_side=future_side,
        second_filled_at="2025-02-04T15:02:00+00:00",
    )
    result = calculate(valuations)
    first_fill = next(item for item in fills if item["ticker"] == "AAA")
    assert result["status"] == "CALCULATED"
    assert result["supporting_fill_ids"] == [first_fill["fill_id"]]
    assert result["supporting_position_value_ids"] == [values[0]["result_id"]]
    assert result["total_recorded_entry_cost"] == "204"
    assert result["remaining_cash"] == "796"
    assert result["total_position_market_value"] == "220"
    assert result["total_equity"] == "1016"
    assert valuations.verify() == [result]


def test_insufficient_funding_fails_closed(tmp_path):
    valuations, _, _, _, _ = chain(tmp_path, funding_amount="300")
    result = calculate(valuations)
    assert result["status"] == "NOT_CALCULABLE"
    assert "cash cannot be negative" in " ".join(result["reasons"])


def test_identity_mismatched_flow_fails_closed(tmp_path):
    flow = cash_flow(100, git_revision="different")
    valuations, _, _, _, _ = chain(tmp_path, flows=[flow])
    result = calculate(valuations)
    assert result["status"] == "NOT_CALCULABLE"
    assert "strategy, model and Git identity" in " ".join(result["reasons"])


def test_identical_concurrent_retries_create_one_record(tmp_path):
    valuations, _, _, _, _ = chain(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(valuations), range(2)))
    assert first == second
    assert len(valuations.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"remaining_cash": "999"},
        {"daily_portfolio_valuation_calculated": False},
        {"broker_cash_reconciled": True},
        {"tax_and_withholding_modelled": True},
        {"portfolio_return_calculated": True},
        {"performance_metric_calculated": True},
        {"recommendation_provided": True},
        {"risk_adjusted": True},
        {"annualized": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"funding_record_hash": "forged"},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    valuations, _, _, _, _ = chain(tmp_path)
    calculate(valuations)
    rewrite_with_valid_hash(valuations.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        valuations.verify()


def test_supporting_position_value_tampering_is_detected(tmp_path):
    valuations, position_values, _, _, _ = chain(tmp_path)
    calculate(valuations)
    records = position_values.path.read_text().splitlines()
    item = json.loads(records[0])
    item["record_hash"] = "changed"
    records[0] = json.dumps(item)
    position_values.path.write_text("\n".join(records) + "\n")
    with pytest.raises(LedgerIntegrityError):
        valuations.verify()


def test_later_cash_flow_does_not_invalidate_pinned_history(tmp_path):
    flows = CashFlowStub()
    valuations, position_values, funding, _, _ = chain(tmp_path)
    valuations.cash_flow_ledger = flows
    result = calculate(valuations)
    flows.values.append(cash_flow(100))
    assert valuations.verify() == [result]


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    valuations, _, _, _, _ = chain(tmp_path)
    result = calculate(valuations)
    with valuations.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        valuations.verify()
    backup = valuations.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert valuations.verify() == [result]
