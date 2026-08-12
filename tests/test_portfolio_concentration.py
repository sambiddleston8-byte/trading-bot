from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import PortfolioCashFlowLedger, PortfolioConcentrationLedger


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
                "calculated_at": "2025-02-03T17:01:00+00:00",
                "positions": [
                    {
                        "ticker": "AAA",
                        "exact_fractions": {
                            "outcome_position_value": fraction(400)
                        },
                    },
                    {
                        "ticker": "BBB",
                        "exact_fractions": {
                            "outcome_position_value": fraction(300)
                        },
                    },
                    {
                        "ticker": "CCC",
                        "exact_fractions": {
                            "outcome_position_value": fraction(100)
                        },
                    },
                ],
                "exact_fractions": {
                    "remaining_cash": fraction(200),
                    "total_equity": fraction(1000),
                },
                **IDENTITY,
            }
        ]

    def verify(self):
        return self.values


def ledgers(tmp_path):
    valuations = ValuationLedgerStub()
    flows = PortfolioCashFlowLedger(tmp_path / "cash_flows.jsonl", valuations)
    concentration = PortfolioConcentrationLedger(
        tmp_path / "concentration.jsonl", valuations, flows
    )
    return valuations, flows, concentration


def record_flow(flows, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "horizon": "1_MONTH",
        "flow_type": "CONTRIBUTION",
        "amount": "200",
        "recorded_at": "2025-02-03T17:02:00+00:00",
    }
    values.update(overrides)
    return flows.record(**values)


def calculate(concentration, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "horizon": "1_MONTH",
        "calculated_at": "2025-02-03T17:03:00+00:00",
    }
    values.update(overrides)
    return concentration.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import portfolio_concentration as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_calculates_exact_cash_inclusive_and_invested_concentration(tmp_path):
    _, _, concentration = ledgers(tmp_path)
    result = calculate(concentration)

    assert result["scope"] == "SIMULATED_POST_FLOW_PORTFOLIO_CONCENTRATION"
    assert result["post_flow_cash"] == "200"
    assert result["post_flow_total_equity"] == "1000"
    assert result["exact_fractions"]["cash_weight"] == fraction(1, 5)
    assert result["exact_fractions"]["cash_inclusive_allocation_hhi"] == fraction(
        3, 10
    )
    assert result["exact_fractions"]["invested_position_hhi"] == fraction(13, 32)
    assert result["exact_fractions"][
        "effective_number_of_invested_positions"
    ] == fraction(32, 13)
    assert result["exact_fractions"]["largest_position_weight"] == fraction(2, 5)
    assert result["exact_fractions"][
        "largest_invested_position_weight"
    ] == fraction(1, 2)
    assert result["exact_fractions"]["top_five_position_weight"] == fraction(4, 5)
    assert result["exact_fractions"][
        "top_five_invested_position_weight"
    ] == fraction(1)
    assert [item["ticker"] for item in result["positions"]] == ["AAA", "BBB", "CCC"]
    assert result["positions"][0]["exact_fractions"][
        "invested_position_weight"
    ] == fraction(1, 2)
    assert result["concentration_calculated"] is True
    assert result["concentration_classification_provided"] is False
    assert result["portfolio_return_calculated"] is False
    assert result["alpha_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert concentration.verify() == [result]


def test_contribution_is_held_as_cash_at_exact_boundary(tmp_path):
    _, flows, concentration = ledgers(tmp_path)
    flow = record_flow(flows)
    result = calculate(concentration)

    assert result["supporting_cash_flow_ids"] == [flow["flow_id"]]
    assert result["cumulative_external_cash_flow"] == "200"
    assert result["post_flow_cash"] == "400"
    assert result["post_flow_total_equity"] == "1200"
    assert result["exact_fractions"]["cash_weight"] == fraction(1, 3)
    assert result["exact_fractions"]["invested_position_weight_total"] == fraction(
        2, 3
    )
    assert result["exact_fractions"]["cash_inclusive_allocation_hhi"] == fraction(
        7, 24
    )
    assert result["exact_fractions"]["invested_position_hhi"] == fraction(13, 32)
    assert result["exact_fractions"]["largest_position_weight"] == fraction(1, 3)


def test_withdrawal_recalculates_post_flow_weights(tmp_path):
    _, flows, concentration = ledgers(tmp_path)
    record_flow(flows, flow_type="WITHDRAWAL", amount="100")
    result = calculate(concentration)
    assert result["post_flow_cash"] == "100"
    assert result["post_flow_total_equity"] == "900"
    assert result["exact_fractions"]["cash_weight"] == fraction(1, 9)
    assert result["exact_fractions"]["largest_position_weight"] == fraction(4, 9)
    assert result["exact_fractions"]["invested_position_hhi"] == fraction(13, 32)


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"horizon": "ENTRY"}, "baseline"),
        ({"horizon": "3_MONTHS"}, "valuation is missing"),
        (
            {"calculated_at": "2025-02-03T17:00:59+00:00"},
            "predate supporting",
        ),
        ({"calculated_at": "2999-01-01T00:00:00+00:00"}, "future"),
    ],
)
def test_invalid_request_fails_closed(tmp_path, overrides, fragment):
    _, _, concentration = ledgers(tmp_path)
    result = calculate(concentration, **overrides)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert concentration.records() == []


@pytest.mark.parametrize(
    "mutation,fragment",
    [
        (lambda value: value.update({"positions": []}), "invested position"),
        (
            lambda value: value["positions"].append(value["positions"][0].copy()),
            "unique tickers",
        ),
        (
            lambda value: value["exact_fractions"].update(
                {"total_equity": fraction(999)}
            ),
            "reconcile exactly",
        ),
    ],
)
def test_incomplete_or_inconsistent_valuation_fails_closed(
    tmp_path, mutation, fragment
):
    valuations, _, concentration = ledgers(tmp_path)
    mutation(valuations.values[0])
    result = calculate(concentration)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])


def test_identical_concurrent_retries_create_one_record(tmp_path):
    _, _, concentration = ledgers(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(
            executor.map(lambda _: calculate(concentration), range(2))
        )
    assert first == second
    assert len(concentration.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"concentration_calculated": False},
        {"concentration_classification_provided": True},
        {"portfolio_return_calculated": True},
        {"alpha_calculated": True},
        {"risk_adjusted": True},
        {"annualized": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"cash_inclusive_allocation_hhi": "999"},
        {"valuation_record_hash": "forged"},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    _, _, concentration = ledgers(tmp_path)
    calculate(concentration)
    rewrite_with_valid_hash(concentration.path, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        concentration.verify()


def test_supporting_valuation_tampering_is_detected(tmp_path):
    valuations, _, concentration = ledgers(tmp_path)
    calculate(concentration)
    valuations.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        concentration.verify()


def test_supporting_cash_flow_tampering_is_detected(tmp_path):
    _, flows, concentration = ledgers(tmp_path)
    record_flow(flows)
    calculate(concentration)
    flow = json.loads(flows.path.read_text())
    flow["record_hash"] = "changed"
    flows.path.write_text(json.dumps(flow) + "\n")
    with pytest.raises(LedgerIntegrityError):
        concentration.verify()


def test_later_boundary_cash_flow_does_not_invalidate_pinned_concentration(
    tmp_path,
):
    _, flows, concentration = ledgers(tmp_path)
    result = calculate(concentration)
    record_flow(flows, recorded_at="2025-02-03T17:04:00+00:00")
    assert concentration.verify() == [result]


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    _, _, concentration = ledgers(tmp_path)
    result = calculate(concentration)
    with concentration.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        concentration.verify()
    backup = concentration.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert concentration.verify() == [result]
