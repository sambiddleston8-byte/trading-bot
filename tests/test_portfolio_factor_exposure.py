from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import (
    FactorExposureEvidenceLedger,
    PortfolioCashFlowLedger,
    PortfolioFactorExposureLedger,
)


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
                        "total_return_result_id": "TRET-AAA",
                        "total_return_result_hash": "return-aaa-hash",
                        "exact_fractions": {"outcome_position_value": fraction(400)},
                    },
                    {
                        "ticker": "BBB",
                        "total_return_result_id": "TRET-BBB",
                        "total_return_result_hash": "return-bbb-hash",
                        "exact_fractions": {"outcome_position_value": fraction(300)},
                    },
                    {
                        "ticker": "CCC",
                        "total_return_result_id": "TRET-CCC",
                        "total_return_result_hash": "return-ccc-hash",
                        "exact_fractions": {"outcome_position_value": fraction(100)},
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
    evidence = FactorExposureEvidenceLedger(tmp_path / "factor_evidence.jsonl", valuations)
    exposure = PortfolioFactorExposureLedger(
        tmp_path / "portfolio_factor_exposure.jsonl", valuations, flows, evidence
    )
    return valuations, flows, evidence, exposure


def observe(evidence, ticker, value, momentum, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "horizon": "1_MONTH",
        "ticker": ticker,
        "provider": "TEST_PROVIDER",
        "factor_model_name": "TEST_MODEL",
        "factor_model_version": "1.0",
        "methodology_uri": "https://provider.example/methodology/1.0",
        "methodology_sha256": "a" * 64,
        "source_uri": f"https://provider.example/factors/{ticker}",
        "source_input_sha256": (ticker.lower()[0]) * 64,
        "factor_effective_at": "2025-02-03T15:30:00+00:00",
        "retrieved_at": "2025-02-03T15:45:00+00:00",
        "recorded_at": "2025-02-03T17:02:00+00:00",
        "factors": [
            {
                "factor_code": "MOMENTUM",
                "factor_name": "Momentum",
                "unit": "standard_score",
                "exposure": momentum,
            },
            {
                "factor_code": "VALUE",
                "factor_name": "Value",
                "unit": "standard_score",
                "exposure": value,
            },
        ],
    }
    values.update(overrides)
    return evidence.observe(**values)


def complete_all(evidence, **overrides):
    return [
        observe(evidence, "AAA", "1", "0.5", **overrides),
        observe(evidence, "BBB", "-1", "0", **overrides),
        observe(evidence, "CCC", "0.5", "-0.5", **overrides),
    ]


def calculate(exposure, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "horizon": "1_MONTH",
        "provider": "TEST_PROVIDER",
        "factor_model_name": "TEST_MODEL",
        "factor_model_version": "1.0",
        "calculated_at": "2025-02-03T17:03:00+00:00",
    }
    values.update(overrides)
    return exposure.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import portfolio_factor_exposure as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_calculates_exact_factor_exposure_and_keeps_cash_separate(tmp_path):
    _, _, evidence, exposure = ledgers(tmp_path)
    support = complete_all(evidence)
    result = calculate(exposure)

    assert result["scope"] == "SIMULATED_POST_FLOW_INVESTED_POSITION_FACTOR_EXPOSURE"
    assert result["post_flow_cash"] == "200"
    assert result["post_flow_total_equity"] == "1000"
    assert result["exact_fractions"]["cash_weight"] == fraction(1, 5)
    assert result["exact_fractions"]["invested_position_weight"] == fraction(4, 5)
    assert [item["factor_code"] for item in result["factors"]] == ["MOMENTUM", "VALUE"]
    for item in result["factors"]:
        assert item["exact_fractions"]["invested_position_weighted_exposure"] == fraction(3, 16)
        assert item["exact_fractions"]["position_contribution_scaled_to_total_equity"] == fraction(3, 20)
    assert result["factor_evidence_ids"] == [item["evidence_id"] for item in support]
    assert result["cash_factor_exposure_modelled"] is False
    assert result["recommendation_provided"] is False
    assert result["performance_claim"] is False
    assert result["alpha_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert exposure.verify() == [result]


def test_contribution_changes_equity_scaling_but_not_invested_exposure(tmp_path):
    _, flows, evidence, exposure = ledgers(tmp_path)
    complete_all(evidence)
    flows.record(
        portfolio_version="PORT-001",
        horizon="1_MONTH",
        flow_type="CONTRIBUTION",
        amount="200",
        recorded_at="2025-02-03T17:02:30+00:00",
    )
    result = calculate(exposure)
    assert result["exact_fractions"]["cash_weight"] == fraction(1, 3)
    assert result["exact_fractions"]["invested_position_weight"] == fraction(2, 3)
    for item in result["factors"]:
        assert item["exact_fractions"]["invested_position_weighted_exposure"] == fraction(3, 16)
        assert item["exact_fractions"]["position_contribution_scaled_to_total_equity"] == fraction(1, 8)


def test_backfilled_evidence_is_disclosed(tmp_path):
    _, _, evidence, exposure = ledgers(tmp_path)
    complete_all(
        evidence,
        retrieved_at="2025-02-04T15:45:00+00:00",
        recorded_at="2025-02-04T17:02:00+00:00",
    )
    result = calculate(exposure, calculated_at="2025-02-04T17:03:00+00:00")
    assert result["contains_backfilled_factor_evidence"] is True


@pytest.mark.parametrize(
    "setup,overrides,fragment",
    [
        (lambda e: None, {}, "AAA requires exactly one complete"),
        (lambda e: complete_all(e), {"factor_model_version": "2.0"}, "requires exactly one complete"),
        (
            lambda e: e.observe(
                portfolio_version="PORT-001",
                horizon="1_MONTH",
                ticker="AAA",
                provider="TEST_PROVIDER",
                factor_model_name="TEST_MODEL",
                factor_model_version="1.0",
                methodology_uri="https://provider.example/methodology/1.0",
                methodology_sha256="a" * 64,
                source_uri="https://provider.example/factors/AAA",
                source_input_sha256="a" * 64,
                factor_effective_at="2025-02-03T15:30:00+00:00",
                retrieved_at="2025-02-03T15:45:00+00:00",
                recorded_at="2025-02-03T17:02:00+00:00",
                completeness_status="UNCERTAIN",
                uncertainty_reasons=["Provider unavailable"],
            ),
            {},
            "AAA requires exactly one complete",
        ),
    ],
)
def test_missing_mixed_or_uncertain_evidence_fails_closed(
    tmp_path, setup, overrides, fragment
):
    _, _, evidence, exposure = ledgers(tmp_path)
    setup(evidence)
    result = calculate(exposure, **overrides)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert exposure.records() == []


@pytest.mark.parametrize(
    "field,value,fragment",
    [
        ("methodology_sha256", "b" * 64, "same factor methodology"),
        ("factor_effective_at", "2025-02-03T15:00:00+00:00", "same factor effective"),
    ],
)
def test_mixed_methodology_or_effective_time_fails_closed(tmp_path, field, value, fragment):
    _, _, evidence, exposure = ledgers(tmp_path)
    observe(evidence, "AAA", "1", "0.5")
    observe(evidence, "BBB", "-1", "0", **{field: value})
    observe(evidence, "CCC", "0.5", "-0.5")
    result = calculate(exposure)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])


def test_mixed_factor_units_fail_closed(tmp_path):
    _, _, evidence, exposure = ledgers(tmp_path)
    observe(evidence, "AAA", "1", "0.5")
    observe(
        evidence,
        "BBB",
        "-1",
        "0",
        factors=[
            {"factor_code": "MOMENTUM", "factor_name": "Momentum", "unit": "percent", "exposure": "0"},
            {"factor_code": "VALUE", "factor_name": "Value", "unit": "standard_score", "exposure": "-1"},
        ],
    )
    observe(evidence, "CCC", "0.5", "-0.5")
    result = calculate(exposure)
    assert result["status"] == "NOT_CALCULABLE"
    assert "identical factor definitions and units" in " ".join(result["reasons"])


def test_missing_valuation_and_entry_horizon_fail_closed(tmp_path):
    _, _, _, exposure = ledgers(tmp_path)
    missing = calculate(exposure, portfolio_version="UNKNOWN")
    entry = calculate(exposure, horizon="ENTRY")
    assert missing["status"] == "NOT_CALCULABLE"
    assert "Verified portfolio valuation is missing" in " ".join(missing["reasons"])
    assert entry["status"] == "NOT_CALCULABLE"
    assert "ENTRY is the funding baseline" in " ".join(entry["reasons"])


class FlowLedgerStub:
    def __init__(self, values):
        self.values = values

    def verify(self):
        return self.values


def invalid_flow(*, valuation_id="PVAL-1-MONTH", signed=-1200):
    return {
        "flow_id": "PCF-INVALID",
        "record_hash": "invalid-flow-hash",
        "portfolio_version": "PORT-001",
        "valuation_id": valuation_id,
        "effective_at": "2025-02-03T16:00:00+00:00",
        "recorded_at": "2025-02-03T17:02:30+00:00",
        "exact_signed_amount": fraction(signed),
        **IDENTITY,
    }


def test_invalid_dependency_flow_cannot_create_negative_cash_or_equity(tmp_path):
    valuations, _, evidence, _ = ledgers(tmp_path)
    complete_all(evidence)
    exposure = PortfolioFactorExposureLedger(
        tmp_path / "negative.jsonl",
        valuations,
        FlowLedgerStub([invalid_flow()]),
        evidence,
    )
    result = calculate(exposure)
    assert result["status"] == "NOT_CALCULABLE"
    assert "cash and total equity must remain valid" in " ".join(result["reasons"])


def test_cash_flow_outside_included_valuation_boundary_fails_closed(tmp_path):
    valuations, _, evidence, _ = ledgers(tmp_path)
    complete_all(evidence)
    exposure = PortfolioFactorExposureLedger(
        tmp_path / "boundary.jsonl",
        valuations,
        FlowLedgerStub([invalid_flow(valuation_id="PVAL-NOT-IN-BOUNDARY", signed=100)]),
        evidence,
    )
    result = calculate(exposure)
    assert result["status"] == "NOT_CALCULABLE"
    assert "included valuation boundary" in " ".join(result["reasons"])


def test_identical_concurrent_retries_create_one_record(tmp_path):
    _, _, evidence, exposure = ledgers(tmp_path)
    complete_all(evidence)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(exposure), range(2)))
    assert first == second
    assert len(exposure.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"portfolio_factor_exposure_calculated": False},
        {"cash_factor_exposure_modelled": True},
        {"recommendation_provided": True},
        {"performance_claim": True},
        {"alpha_calculated": True},
        {"risk_adjusted": True},
        {"annualized": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"post_flow_cash": "999"},
        {"valuation_record_hash": "forged"},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    _, _, evidence, exposure = ledgers(tmp_path)
    complete_all(evidence)
    calculate(exposure)
    rewrite_with_valid_hash(exposure.path, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        exposure.verify()


def test_supporting_factor_evidence_tampering_is_detected(tmp_path):
    _, _, evidence, exposure = ledgers(tmp_path)
    complete_all(evidence)
    calculate(exposure)
    records = evidence.path.read_text().splitlines()
    first = json.loads(records[0])
    first["record_hash"] = "changed"
    records[0] = json.dumps(first)
    evidence.path.write_text("\n".join(records) + "\n")
    with pytest.raises(LedgerIntegrityError):
        exposure.verify()


def test_later_cash_flow_does_not_invalidate_pinned_result(tmp_path):
    _, flows, evidence, exposure = ledgers(tmp_path)
    complete_all(evidence)
    result = calculate(exposure)
    flows.record(
        portfolio_version="PORT-001",
        horizon="1_MONTH",
        flow_type="CONTRIBUTION",
        amount="200",
        recorded_at="2025-02-03T17:04:00+00:00",
    )
    assert exposure.verify() == [result]


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    _, _, evidence, exposure = ledgers(tmp_path)
    complete_all(evidence)
    result = calculate(exposure)
    with exposure.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        exposure.verify()
    backup = exposure.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert exposure.verify() == [result]
