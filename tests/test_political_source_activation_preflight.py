import pytest

from core.political import assess_source_activation


def assess(**overrides):
    values = {
        "source": "OFFICIAL_HOUSE",
        "intended_use": "PRIVATE_NONCOMMERCIAL_RESEARCH",
        "terms_uri": "https://disclosures-clerk.house.gov/FinancialDisclosure/ViewSearch",
        "terms_content_sha256": "a" * 64,
        "terms_review_reference": "LEGAL-REVIEW-001",
        "reviewed_on": "2026-08-13",
        "reviewer_role": "qualified legal reviewer",
        "legal_use_permitted": True,
        "automated_access_permitted": True,
        "licensed_feed_contract_in_force": False,
        "redistribution_permitted": False,
    }
    values.update(overrides)
    return assess_source_activation(**values)


def test_official_source_requires_documented_legal_and_automation_approval():
    result = assess()
    assert result["status"] == "READY_FOR_SEPARATE_CONNECTOR_REVIEW"
    assert result["source_approved"] is True
    assert result["connector_implemented"] is False
    assert result["data_downloaded"] is False
    assert result["scraper_enabled"] is False
    assert result["copy_trade_allowed"] is False
    assert result["live_trading_enabled"] is False


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"legal_use_permitted": False}, "legal review"),
        ({"automated_access_permitted": False}, "automated access"),
        (
            {
                "source": "CAPITOL_TRADES",
                "terms_uri": "https://www.capitoltrades.com/terms",
                "licensed_feed_contract_in_force": False,
            },
            "licensed feed",
        ),
    ],
)
def test_unresolved_terms_or_license_blocks(overrides, fragment):
    result = assess(**overrides)
    assert result["status"] == "BLOCKED"
    assert result["source_approved"] is False
    assert fragment in " ".join(result["reasons"])
    assert result["data_downloaded"] is False


def test_licensed_provider_can_only_reach_connector_review_not_ingestion():
    result = assess(
        source="CAPITOL_TRADES",
        intended_use="COMMERCIAL_INVESTMENT_RESEARCH",
        terms_uri="https://www.capitoltrades.com/terms",
        licensed_feed_contract_in_force=True,
        redistribution_permitted=False,
    )
    assert result["status"] == "READY_FOR_SEPARATE_CONNECTOR_REVIEW"
    assert result["source_approved"] is True
    assert result["connector_implemented"] is False
    assert result["scraper_enabled"] is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"terms_uri": "http://example.com/terms"},
        {"terms_content_sha256": "bad"},
        {"reviewed_on": "13/08/2026"},
        {"reviewed_on": "2999-01-01"},
        {"source": "UNKNOWN"},
        {"intended_use": "ANYTHING"},
    ],
)
def test_invalid_review_evidence_is_rejected(overrides):
    with pytest.raises(ValueError):
        assess(**overrides)


def test_assessment_fingerprint_changes_with_use_or_terms():
    first = assess()
    second = assess(intended_use="COMMERCIAL_INVESTMENT_RESEARCH")
    assert first["assessment_id"] != second["assessment_id"]
