from pathlib import Path

import pytest

from core.orchestration.campaign_stage_readiness import (
    LEGACY_EVIDENCE_ARCHIVE,
    credential_file_present,
    stage_0_readiness,
)
from core.research.campaign_v2_revision_2_capture_specification import capture_specification
from core.research import campaign_v2_revision_2_registered_chain as chain
from core.orchestration import campaign_v2_revision_2_account_entitlement as legacy


def test_stage_zero_is_exact_two_factor_conjunction():
    for credential, authorization, complete in (
        (False, False, False), (True, False, False), (False, True, False), (True, True, True)
    ):
        result = stage_0_readiness(
            credential_present=credential, provider_use_authorized=authorization
        )
        assert result["stage_0_complete"] is complete
    with pytest.raises(TypeError):
        stage_0_readiness(credential_present=1, provider_use_authorized=False)


def test_credential_probe_never_reads_and_requires_owner_only_regular_file(tmp_path: Path):
    key = tmp_path / "key"
    key.write_text("synthetic-not-a-key")
    key.chmod(0o600)
    assert credential_file_present(key) is True
    key.chmod(0o644)
    assert credential_file_present(key) is False
    key.unlink()
    key.symlink_to(tmp_path / "missing")
    assert credential_file_present(key) is False


def test_legacy_modules_are_archived_without_deletion_or_activation():
    assert len(LEGACY_EVIDENCE_ARCHIVE) == 5
    assert {item["status"] for item in LEGACY_EVIDENCE_ARCHIVE} == {"READ_ONLY_HISTORICAL_RECORD"}
    for name in (
        "PROPOSAL_SHA256", "PREREGISTRATION_ID", "PREREGISTRATION_RECORD_SHA256",
        "PUBLIC_DOCUMENTATION_EVIDENCE_ID", "PUBLIC_DOCUMENTATION_RECORD_SHA256",
        "PUBLIC_DOCUMENTATION_BUNDLE_SHA256",
    ):
        assert getattr(chain, name) == getattr(legacy, name)


def test_requested_capture_record_is_deterministic_bounded_and_inert():
    first = capture_specification()
    assert first == capture_specification()
    assert first["symbols"] == ["AAPL", "MSFT", "SPY"]
    assert first["requested_window"] == {"start": "2024-09-01", "end": "2025-07-31"}
    assert [item["name"] for item in first["datasets"]] == ["DAILY_BARS", "DIVIDENDS", "STOCK_SPLITS"]
    assert first["provider_use_authorized"] is False
    assert first["provider_request_allowed"] is False
    assert "REQUESTED_START_DIFFERS_FROM_REGISTERED_REVISION_2_WINDOW" in first["block_reasons"]
