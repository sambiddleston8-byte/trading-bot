from datetime import datetime, timedelta, timezone
import hashlib
import json
import os

import pytest

from core.decision_ledger import LedgerIntegrityError
from core.orchestration.campaign_v2_revision_2_account_entitlement import (
    FIXED_FALSE, PREREGISTRATION_ID, PREREGISTRATION_RECORD_SHA256, PROPOSAL_SHA256,
    PUBLIC_DOCUMENTATION_BUNDLE_SHA256, PUBLIC_DOCUMENTATION_EVIDENCE_ID,
    PUBLIC_DOCUMENTATION_RECORD_SHA256,
)
from core.orchestration.campaign_v2_revision_2_dashboard_capture import (
    CampaignV2Revision2DashboardCaptureLedger, build_dashboard_capture_evidence,
)

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _html(plan="Stocks Basic", customer="Individual", price="$0/m"):
    return f"<!doctype html><html><body><h1>{plan}</h1><p>{customer}</p><span>{price}</span></body></html>".encode()


def _capture(**changes):
    value = {"captured_at": NOW - timedelta(minutes=2), "source_uri": "https://massive.com/dashboard/plans",
             "payload_bytes": _html(), "authenticated_session_attested": True}
    value.update(changes)
    return value


def _parent(**changes):
    value = {"evidence_id": PUBLIC_DOCUMENTATION_EVIDENCE_ID, "record_hash": PUBLIC_DOCUMENTATION_RECORD_SHA256,
             "preregistration_id": PREREGISTRATION_ID, "preregistration_record_sha256": PREREGISTRATION_RECORD_SHA256,
             "proposal_sha256": PROPOSAL_SHA256,
             "public_documentation_evidence": {"evidence_bundle_sha256": PUBLIC_DOCUMENTATION_BUNDLE_SHA256},
             "authenticated_account_evidence_collected": False, "data_calls_allowed": False,
             "evaluation_allowed": False, "orders_submitted": False}
    value.update(changes)
    return value


def _ledger(tmp_path, parent=None):
    return CampaignV2Revision2DashboardCaptureLedger(tmp_path / "private/evidence.jsonl", tmp_path / "private/blobs",
        repository_root=tmp_path, clock=lambda: NOW, public_evidence_resolver=lambda: parent or _parent())


def test_derives_only_visible_labels_and_keeps_semantics_unresolved():
    evidence = build_dashboard_capture_evidence(_capture(), assessed_at=NOW)
    assert evidence["observed_labels"] == {"plan_label": "Stocks Basic", "customer_type_label": "Individual", "displayed_price_label": "$0/m"}
    for field in ("account_identity_proven", "account_to_plan_binding_proven", "daily_bars_access_confirmed",
                  "dividends_access_confirmed", "stock_splits_access_confirmed", "historical_lookback_start_proven",
                  "zero_incremental_cost_proven", "evidence_complete_for_separate_capture_activation_record"):
        assert evidence[field] is False


def test_seals_owner_only_exact_bytes_with_no_authority(tmp_path):
    ledger = _ledger(tmp_path)
    record = ledger.register(_capture(), assessed_at=NOW)
    assert all(record[field] is False for field in FIXED_FALSE)
    assert ledger.records() == [record]
    assert ledger.path.stat().st_mode & 0o777 == 0o600
    blob = ledger.blob_root / record["blob_reference"]["blob_relative_path"]
    assert blob.stat().st_mode & 0o777 == 0o400
    assert blob.read_bytes() == _html()


@pytest.mark.parametrize("change", [
    {"payload_bytes": _html(plan="Stocks Advanced")}, {"payload_bytes": _html(price="$0/m $0/m")},
    {"payload_bytes": b'{"plan":"Stocks Basic"}'}, {"authenticated_session_attested": False},
    {"source_uri": "https://example.com/dashboard"}, {"source_uri": "https://massive.com/dashboard?token=secret"},
    {"payload_bytes": b"<html><body>Stocks Basic Individual $0/m api_key=secret</body></html>"},
])
def test_rejects_unqualified_or_unsafe_capture(change):
    with pytest.raises(ValueError):
        build_dashboard_capture_evidence(_capture(**change), assessed_at=NOW)


def test_hidden_text_cannot_prove_visible_label():
    payload = b"<html><body>Stocks Basic Individual<script>$0/m</script></body></html>"
    with pytest.raises(ValueError, match="displayed_price_label"):
        build_dashboard_capture_evidence(_capture(payload_bytes=payload), assessed_at=NOW)


@pytest.mark.parametrize("change", [{"record_hash": "f" * 64}, {"proposal_sha256": "f" * 64},
                                      {"authenticated_account_evidence_collected": True}, {"data_calls_allowed": True}])
def test_rejects_substituted_or_authorizing_public_parent(tmp_path, change):
    with pytest.raises(LedgerIntegrityError, match="parent"):
        _ledger(tmp_path, _parent(**change)).register(_capture(), assessed_at=NOW)


def test_tampering_replacement_and_permissions_fail_closed(tmp_path):
    ledger = _ledger(tmp_path)
    record = ledger.register(_capture(), assessed_at=NOW)
    blob = ledger.blob_root / record["blob_reference"]["blob_relative_path"]
    blob.chmod(0o600); blob.write_bytes(_html(price="$1/m")); blob.chmod(0o400)
    with pytest.raises((LedgerIntegrityError, ValueError)):
        ledger.records()
    fresh = _ledger(tmp_path / "fresh")
    fresh.register(_capture(), assessed_at=NOW)
    with pytest.raises(LedgerIntegrityError, match="Different"):
        fresh.register(_capture(payload_bytes=_html() + b"<!-- changed -->"), assessed_at=NOW)
    os.chmod(fresh.path, 0o644)
    with pytest.raises(LedgerIntegrityError, match="unsafe"):
        fresh.records()


def test_rejects_rehashed_outer_field_injection(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.register(_capture(), assessed_at=NOW)
    row = json.loads(ledger.path.read_text())
    row["proposal_sha256"] = "f" * 64
    row["evidence_complete_for_separate_capture_activation_record"] = True
    material = {key: value for key, value in row.items() if key != "record_hash"}
    row["record_hash"] = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    ledger.path.write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    os.chmod(ledger.path, 0o600)
    with pytest.raises(LedgerIntegrityError, match="changed"):
        ledger.records()


def test_rejects_unsafe_recording_clock(tmp_path):
    ledger = CampaignV2Revision2DashboardCaptureLedger(tmp_path / "private/evidence.jsonl", tmp_path / "private/blobs",
        repository_root=tmp_path, clock=lambda: NOW - timedelta(hours=1), public_evidence_resolver=_parent)
    with pytest.raises(ValueError, match="recording clock"):
        ledger.register(_capture(), assessed_at=NOW)


def test_rejects_uri_controls_and_symlink_without_chmod_side_effect(tmp_path):
    with pytest.raises(ValueError, match="canonical"):
        build_dashboard_capture_evidence(_capture(source_uri="https://mass\nive.com/dashboard"), assessed_at=NOW)
    target = tmp_path / "target"
    target.mkdir(mode=0o755)
    ledger = _ledger(tmp_path)
    ledger.blob_root.parent.mkdir(parents=True, mode=0o700)
    ledger.blob_root.symlink_to(target, target_is_directory=True)
    with pytest.raises(LedgerIntegrityError, match="unsafe"):
        ledger.register(_capture(), assessed_at=NOW)
    assert target.stat().st_mode & 0o777 == 0o755
