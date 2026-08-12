from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker import LocalPaperExecutionLedger, PaperOrderProposalLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import (
    CorporateActionLedger,
    OutcomeObservationLedger,
    PortfolioFundingLedger,
    SimulatedPortfolioValuationLedger,
    TotalReturnLedger,
)


def complete_chain(
    tmp_path,
    *,
    funding_amount="1000",
    include_second_fill=True,
    include_second_return=True,
    second_outcome_at="2025-02-03T16:00:00+00:00",
    target_weights=(0.20, 0.15),
):
    proposals = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    proposal_values = [
        {
            "decision_id": "DEC-NVDA",
            "ticker": "NVDA",
            "quantity": 2,
            "reference_price": 100,
            "target_weight": target_weights[0],
            "order_id": "PORD-NVDA",
        },
        {
            "decision_id": "DEC-AAPL",
            "ticker": "AAPL",
            "quantity": 3,
            "reference_price": 50,
            "target_weight": target_weights[1],
            "order_id": "PORD-AAPL",
        },
    ]
    for values in proposal_values:
        proposals.propose(
            portfolio_version="PORT-001",
            side="BUY",
            strategy_version="strategy-v1",
            model_versions=[{"component": "portfolio", "version": "1.0"}],
            created_at="2025-01-02T15:00:00+00:00",
            git_revision="abc123",
            **values,
        )
    funding = PortfolioFundingLedger(tmp_path / "funding.jsonl", proposals)
    funding_record = funding.record_initial_funding(
        portfolio_version="PORT-001",
        amount=funding_amount,
        effective_at="2025-01-02T14:58:00+00:00",
        recorded_at="2025-01-02T14:59:00+00:00",
    )

    executions = LocalPaperExecutionLedger(tmp_path / "fills.jsonl", proposals)
    fills = [
        executions.simulate_full_fill(
            order_id="PORD-NVDA",
            fill_price=101,
            fees=2,
            filled_at="2025-01-02T15:01:00+00:00",
        )
    ]
    if include_second_fill:
        fills.append(
            executions.simulate_full_fill(
                order_id="PORD-AAPL",
                fill_price=51,
                fees=1,
                filled_at="2025-01-02T15:02:00+00:00",
            )
        )

    observations = OutcomeObservationLedger(tmp_path / "observations.jsonl", executions)
    outcome_prices = {"NVDA": 111, "AAPL": 55}
    for index, fill in enumerate(fills):
        observations.observe(
            fill_id=fill["fill_id"],
            horizon="ENTRY",
            benchmark_price=5_900,
            benchmark_price_effective_at=f"2025-01-02T15:0{index}:00+00:00",
            retrieved_at="2025-01-02T15:03:00+00:00",
            data_source="TEST_FIXTURE",
            source_version="fixture-v1",
            market_price_basis="UNADJUSTED_CLOSE",
        )
        outcome_at = (
            second_outcome_at if fill["ticker"] == "AAPL" else "2025-02-03T16:00:00+00:00"
        )
        observations.observe(
            fill_id=fill["fill_id"],
            horizon="1_MONTH",
            asset_price=outcome_prices[fill["ticker"]],
            benchmark_price=6_018,
            asset_price_effective_at=outcome_at,
            benchmark_price_effective_at=outcome_at,
            retrieved_at="2025-02-03T17:00:00+00:00",
            data_source="TEST_FIXTURE",
            source_version="fixture-v1",
            market_price_basis="UNADJUSTED_CLOSE",
        )

    actions = CorporateActionLedger(tmp_path / "actions.jsonl", executions)
    for fill in fills:
        through_at = (
            second_outcome_at if fill["ticker"] == "AAPL" else "2025-02-03T16:00:00+00:00"
        )
        actions.record(
            fill_id=fill["fill_id"],
            covers_from_at=fill["filled_at"],
            through_at=through_at,
            retrieved_at="2025-02-03T17:00:00+00:00",
            data_source="TEST_CORPORATE_ACTION_FIXTURE",
            source_version="fixture-v1",
            source_input_sha256=("a" if fill["ticker"] == "NVDA" else "b") * 64,
            events=(),
            completeness_status="COMPLETE",
            uncertainty_reasons=(),
        )
    total_returns = TotalReturnLedger(
        tmp_path / "total_returns.jsonl", observations, actions
    )
    for fill in fills:
        if fill["ticker"] == "AAPL" and not include_second_return:
            continue
        total_returns.calculate(
            fill_id=fill["fill_id"],
            horizon="1_MONTH",
            calculated_at="2025-02-03T17:01:00+00:00",
        )
    valuations = SimulatedPortfolioValuationLedger(
        tmp_path / "valuations.jsonl", executions, total_returns, funding
    )
    return valuations, funding, funding_record, proposals


def calculate(valuations, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "horizon": "1_MONTH",
        "calculated_at": "2025-02-03T17:02:00+00:00",
    }
    values.update(overrides)
    return valuations.calculate(**values)


def rewrite_with_valid_hash(path, module, **changes):
    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_initial_funding_is_exact_immutable_and_pretrade(tmp_path):
    _, funding, record, _ = complete_chain(tmp_path)
    assert record["record_type"] == "SIMULATED_PORTFOLIO_INITIAL_FUNDING"
    assert record["amount"] == "1000"
    assert record["exact_amount"] == {"numerator": "1000", "denominator": "1"}
    assert record["cash_flow_policy"] == "INITIAL_FUNDING_ONLY_EXTERNAL_FLOWS_BLOCKED"
    assert record["external_contributions_supported"] is False
    assert record["external_withdrawals_supported"] is False
    assert record["portfolio_return_calculated"] is False
    assert record["previous_hash"] == GENESIS_HASH
    assert funding.verify() == [record]


def test_late_or_future_funding_is_rejected(tmp_path):
    proposals = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    proposals.propose(
        decision_id="DEC-001",
        portfolio_version="PORT-001",
        ticker="NVDA",
        side="BUY",
        quantity=1,
        reference_price=100,
        target_weight=1,
        strategy_version="strategy-v1",
        model_versions=[{"component": "portfolio", "version": "1.0"}],
        created_at="2025-01-02T15:00:00+00:00",
        git_revision="abc123",
        order_id="PORD-001",
    )
    funding = PortfolioFundingLedger(tmp_path / "funding.jsonl", proposals)
    with pytest.raises(ValueError, match="before portfolio proposals"):
        funding.record_initial_funding(
            portfolio_version="PORT-001",
            amount=100,
            effective_at="2025-01-02T15:00:00+00:00",
            recorded_at="2025-01-02T15:01:00+00:00",
        )


def test_calculates_complete_portfolio_cash_value_and_weights(tmp_path):
    valuations, _, funding_record, _ = complete_chain(tmp_path)
    result = calculate(valuations)

    assert result["scope"] == "SIMULATED_INITIAL_FUNDED_PORTFOLIO_VALUATION"
    assert result["funding_record_hash"] == funding_record["record_hash"]
    assert result["position_count"] == 2
    assert result["initial_funding"] == "1000"
    assert result["total_recorded_entry_cost"] == "358"
    assert result["total_recorded_entry_fees"] == "3"
    assert result["total_recorded_entry_slippage_amount"] == "5"
    assert result["total_gross_dividend_cash"] == "0"
    assert result["remaining_cash"] == "642"
    assert result["total_position_market_value"] == "387"
    assert result["total_equity"] == "1029"
    assert result["target_position_weight_total"] == "0.35"
    assert result["target_cash_weight"] == "0.65"
    assert result["exact_fractions"]["actual_cash_weight"] == {
        "numerator": "214",
        "denominator": "343",
    }
    assert result["exact_fractions"]["actual_weight_total"] == {
        "numerator": "1",
        "denominator": "1",
    }
    positions = {item["ticker"]: item for item in result["positions"]}
    assert positions["NVDA"]["exact_fractions"]["actual_weight"] == {
        "numerator": "74",
        "denominator": "343",
    }
    assert positions["AAPL"]["exact_fractions"]["actual_weight"] == {
        "numerator": "55",
        "denominator": "343",
    }
    assert result["portfolio_valuation_calculated"] is True
    assert result["portfolio_return_calculated"] is False
    assert result["relative_portfolio_return_calculated"] is False
    assert result["alpha_calculated"] is False
    assert result["risk_adjusted"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert valuations.verify() == [result]


@pytest.mark.parametrize(
    "fixture_overrides,fragment",
    [
        ({"include_second_fill": False}, "proposal"),
        ({"include_second_return": False}, "total-return"),
        (
            {"second_outcome_at": "2025-02-03T16:01:00+00:00"},
            "effective times",
        ),
        ({"funding_amount": "300"}, "insufficient"),
        ({"target_weights": (0.60, 0.60)}, "exceed 100%"),
    ],
)
def test_incomplete_or_inconsistent_portfolio_fails_closed(
    tmp_path, fixture_overrides, fragment
):
    valuations, _, _, _ = complete_chain(tmp_path, **fixture_overrides)
    result = calculate(valuations)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert valuations.records() == []


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"horizon": "ENTRY"}, "baseline"),
        ({"horizon": "3_MONTHS"}, "total-return"),
        (
            {"calculated_at": "2025-02-03T17:00:59+00:00"},
            "predate supporting",
        ),
        ({"calculated_at": "2999-01-01T00:00:00+00:00"}, "future"),
    ],
)
def test_invalid_valuation_request_does_not_append(tmp_path, overrides, fragment):
    valuations, _, _, _ = complete_chain(tmp_path)
    result = calculate(valuations, **overrides)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert valuations.records() == []


def test_identical_concurrent_retries_create_one_valuation(tmp_path):
    valuations, _, _, _ = complete_chain(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(valuations), range(2)))
    assert first == second
    assert len(valuations.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"portfolio_return_calculated": True},
        {"alpha_calculated": True},
        {"learning_eligible": True},
        {"total_equity": "999999"},
        {"funding_record_hash": "f" * 64},
    ],
)
def test_rehashed_valuation_tampering_is_detected(tmp_path, changes):
    from core.performance import portfolio_valuation as module

    valuations, _, _, _ = complete_chain(tmp_path)
    calculate(valuations)
    rewrite_with_valid_hash(valuations.path, module, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        valuations.verify()


def test_rehashed_funding_tampering_is_detected(tmp_path):
    from core.performance import portfolio_funding as module

    _, funding, _, _ = complete_chain(tmp_path)
    rewrite_with_valid_hash(funding.path, module, external_withdrawals_supported=True)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        funding.verify()


def test_incomplete_valuation_tail_requires_explicit_repair(tmp_path):
    valuations, _, _, _ = complete_chain(tmp_path)
    result = calculate(valuations)
    with valuations.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        valuations.verify()
    backup = valuations.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert valuations.verify() == [result]
