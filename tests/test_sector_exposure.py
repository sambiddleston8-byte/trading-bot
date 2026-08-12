from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import (
    PortfolioCashFlowLedger,
    SectorClassificationEvidenceLedger,
    SectorExposureLedger,
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
    classifications = SectorClassificationEvidenceLedger(
        tmp_path / "classifications.jsonl", valuations
    )
    exposure = SectorExposureLedger(
        tmp_path / "sector_exposure.jsonl", valuations, flows, classifications
    )
    return valuations, flows, classifications, exposure


def classify(classifications, ticker, code, name, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "horizon": "1_MONTH",
        "ticker": ticker,
        "provider": "TEST_PROVIDER",
        "taxonomy_name": "TEST_CLASSIFICATION",
        "taxonomy_version": "2025.1",
        "sector_code": code,
        "sector_name": name,
        "classification_effective_at": "2025-01-01T00:00:00+00:00",
        "retrieved_at": "2025-02-03T15:00:00+00:00",
        "recorded_at": "2025-02-03T17:02:00+00:00",
        "source_uri": f"https://provider.example/classification/{ticker}",
        "source_input_sha256": (ticker.lower()[0] if ticker else "a") * 64,
    }
    values.update(overrides)
    return classifications.observe(**values)


def complete_all(classifications, **overrides):
    return [
        classify(classifications, "AAA", "45", "Technology", **overrides),
        classify(classifications, "BBB", "40", "Financials", **overrides),
        classify(classifications, "CCC", "45", "Technology", **overrides),
    ]


def calculate(exposure, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "horizon": "1_MONTH",
        "provider": "TEST_PROVIDER",
        "taxonomy_name": "TEST_CLASSIFICATION",
        "taxonomy_version": "2025.1",
        "calculated_at": "2025-02-03T17:03:00+00:00",
    }
    values.update(overrides)
    return exposure.calculate(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import sector_exposure as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_calculates_exact_sector_exposure_with_cash_separate(tmp_path):
    _, _, classifications, exposure = ledgers(tmp_path)
    evidence = complete_all(classifications)
    result = calculate(exposure)

    assert result["scope"] == "SIMULATED_POST_FLOW_POINT_IN_TIME_SECTOR_EXPOSURE"
    assert result["post_flow_cash"] == "200"
    assert result["post_flow_total_equity"] == "1000"
    assert result["exact_fractions"]["cash_weight"] == fraction(1, 5)
    assert result["exact_fractions"]["invested_position_weight"] == fraction(4, 5)
    assert [item["sector_code"] for item in result["sectors"]] == ["45", "40"]
    assert result["sectors"][0]["tickers"] == ["AAA", "CCC"]
    assert result["sectors"][0]["exact_fractions"]["portfolio_weight"] == fraction(1, 2)
    assert result["sectors"][0]["exact_fractions"]["invested_weight"] == fraction(5, 8)
    assert result["sectors"][1]["exact_fractions"]["portfolio_weight"] == fraction(3, 10)
    assert result["sectors"][1]["exact_fractions"]["invested_weight"] == fraction(3, 8)
    assert result["classification_evidence_ids"] == [item["evidence_id"] for item in evidence]
    assert result["cash_classified_as_sector"] is False
    assert result["recommendation_provided"] is False
    assert result["portfolio_return_calculated"] is False
    assert result["alpha_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["track_record_claim"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert exposure.verify() == [result]


def test_contribution_remains_cash_and_changes_portfolio_weights_only(tmp_path):
    _, flows, classifications, exposure = ledgers(tmp_path)
    complete_all(classifications)
    flows.record(
        portfolio_version="PORT-001",
        horizon="1_MONTH",
        flow_type="CONTRIBUTION",
        amount="200",
        recorded_at="2025-02-03T17:02:30+00:00",
    )
    result = calculate(exposure)
    assert result["post_flow_cash"] == "400"
    assert result["post_flow_total_equity"] == "1200"
    assert result["exact_fractions"]["cash_weight"] == fraction(1, 3)
    assert result["exact_fractions"]["invested_position_weight"] == fraction(2, 3)
    assert result["sectors"][0]["exact_fractions"]["portfolio_weight"] == fraction(5, 12)
    assert result["sectors"][0]["exact_fractions"]["invested_weight"] == fraction(5, 8)


def test_backfilled_classification_is_disclosed(tmp_path):
    _, _, classifications, exposure = ledgers(tmp_path)
    complete_all(
        classifications,
        retrieved_at="2025-02-04T15:00:00+00:00",
        recorded_at="2025-02-04T15:01:00+00:00",
    )
    result = calculate(exposure, calculated_at="2025-02-04T15:02:00+00:00")
    assert result["contains_backfilled_classification_evidence"] is True


@pytest.mark.parametrize(
    "setup,overrides,fragment",
    [
        (lambda c: None, {}, "AAA requires exactly one complete"),
        (
            lambda c: complete_all(c),
            {"taxonomy_version": "different"},
            "requires exactly one complete",
        ),
        (
            lambda c: classify(
                c,
                "AAA",
                None,
                None,
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
    _, _, classifications, exposure = ledgers(tmp_path)
    setup(classifications)
    result = calculate(exposure, **overrides)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])
    assert exposure.records() == []


def test_duplicate_complete_evidence_fails_closed(tmp_path):
    _, _, classifications, exposure = ledgers(tmp_path)
    complete_all(classifications)
    classify(
        classifications,
        "AAA",
        "45",
        "Technology",
        source_input_sha256="d" * 64,
        retrieved_at="2025-02-03T15:30:00+00:00",
        recorded_at="2025-02-03T17:02:30+00:00",
    )
    result = calculate(exposure)
    assert result["status"] == "NOT_CALCULABLE"
    assert "AAA requires exactly one complete" in " ".join(result["reasons"])


@pytest.mark.parametrize(
    "second_code,second_name,fragment",
    [
        ("45", "Different name", "conflicting names"),
        ("99", "Technology", "conflicting codes"),
    ],
)
def test_conflicting_provider_code_name_mapping_fails_closed(
    tmp_path, second_code, second_name, fragment
):
    _, _, classifications, exposure = ledgers(tmp_path)
    classify(classifications, "AAA", "45", "Technology")
    classify(classifications, "BBB", second_code, second_name)
    classify(classifications, "CCC", "40", "Financials")
    result = calculate(exposure)
    assert result["status"] == "NOT_CALCULABLE"
    assert fragment in " ".join(result["reasons"])


def test_identical_concurrent_retries_create_one_record(tmp_path):
    _, _, classifications, exposure = ledgers(tmp_path)
    complete_all(classifications)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(exposure), range(2)))
    assert first == second
    assert len(exposure.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"sector_exposure_calculated": False},
        {"cash_classified_as_sector": True},
        {"recommendation_provided": True},
        {"portfolio_return_calculated": True},
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
    _, _, classifications, exposure = ledgers(tmp_path)
    complete_all(classifications)
    calculate(exposure)
    rewrite_with_valid_hash(exposure.path, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        exposure.verify()


def test_supporting_classification_tampering_is_detected(tmp_path):
    _, _, classifications, exposure = ledgers(tmp_path)
    complete_all(classifications)
    calculate(exposure)
    records = classifications.path.read_text().splitlines()
    first = json.loads(records[0])
    first["record_hash"] = "changed"
    records[0] = json.dumps(first)
    classifications.path.write_text("\n".join(records) + "\n")
    with pytest.raises(LedgerIntegrityError):
        exposure.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    _, _, classifications, exposure = ledgers(tmp_path)
    complete_all(classifications)
    result = calculate(exposure)
    with exposure.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        exposure.verify()
    backup = exposure.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert exposure.verify() == [result]
