import json
from datetime import datetime, timezone

import pytest

from core.data_quality.authenticated_source_content import AuthenticatedSourceContentLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.portfolio.historical_provider_qualification import (
    REQUIRED_CAPABILITIES,
    REQUIRED_CORPORATE_ACTIONS,
    REQUIRED_TIME_FIELDS,
    HistoricalProviderQualificationLedger,
)


def content_ledger(tmp_path):
    return AuthenticatedSourceContentLedger(tmp_path / "sources.jsonl", tmp_path / "blobs")


def ingest(ledger, suffix, payload):
    return ledger.ingest(
        source_uri=f"https://provider.example/{suffix}", payload=payload,
        media_type="application/json",
        publicly_available_at="2025-01-01T00:00:00+00:00",
        retrieved_at="2025-01-02T00:00:00+00:00",
        recorded_at="2025-01-03T00:00:00+00:00", source_locator="root",
    )


def evidence(tmp_path):
    ledger = content_ledger(tmp_path)
    terms = ingest(ledger, "terms", b"terms")
    documentation = ingest(ledger, "documentation", b"documentation")
    sample = ingest(ledger, "sample", b"sample")
    references = [
        {"content_evidence_id": terms["content_evidence_id"], "evidence_kind": "TERMS", "purpose": "Permitted uses and redistribution"},
        {"content_evidence_id": documentation["content_evidence_id"], "evidence_kind": "DOCUMENTATION", "purpose": "Fields, history and corrections"},
        {"content_evidence_id": sample["content_evidence_id"], "evidence_kind": "SAMPLE", "purpose": "Observed representative export"},
    ]
    matrix = [
        {
            "capability": capability,
            "documentation_content_evidence_id": documentation["content_evidence_id"],
            "sample_content_evidence_id": sample["content_evidence_id"],
        }
        for capability in sorted(REQUIRED_CAPABILITIES)
    ]
    return ledger, references, matrix


def target(tmp_path):
    contents, references, matrix = evidence(tmp_path)
    return (
        HistoricalProviderQualificationLedger(tmp_path / "qualification.jsonl", contents),
        references,
        matrix,
    )


def qualify(ledger, references, matrix, **overrides):
    values = {
        "provider_name": "Example Historical Data",
        "product_or_plan": "Research Plan",
        "provider_hosts": ["provider.example"],
        "evidence_references": references,
        "permitted_uses": ["LOCAL_RESEARCH", "HISTORICAL_REPLAY", "INTERNAL_DERIVED_RESULTS"],
        "evaluated_universes": ["SP500", "NASDAQ100"],
        "coverage_not_before": "2000-01-01",
        "coverage_not_after": "2025-12-31",
        "capability_evidence": matrix,
        "corporate_action_types": sorted(REQUIRED_CORPORATE_ACTIONS),
        "point_in_time_fields": sorted(REQUIRED_TIME_FIELDS),
        "corrections_and_revisions_preserved": True,
        "delisted_history_retained": True,
        "terminal_zero_recovery_representable": True,
        "prices_adjustment_method_documented": True,
        "observed_sample_matches_documentation": True,
        "reproducible_export_available": True,
        "redistribution_allowed": False,
        "known_limitations": [{
            "description": "Daily data only",
            "mitigation": "Daily frequency matches the registered replay design",
            "blocks_required_capability": False,
        }],
        "assessed_by": "Sam",
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }
    values.update(overrides)
    return ledger.qualify(**values)


def test_passes_only_as_candidate_for_separate_human_approval(tmp_path):
    ledger, references, matrix = target(tmp_path)
    record = qualify(ledger, references, matrix)
    assert record["previous_hash"] == GENESIS_HASH
    assert record["qualification_passed"] is True
    assert record["status"] == "PASSED_AWAITING_HUMAN_APPROVAL"
    assert record["eligible_for_separate_human_approval"] is True
    for field in (
        "source_quality_independently_proven", "provider_approved",
        "subscription_purchased", "credentials_recorded",
        "data_fetched_by_qualification", "evaluation_dataset_opened",
        "replay_executed", "performance_claim_allowed", "broker_submission_enabled",
        "live_trading_enabled",
    ):
        assert record[field] is False
    assert ledger.verify() == [record]


@pytest.mark.parametrize("field,value", [
    ("permitted_uses", ["LOCAL_RESEARCH"]),
    ("evaluated_universes", ["SP500"]),
    ("corporate_action_types", ["SPLITS"]),
    ("point_in_time_fields", ["EFFECTIVE_AT"]),
    ("corrections_and_revisions_preserved", False),
    ("delisted_history_retained", False),
    ("terminal_zero_recovery_representable", False),
    ("prices_adjustment_method_documented", False),
    ("observed_sample_matches_documentation", False),
    ("reproducible_export_available", False),
])
def test_incomplete_provider_fails_without_approval_or_authority(tmp_path, field, value):
    ledger, references, matrix = target(tmp_path)
    record = qualify(ledger, references, matrix, **{field: value})
    assert record["status"] == "FAILED"
    assert record["qualification_passed"] is False
    assert record["eligible_for_separate_human_approval"] is False
    assert record["provider_approved"] is False


def test_blocking_known_limitation_forces_failure(tmp_path):
    ledger, references, matrix = target(tmp_path)
    record = qualify(ledger, references, matrix, known_limitations=[{
        "description": "Delisted records are unavailable",
        "mitigation": "None in the assessed product",
        "blocks_required_capability": True,
    }])
    assert record["qualification_passed"] is False
    assert record["qualification_checks"][
        "no_limitation_blocks_a_required_capability"
    ] is False


def test_every_capability_requires_authenticated_documentation_and_sample(tmp_path):
    ledger, references, matrix = target(tmp_path)
    with pytest.raises(ValueError, match="each capability"):
        qualify(
            ledger, references, matrix,
            capability_evidence=[{
                **item,
                "sample_content_evidence_id": item["documentation_content_evidence_id"],
            } for item in matrix],
        )
    failed = qualify(ledger, references, matrix[:-1])
    assert failed["qualification_passed"] is False
    assert failed["qualification_checks"][
        "all_required_capabilities_documented_and_sampled"
    ] is False


def test_terms_documentation_and_sample_are_all_required_and_provider_host_bound(tmp_path):
    ledger, references, matrix = target(tmp_path)
    with pytest.raises(ValueError, match="all required"):
        qualify(ledger, references[:-1], matrix)
    with pytest.raises(ValueError, match="outside the assessed provider"):
        qualify(ledger, references, matrix, provider_hosts=["different.example"])


def test_identical_retry_is_idempotent(tmp_path):
    ledger, references, matrix = target(tmp_path)
    first = qualify(ledger, references, matrix)
    assert qualify(
        ledger, references, matrix, assessed_at=first["assessed_at"]
    ) == first


@pytest.mark.parametrize("change", [
    {"provider_approved": True}, {"subscription_purchased": True},
    {"credentials_recorded": True}, {"evaluation_dataset_opened": True},
    {"replay_executed": True}, {"performance_claim_allowed": True},
    {"broker_submission_enabled": True}, {"live_trading_enabled": True},
])
def test_rehashed_tampering_cannot_approve_purchase_or_enable_trading(tmp_path, change):
    ledger, references, matrix = target(tmp_path)
    record = qualify(ledger, references, matrix)
    record.update(change)
    from core.portfolio import historical_provider_qualification as module
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    ledger.path.write_text(json.dumps(record) + "\n")
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        ledger.verify()
