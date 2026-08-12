from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import SectorClassificationEvidenceLedger


IDENTITY = {
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "portfolio", "version": "1.0"}],
    "git_revision": "abc123",
}


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
                        "total_return_result_id": "TRET-AAA",
                        "total_return_result_hash": "return-aaa-hash",
                    },
                    {
                        "ticker": "BBB",
                        "total_return_result_id": "TRET-BBB",
                        "total_return_result_hash": "return-bbb-hash",
                    },
                ],
                **IDENTITY,
            }
        ]

    def verify(self):
        return self.values


def ledger(tmp_path):
    valuations = ValuationLedgerStub()
    evidence = SectorClassificationEvidenceLedger(
        tmp_path / "sector_classification.jsonl", valuations
    )
    return valuations, evidence


def observe(evidence, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "horizon": "1_MONTH",
        "ticker": "AAA",
        "provider": "TEST_PROVIDER",
        "taxonomy_name": "TEST_CLASSIFICATION",
        "taxonomy_version": "2025.1",
        "sector_code": "45",
        "sector_name": "Information Technology",
        "industry_code": "45103010",
        "industry_name": "Application Software",
        "classification_effective_at": "2025-01-01T00:00:00+00:00",
        "retrieved_at": "2025-02-03T15:00:00+00:00",
        "recorded_at": "2025-02-03T17:01:00+00:00",
        "source_uri": "https://provider.example/classification/AAA",
        "source_input_sha256": "a" * 64,
    }
    values.update(overrides)
    return evidence.observe(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import sector_classification as module

    record = json.loads(path.read_text())
    record.update(changes)
    if any(
        key in changes
        for key in (
            "sector_code",
            "sector_name",
            "industry_code",
            "industry_name",
            "retrieved_at",
            "uncertainty_reasons",
            "source_uri",
            "source_input_sha256",
        )
    ):
        record["source_evidence_sha256"] = module._source_evidence_hash(record)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_records_complete_point_in_time_provider_evidence(tmp_path):
    _, evidence = ledger(tmp_path)
    record = observe(evidence)

    assert record["record_type"] == (
        "POINT_IN_TIME_SECTOR_CLASSIFICATION_EVIDENCE"
    )
    assert record["status"] == "OBSERVED"
    assert record["ticker"] == "AAA"
    assert record["sector_code"] == "45"
    assert record["sector_name"] == "Information Technology"
    assert record["industry_code"] == "45103010"
    assert record["industry_name"] == "Application Software"
    assert record["availability_at_valuation"] == "AVAILABLE_BY_BOUNDARY"
    assert record["label_policy"] == (
        "PROVIDER_LABELS_PRESERVED_WITHOUT_TRANSLATION"
    )
    assert record["missing_policy"] == (
        "MISSING_OR_UNKNOWN_IS_UNCERTAIN_NOT_A_SECTOR"
    )
    assert record["valuation_record_hash"] == "valuation-hash"
    assert record["position_total_return_result_hash"] == "return-aaa-hash"
    assert record["exposure_calculated"] is False
    assert record["recommendation_provided"] is False
    assert record["performance_claim"] is False
    assert record["alpha_calculated"] is False
    assert record["learning_eligible"] is False
    assert record["track_record_claim"] is False
    assert record["previous_hash"] == GENESIS_HASH
    assert len(record["source_evidence_sha256"]) == 64
    assert evidence.verify() == [record]
    assert evidence.complete_evidence(
        valuation_id="PVAL-1-MONTH",
        ticker="AAA",
        provider="TEST_PROVIDER",
        taxonomy_name="TEST_CLASSIFICATION",
        taxonomy_version="2025.1",
    ) == record


def test_backfilled_evidence_is_explicitly_labelled(tmp_path):
    _, evidence = ledger(tmp_path)
    record = observe(
        evidence,
        retrieved_at="2025-02-04T15:00:00+00:00",
        recorded_at="2025-02-04T15:01:00+00:00",
    )
    assert record["availability_at_valuation"] == "BACKFILLED_AFTER_BOUNDARY"


def test_industry_is_optional_but_sector_is_required(tmp_path):
    _, evidence = ledger(tmp_path)
    record = observe(evidence, industry_code=None, industry_name=None)
    assert record["industry_code"] is None
    assert record["industry_name"] is None


def test_uncertain_evidence_asserts_no_classification(tmp_path):
    _, evidence = ledger(tmp_path)
    record = observe(
        evidence,
        completeness_status="UNCERTAIN",
        uncertainty_reasons=["Provider classification could not be reconciled"],
        sector_code=None,
        sector_name=None,
        industry_code=None,
        industry_name=None,
    )
    assert record["status"] == "UNCERTAIN"
    assert record["sector_name"] is None
    assert evidence.complete_evidence(
        valuation_id="PVAL-1-MONTH",
        ticker="AAA",
        provider="TEST_PROVIDER",
        taxonomy_name="TEST_CLASSIFICATION",
        taxonomy_version="2025.1",
    ) is None


def test_uncertain_can_resolve_to_complete_without_replacement(tmp_path):
    _, evidence = ledger(tmp_path)
    uncertain = observe(
        evidence,
        completeness_status="UNCERTAIN",
        uncertainty_reasons=["Provider response pending"],
        sector_code=None,
        sector_name=None,
        industry_code=None,
        industry_name=None,
    )
    complete = observe(
        evidence,
        retrieved_at="2025-02-04T15:00:00+00:00",
        recorded_at="2025-02-04T15:01:00+00:00",
        source_input_sha256="b" * 64,
    )
    assert uncertain["evidence_id"] != complete["evidence_id"]
    assert [item["completeness_status"] for item in evidence.verify()] == [
        "UNCERTAIN",
        "COMPLETE",
    ]


def test_complete_cannot_regress_to_uncertain(tmp_path):
    _, evidence = ledger(tmp_path)
    observe(evidence)
    with pytest.raises(LedgerIntegrityError, match="cannot regress"):
        observe(
            evidence,
            completeness_status="UNCERTAIN",
            uncertainty_reasons=["Late uncertainty"],
            sector_code=None,
            sector_name=None,
            industry_code=None,
            industry_name=None,
            retrieved_at="2025-02-04T15:00:00+00:00",
            recorded_at="2025-02-04T15:01:00+00:00",
        )


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"horizon": "3_MONTHS"}, "verified portfolio valuation"),
        ({"ticker": "MISSING"}, "position"),
        (
            {"classification_effective_at": "2025-02-04T00:00:00+00:00"},
            "later than the valuation",
        ),
        ({"retrieved_at": "2024-12-31T00:00:00+00:00"}, "predate"),
        ({"recorded_at": "2025-02-03T16:59:00+00:00"}, "predate"),
        ({"source_uri": "http://provider.example/AAA"}, "HTTPS"),
        ({"source_uri": "https://user:pass@provider.example/AAA"}, "credentials"),
        ({"source_input_sha256": "bad"}, "SHA-256"),
        ({"sector_code": None}, "sector_code"),
        ({"sector_name": "Unknown"}, "missing-data placeholder"),
        ({"industry_name": None}, "both be supplied"),
        (
            {
                "completeness_status": "UNCERTAIN",
                "sector_code": None,
                "sector_name": None,
                "industry_code": None,
                "industry_name": None,
            },
            "requires reasons",
        ),
        (
            {
                "completeness_status": "UNCERTAIN",
                "uncertainty_reasons": ["unresolved"],
            },
            "cannot assert",
        ),
    ],
)
def test_invalid_evidence_fails_closed(tmp_path, overrides, fragment):
    _, evidence = ledger(tmp_path)
    with pytest.raises((ValueError, LedgerIntegrityError), match=fragment):
        observe(evidence, **overrides)


def test_identical_concurrent_retries_create_one_record(tmp_path):
    _, evidence = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: observe(evidence), range(2)))
    assert first == second
    assert len(evidence.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"exposure_calculated": True},
        {"recommendation_provided": True},
        {"performance_claim": True},
        {"alpha_calculated": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"sector_name": "Forged"},
        {"retrieved_at": "2025-02-03T15:30:00+00:00"},
        {"source_uri": "https://forged.example/classification/AAA"},
        {"valuation_record_hash": "forged"},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    _, evidence = ledger(tmp_path)
    observe(evidence)
    rewrite_with_valid_hash(evidence.path, **changes)
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        evidence.verify()


def test_supporting_valuation_tampering_is_detected(tmp_path):
    valuations, evidence = ledger(tmp_path)
    observe(evidence)
    valuations.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        evidence.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    _, evidence = ledger(tmp_path)
    record = observe(evidence)
    with evidence.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        evidence.verify()
    backup = evidence.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert evidence.verify() == [record]
