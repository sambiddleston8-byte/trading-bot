from pathlib import Path

from core.portfolio.historical_provider_qualification import (
    REQUIRED_CAPABILITIES,
    REQUIRED_CORPORATE_ACTIONS,
    REQUIRED_TIME_FIELDS,
    REQUIRED_UNIVERSES,
    REQUIRED_USES,
)


DOCUMENT = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "HISTORICAL_PROVIDER_SAMPLE_REQUEST.md"
)


def test_provider_request_tracks_every_qualification_ledger_requirement():
    text = DOCUMENT.read_text(encoding="utf-8")

    for value in (
        REQUIRED_CAPABILITIES
        | REQUIRED_CORPORATE_ACTIONS
        | REQUIRED_TIME_FIELDS
        | REQUIRED_UNIVERSES
        | REQUIRED_USES
    ):
        assert f"`{value}`" in text


def test_provider_request_keeps_candidates_evidence_and_spending_fail_closed():
    text = DOCUMENT.read_text(encoding="utf-8")

    for provider in ("Intrinio", "Polygon.io", "Databento", "Nasdaq"):
        assert provider in text
    for evidence_kind in ("TERMS", "DOCUMENTATION", "SAMPLE"):
        assert f"`{evidence_kind}`" in text
    for status in ("PASS", "FAIL", "UNRESOLVED", "PASSED_AWAITING_HUMAN_APPROVAL"):
        assert f"`{status}`" in text
    assert "Do not include API keys" in text
    assert "does not approve" in text
    assert "separate human approval" in text
    assert "Do not purchase" in text
    assert "redistribution_allowed" in text
    assert "absence is not treated as consent" in text
    assert "credential-free HTTPS source URLs" in text
    assert "Email-only attachments cannot" in text
    assert "never invent a `source_uri`" in text
    assert "consistent pseudonymization" in text
    assert "cross-record linkage is not preserved" in text


def test_provider_request_preserves_cloud_native_delivery_boundary():
    text = DOCUMENT.read_text(encoding="utf-8")

    assert "HTTPS REST API or reproducible versioned flat files" in text
    assert "Windows-only updater" in text
    assert "macOS/Linux" in text
    assert "canonical `effective_at`" in text
    assert "canonical `available_at`" in text
