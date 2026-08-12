from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import EntryTransactionCostAttributionLedger


IDENTITY = {
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "portfolio", "version": "1.0"}],
    "git_revision": "abc123",
}


def fraction(numerator, denominator=1):
    return {"numerator": str(numerator), "denominator": str(denominator)}


class ValuationLedgerStub:
    def __init__(self):
        self.values = [
            {
                "valuation_id": "PVAL-1-MONTH",
                "record_hash": "valuation-hash",
                "portfolio_version": "PORT-001",
                "horizon": "1_MONTH",
                "horizon_label": "1 month",
                "outcome_asset_price_effective_at": "2025-02-03T16:00:00+00:00",
                "calculated_at": "2025-02-03T17:00:00+00:00",
                "positions": [
                    {
                        "ticker": "AAA",
                        "order_id": "ORDER-AAA",
                        "fill_id": "FILL-AAA",
                        "execution_record_hash": "fill-aaa-hash",
                        "exact_fractions": {
                            "recorded_entry_cost": fraction(103),
                            "recorded_entry_fee": fraction(2),
                            "recorded_entry_slippage_amount": fraction(1),
                        },
                    },
                    {
                        "ticker": "BBB",
                        "order_id": "ORDER-BBB",
                        "fill_id": "FILL-BBB",
                        "execution_record_hash": "fill-bbb-hash",
                        "exact_fractions": {
                            "recorded_entry_cost": fraction(50),
                            "recorded_entry_fee": fraction(1),
                            "recorded_entry_slippage_amount": fraction(-1),
                        },
                    },
                ],
                "exact_fractions": {
                    "total_recorded_entry_cost": fraction(153),
                    "total_recorded_entry_fees": fraction(3),
                    "total_recorded_entry_slippage_amount": fraction(0),
                },
                **IDENTITY,
            }
        ]

    def verify(self):
        return self.values


def ledgers(tmp_path):
    valuations = ValuationLedgerStub()
    costs = EntryTransactionCostAttributionLedger(
        tmp_path / "transaction_costs.jsonl", valuations
    )
    return valuations, costs


def calculate(costs, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "horizon": "1_MONTH",
        "calculated_at": "2025-02-03T17:01:00+00:00",
    }
    values.update(overrides)
    return costs.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import transaction_cost_attribution as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_attributes_exact_recorded_entry_costs_without_double_counting(tmp_path):
    _, costs = ledgers(tmp_path)
    result = calculate(costs)

    assert result["scope"] == "SIMULATED_RECORDED_ENTRY_TRANSACTION_COST_ATTRIBUTION"
    assert result["reference_notional"] == "150"
    assert result["simulated_fill_notional"] == "150"
    assert result["recorded_entry_fees"] == "3"
    assert result["signed_recorded_entry_slippage"] == "0"
    assert result["adverse_slippage_cost"] == "1"
    assert result["favourable_slippage_benefit"] == "1"
    assert result["net_recorded_entry_execution_cost"] == "3"
    assert result["fee_bps_of_reference_notional"] == "200"
    assert result["signed_slippage_bps_of_reference_notional"] == "0"
    assert result["net_cost_bps_of_reference_notional"] == "200"
    positions = {item["ticker"]: item for item in result["positions"]}
    assert positions["AAA"]["net_recorded_entry_execution_cost"] == "3"
    assert positions["BBB"]["favourable_slippage_benefit"] == "1"
    assert positions["BBB"]["net_recorded_entry_execution_cost"] == "0"
    assert result["costs_already_embedded_no_rededuction"] is True
    assert result["exit_cost_included"] is False
    assert result["bid_ask_spread_separately_included"] is False
    assert result["market_impact_included"] is False
    assert result["latency_cost_included"] is False
    assert result["turnover_calculated"] is False
    assert result["portfolio_return_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert costs.verify() == [result]


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"horizon": "ENTRY"}, "not a portfolio valuation"),
        ({"horizon": "3_MONTHS"}, "valuation is missing"),
        ({"calculated_at": "2025-02-03T16:59:00+00:00"}, "predate"),
        ({"calculated_at": "2999-01-01T00:00:00+00:00"}, "future"),
    ],
)
def test_invalid_request_fails_closed(tmp_path, overrides, fragment):
    _, costs = ledgers(tmp_path)
    result = calculate(costs, **overrides)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert costs.records() == []


@pytest.mark.parametrize(
    "mutation,fragment",
    [
        (lambda value: value.update({"positions": []}), "verified position"),
        (
            lambda value: value["positions"].append(value["positions"][0].copy()),
            "unique tickers",
        ),
        (
            lambda value: value["exact_fractions"].update(
                {"total_recorded_entry_fees": fraction(4)}
            ),
            "do not reconcile",
        ),
        (
            lambda value: value["positions"][0]["exact_fractions"].update(
                {"recorded_entry_slippage_amount": fraction(200)}
            ),
            "reference notional",
        ),
    ],
)
def test_invalid_cost_evidence_fails_closed(tmp_path, mutation, fragment):
    valuations, costs = ledgers(tmp_path)
    mutation(valuations.values[0])
    result = calculate(costs)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])


def test_identical_concurrent_retries_create_one_record(tmp_path):
    _, costs = ledgers(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(costs), range(2)))
    assert first == second
    assert len(costs.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"costs_already_embedded_no_rededuction": False},
        {"exit_cost_included": True},
        {"bid_ask_spread_separately_included": True},
        {"market_impact_included": True},
        {"turnover_calculated": True},
        {"portfolio_return_calculated": True},
        {"alpha_calculated": True},
        {"recommendation_provided": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"net_recorded_entry_execution_cost": "999"},
        {"valuation_record_hash": "forged"},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    _, costs = ledgers(tmp_path)
    calculate(costs)
    rewrite_with_valid_hash(costs.path, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        costs.verify()


def test_supporting_valuation_tampering_is_detected(tmp_path):
    valuations, costs = ledgers(tmp_path)
    calculate(costs)
    valuations.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        costs.verify()


def test_later_unrelated_valuation_does_not_invalidate_pinned_costs(tmp_path):
    valuations, costs = ledgers(tmp_path)
    result = calculate(costs)
    later = dict(valuations.values[0])
    later.update(
        {
            "valuation_id": "PVAL-3-MONTHS",
            "record_hash": "later-valuation-hash",
            "horizon": "3_MONTHS",
            "horizon_label": "3 months",
            "outcome_asset_price_effective_at": "2025-04-03T16:00:00+00:00",
            "calculated_at": "2025-04-03T17:00:00+00:00",
        }
    )
    valuations.values.append(later)
    assert costs.verify() == [result]


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    _, costs = ledgers(tmp_path)
    result = calculate(costs)
    with costs.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        costs.verify()
    backup = costs.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert costs.verify() == [result]
