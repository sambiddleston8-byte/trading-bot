from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import FactorExposureEvidenceLedger


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
                    }
                ],
                **IDENTITY,
            }
        ]

    def verify(self):
        return self.values


def ledger(tmp_path):
    valuations = ValuationLedgerStub()
    evidence = FactorExposureEvidenceLedger(
        tmp_path / "factor_evidence.jsonl", valuations
    )
    return valuations, evidence


def observe(evidence, **overrides):
    values = {
        "portfolio_version": "PORT-001",
        "horizon": "1_MONTH",
        "ticker": "AAA",
        "provider": "TEST_PROVIDER",
        "factor_model_name": "TEST_STYLE_MODEL",
        "factor_model_version": "2025.1",
        "methodology_uri": "https://provider.example/methodology/style-model",
        "methodology_sha256": "a" * 64,
        "source_uri": "https://provider.example/factors/AAA",
        "source_input_sha256": "b" * 64,
        "factor_effective_at": "2025-02-03T16:00:00+00:00",
        "retrieved_at": "2025-02-03T16:30:00+00:00",
        "recorded_at": "2025-02-03T17:01:00+00:00",
        "factors": [
            {
                "factor_code": "MOMENTUM",
                "factor_name": "Momentum",
                "unit": "STANDARDIZED_SCORE",
                "exposure": "0.125",
            },
            {
                "factor_code": "VALUE",
                "factor_name": "Value",
                "unit": "STANDARDIZED_SCORE",
                "exposure": "-0.20",
            },
        ],
    }
    values.update(overrides)
    return evidence.observe(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import factor_exposure_evidence as module

    record = json.loads(path.read_text())
    record.update(changes)
    record["source_evidence_sha256"] = module._source_evidence_hash(record)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_records_exact_point_in_time_factor_evidence(tmp_path):
    _, evidence = ledger(tmp_path)
    record = observe(evidence)

    assert record["record_type"] == "POINT_IN_TIME_SECURITY_FACTOR_EXPOSURE_EVIDENCE"
    assert record["status"] == "OBSERVED"
    assert record["availability_at_valuation"] == "BACKFILLED_AFTER_BOUNDARY"
    assert [item["factor_code"] for item in record["factors"]] == [
        "MOMENTUM",
        "VALUE",
    ]
    assert record["factors"][0]["exposure"] == "0.125"
    assert record["factors"][0]["exact_exposure"] == {
        "numerator": "1",
        "denominator": "8",
    }
    assert record["factors"][1]["exact_exposure"] == {
        "numerator": "-1",
        "denominator": "5",
    }
    assert record["portfolio_factor_exposure_calculated"] is False
    assert record["recommendation_provided"] is False
    assert record["performance_claim"] is False
    assert record["alpha_calculated"] is False
    assert record["learning_eligible"] is False
    assert record["track_record_claim"] is False
    assert record["previous_hash"] == GENESIS_HASH
    assert evidence.verify() == [record]


def test_available_by_boundary_is_distinct_from_backfill(tmp_path):
    _, evidence = ledger(tmp_path)
    record = observe(
        evidence,
        factor_effective_at="2025-02-03T15:00:00+00:00",
        retrieved_at="2025-02-03T15:30:00+00:00",
    )
    assert record["availability_at_valuation"] == "AVAILABLE_BY_BOUNDARY"


def test_uncertain_can_resolve_to_complete_append_only(tmp_path):
    _, evidence = ledger(tmp_path)
    uncertain = observe(
        evidence,
        completeness_status="UNCERTAIN",
        uncertainty_reasons=["Provider model output unavailable"],
        factors=(),
    )
    complete = observe(
        evidence,
        retrieved_at="2025-02-04T16:30:00+00:00",
        recorded_at="2025-02-04T16:31:00+00:00",
        source_input_sha256="c" * 64,
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
            factors=(),
            retrieved_at="2025-02-04T16:30:00+00:00",
            recorded_at="2025-02-04T16:31:00+00:00",
        )


def test_conflicting_complete_evidence_is_rejected(tmp_path):
    _, evidence = ledger(tmp_path)
    observe(evidence)
    with pytest.raises(LedgerIntegrityError, match="Conflicting complete"):
        observe(
            evidence,
            source_input_sha256="d" * 64,
            retrieved_at="2025-02-04T16:30:00+00:00",
            recorded_at="2025-02-04T16:31:00+00:00",
            factors=[
                {
                    "factor_code": "MOMENTUM",
                    "factor_name": "Momentum",
                    "unit": "STANDARDIZED_SCORE",
                    "exposure": "0.987",
                }
            ],
        )


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"horizon": "3_MONTHS"}, "valuation"),
        ({"ticker": "MISSING"}, "position"),
        ({"factor_effective_at": "2025-02-04T16:00:00+00:00"}, "boundary"),
        ({"retrieved_at": "2025-02-02T16:00:00+00:00"}, "predate"),
        ({"recorded_at": "2025-02-03T16:59:00+00:00"}, "predate"),
        ({"source_uri": "http://provider.example/AAA"}, "HTTPS"),
        ({"methodology_sha256": "bad"}, "SHA-256"),
        ({"factors": []}, "requires factors"),
        (
            {
                "factors": [
                    {
                        "factor_code": "VALUE",
                        "factor_name": "Value",
                        "unit": "SCORE",
                        "exposure": "NaN",
                    }
                ]
            },
            "finite decimal",
        ),
        (
            {
                "factors": [
                    {
                        "factor_code": "VALUE",
                        "factor_name": "Value",
                        "unit": "SCORE",
                        "exposure": "1",
                    },
                    {
                        "factor_code": "VALUE",
                        "factor_name": "Other",
                        "unit": "SCORE",
                        "exposure": "2",
                    },
                ]
            },
            "unique",
        ),
        (
            {
                "completeness_status": "UNCERTAIN",
                "uncertainty_reasons": ["missing"],
            },
            "no factors",
        ),
    ],
)
def test_invalid_factor_evidence_fails_closed(tmp_path, overrides, fragment):
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
        {"portfolio_factor_exposure_calculated": True},
        {"recommendation_provided": True},
        {"performance_claim": True},
        {"alpha_calculated": True},
        {"learning_eligible": True},
        {"track_record_claim": True},
        {"valuation_record_hash": "forged"},
        {
            "factors": [
                {
                    "factor_code": "VALUE",
                    "factor_name": "Value",
                    "unit": "STANDARDIZED_SCORE",
                    "exposure": "999",
                    "exact_exposure": {"numerator": "999", "denominator": "1"},
                }
            ]
        },
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
