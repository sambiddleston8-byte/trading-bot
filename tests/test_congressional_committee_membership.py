import pytest

from core.political import (
    create_committee_membership,
    membership_available_for_disclosure,
)


def membership(**overrides):
    values = {
        "chamber": "HOUSE",
        "politician_name": "Example Member",
        "committee_name": "Example Committee",
        "subcommittee_name": None,
        "membership_role": "MEMBER",
        "effective_from": "2025-01-01",
        "effective_to": "2026-12-31",
        "evidence_known_at": "2025-01-02T00:00:00+00:00",
        "source_url": "https://example.house.gov/membership",
        "evidence_sha256": "a" * 64,
        "review_reference": "COMMITTEE-REVIEW-1",
        "reviewed_by": "synthetic-test",
    }
    values.update(overrides)
    return create_committee_membership(**values)


def disclosure(**overrides):
    values = {
        "chamber": "HOUSE",
        "politician_name": "Example Member",
        "transaction_date": "2026-01-10",
        "historical_point_in_time_signal_at": "2026-02-20T00:00:00+00:00",
    }
    values.update(overrides)
    return values


def test_records_membership_without_inferring_investment_advantage():
    result = membership()
    assert result["status"] == "MEMBERSHIP_EVIDENCE_ONLY"
    assert result["committee_relevance_inferred"] is False
    assert result["investment_advantage_inferred"] is False
    assert result["automatic_recommendation"] is False
    assert membership_available_for_disclosure(disclosure(), result) is True


@pytest.mark.parametrize(
    "disclosure_changes,membership_changes",
    [
        ({"chamber": "SENATE"}, {}),
        ({"politician_name": "Different Member"}, {}),
        ({"transaction_date": "2027-01-01"}, {}),
        ({}, {"evidence_known_at": "2026-03-01T00:00:00+00:00"}),
    ],
)
def test_mismatch_hindsight_or_out_of_period_membership_is_unavailable(
    disclosure_changes, membership_changes
):
    assert membership_available_for_disclosure(
        disclosure(**disclosure_changes), membership(**membership_changes)
    ) is False


def test_tampered_relevance_claim_is_unavailable():
    changed = membership()
    changed["committee_relevance_inferred"] = True
    assert membership_available_for_disclosure(disclosure(), changed) is False


def test_tampered_membership_identity_is_unavailable():
    changed = membership()
    changed["committee_name"] = "Different Committee"
    assert membership_available_for_disclosure(disclosure(), changed) is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"chamber": "UNKNOWN"},
        {"membership_role": "UNKNOWN"},
        {"effective_to": "2024-01-01"},
        {"evidence_known_at": "2999-01-01T00:00:00+00:00"},
        {"source_url": "http://example.com"},
        {"evidence_sha256": "bad"},
    ],
)
def test_invalid_membership_evidence_is_rejected(overrides):
    with pytest.raises(ValueError):
        membership(**overrides)
