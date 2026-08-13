import json
from datetime import datetime, timezone

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.portfolio.historical_data_source_approval import HistoricalDataSourceApprovalLedger
from tests.test_historical_provider_qualification import target as qualification_target, qualify


BASE = {
    "provider_name": "Example Historical Data",
    "product_or_plan": "Research Plan",
    "provider_url": "https://provider.example/product",
    "terms_url": "https://provider.example/terms",
    "terms_content_sha256": "a" * 64,
    "terms_review_reference": "Human review of terms dated 2026-08-13",
    "approved_data_hosts": ["provider.example"],
    "approved_universes": ["SP500", "NASDAQ100"],
    "permitted_uses": ["LOCAL_RESEARCH", "HISTORICAL_REPLAY", "INTERNAL_DERIVED_RESULTS"],
    "coverage_not_before": "2000-01-01",
    "coverage_not_after": "2025-12-31",
    "historical_constituents_included": True,
    "removed_securities_included": True,
    "delisted_securities_included": True,
    "corporate_actions_included": True,
    "terminal_outcome_support_documented": True,
    "redistribution_allowed": False,
    "approved_by": "Sam",
}


def ledger(tmp_path):
    qualifications, references, matrix = qualification_target(tmp_path / "qualification")
    qualification = qualify(qualifications, references, matrix)
    target = HistoricalDataSourceApprovalLedger(tmp_path / "approvals.jsonl", qualifications)
    target.qualification = qualification
    return target


def approve(target, **overrides):
    values = {
        **BASE,
        "qualification_id": target.qualification["qualification_id"],
        "qualification_record_hash": target.qualification["record_hash"],
        "terms_url": next(
            item["source_uri"] for item in target.qualification_ledger.content_ledger.verify()
            if item["content_evidence_id"] == next(
                ref["content_evidence_id"] for ref in target.qualification["evidence_references"]
                if ref["evidence_kind"] == "TERMS"
            )
        ),
        "terms_content_sha256": next(
            item["source_input_sha256"] for item in target.qualification_ledger.content_ledger.verify()
            if item["content_evidence_id"] == next(
                ref["content_evidence_id"] for ref in target.qualification["evidence_references"]
                if ref["evidence_kind"] == "TERMS"
            )
        ),
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    values.update(overrides)
    return target.approve(**values)


def test_records_human_source_and_terms_approval_without_buying_or_fetching(tmp_path):
    target = ledger(tmp_path)
    record = approve(target)
    assert record["previous_hash"] == GENESIS_HASH
    assert record["approved_universes"] == ["NASDAQ100", "SP500"]
    for field in (
        "source_quality_independently_proven", "subscription_purchased",
        "credentials_recorded", "data_fetched", "coverage_certificate_created",
        "replay_executed", "performance_claim_allowed", "broker_submission_enabled",
        "live_trading_enabled",
    ):
        assert record[field] is False
    assert target.verify() == [record]


@pytest.mark.parametrize("field,value,fragment", [
    ("historical_constituents_included", False, "must cover"),
    ("removed_securities_included", False, "must cover"),
    ("delisted_securities_included", False, "must cover"),
    ("corporate_actions_included", False, "must cover"),
    ("terminal_outcome_support_documented", False, "must cover"),
    ("approved_universes", ["RUSSELL2000"], "unsupported"),
    ("permitted_uses", ["REDISTRIBUTION"], "unsupported"),
    ("approved_data_hosts", [], "cannot be empty"),
    ("approved_data_hosts", ["https://provider.example/path"], "bare lowercase"),
    ("provider_url", "http://provider.example", "HTTPS"),
    ("coverage_not_after", "1999-12-31", "later"),
])
def test_inadequate_or_unlicensed_source_cannot_be_approved(tmp_path, field, value, fragment):
    with pytest.raises(ValueError, match=fragment):
        approve(ledger(tmp_path), **{field: value})


def test_identical_approval_is_idempotent(tmp_path):
    target = ledger(tmp_path)
    first = approve(target)
    retry = {key: value for key, value in first.items() if key not in {
        "schema_version", "policy_version", "approval_id", "record_type", "status",
        "source_quality_independently_proven", "subscription_purchased",
        "credentials_recorded", "data_fetched", "coverage_certificate_created",
        "replay_executed", "performance_claim_allowed", "broker_submission_enabled",
        "live_trading_enabled", "previous_hash", "record_hash",
    }}
    assert target.approve(**retry) == first


def test_approval_cannot_bypass_or_exceed_passing_qualification(tmp_path):
    target = ledger(tmp_path)
    with pytest.raises(ValueError, match="exact verified"):
        approve(target, qualification_record_hash="0" * 64)
    with pytest.raises(ValueError, match="derive"):
        approve(target, approved_data_hosts=["different.example"])


@pytest.mark.parametrize("change", [
    {"subscription_purchased": True}, {"data_fetched": True},
    {"coverage_certificate_created": True}, {"replay_executed": True},
    {"performance_claim_allowed": True}, {"broker_submission_enabled": True},
    {"live_trading_enabled": True},
])
def test_rehashed_tampering_cannot_purchase_fetch_or_grant_authority(tmp_path, change):
    target = ledger(tmp_path)
    record = approve(target); record.update(change)
    from core.portfolio import historical_data_source_approval as module
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    target.path.write_text(json.dumps(record) + "\n")
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        target.verify()
