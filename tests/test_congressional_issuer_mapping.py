import pytest

from core.political import create_issuer_mapping, resolve_disclosure_issuer


def mapping(**overrides):
    values = {
        "ticker": "OLD",
        "asset_name": "Example Corporation Common Stock",
        "issuer_identifier_type": "CIK",
        "issuer_identifier": "0000123456",
        "effective_from": "2025-01-01",
        "effective_to": "2026-06-30",
        "mapping_known_at": "2025-01-02T00:00:00+00:00",
        "source_url": "https://www.sec.gov/example",
        "evidence_sha256": "a" * 64,
        "review_reference": "MAP-REVIEW-1",
        "reviewed_by": "synthetic-test",
    }
    values.update(overrides)
    return create_issuer_mapping(**values)


def disclosure(**overrides):
    values = {
        "disclosure_id": "CTR-SYNTHETIC",
        "ticker": "OLD",
        "asset_name": "Example Corporation Common Stock",
        "transaction_date": "2026-01-10",
        "historical_point_in_time_signal_at": "2026-02-20T00:00:00+00:00",
    }
    values.update(overrides)
    return values


def test_resolves_mapping_effective_and_known_at_disclosure_time():
    result = resolve_disclosure_issuer(disclosure(), [mapping()])
    assert result["status"] == "RESOLVED_RESEARCH_IDENTITY"
    assert result["issuer_identifier"] == "0000123456"
    assert result["mapping_known_by_disclosure_availability"] is True
    assert result["automatic_recommendation"] is False
    assert result["live_trading_enabled"] is False


def test_rejects_hindsight_mapping_learned_after_disclosure():
    late = mapping(mapping_known_at="2026-03-01T00:00:00+00:00")
    result = resolve_disclosure_issuer(disclosure(), [late])
    assert result["status"] == "UNRESOLVED"
    assert "knowable" in " ".join(result["reasons"])


def test_rejects_mapping_outside_transaction_effective_period():
    result = resolve_disclosure_issuer(
        disclosure(transaction_date="2026-07-01"), [mapping()]
    )
    assert result["status"] == "UNRESOLVED"


def test_mapping_can_be_known_before_its_effective_period():
    announced = mapping(
        effective_from="2026-01-15",
        effective_to=None,
        mapping_known_at="2026-01-01T00:00:00+00:00",
    )
    result = resolve_disclosure_issuer(
        disclosure(transaction_date="2026-01-20"), [announced]
    )
    assert result["status"] == "RESOLVED_RESEARCH_IDENTITY"


def test_asset_name_mismatch_fails_closed():
    result = resolve_disclosure_issuer(
        disclosure(asset_name="Different Security"), [mapping()]
    )
    assert result["status"] == "UNRESOLVED"


def test_tampered_mapping_fingerprint_fails_closed():
    tampered = mapping()
    tampered["issuer_identifier"] = "0000999999"
    assert resolve_disclosure_issuer(disclosure(), [tampered])["status"] == "UNRESOLVED"


def test_conflicting_identifiers_fail_closed():
    other = mapping(
        issuer_identifier="0000999999",
        evidence_sha256="b" * 64,
        review_reference="MAP-REVIEW-2",
    )
    result = resolve_disclosure_issuer(disclosure(), [mapping(), other])
    assert result["status"] == "UNRESOLVED"
    assert "Conflicting" in " ".join(result["reasons"])


def test_same_identifier_from_two_evidence_records_is_unambiguous():
    corroborating = mapping(
        evidence_sha256="b" * 64,
        review_reference="MAP-REVIEW-2",
    )
    assert resolve_disclosure_issuer(disclosure(), [mapping(), corroborating])["status"] == "RESOLVED_RESEARCH_IDENTITY"


@pytest.mark.parametrize(
    "overrides",
    [
        {"ticker": "BAD TICKER"},
        {"issuer_identifier_type": "UNKNOWN"},
        {"effective_to": "2024-01-01"},
        {"mapping_known_at": "2999-01-01T00:00:00+00:00"},
        {"source_url": "http://example.com"},
        {"evidence_sha256": "bad"},
    ],
)
def test_invalid_mapping_evidence_is_rejected(overrides):
    with pytest.raises(ValueError):
        mapping(**overrides)
