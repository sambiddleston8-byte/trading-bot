from datetime import datetime, timedelta, timezone
from collections.abc import Mapping
import hashlib
import json
import os

import pytest

import core.orchestration.campaign_v2_revision_2_account_plan_supplement as supplement_module
from core.decision_ledger import LedgerIntegrityError
from core.orchestration.campaign_v2_revision_2_account_entitlement import FIXED_FALSE
from core.orchestration.campaign_v2_revision_2_account_plan_supplement import (
    CAPTURE_KIND,
    DASHBOARD_BUNDLE_SHA256,
    DASHBOARD_EVIDENCE_ID,
    DASHBOARD_PAYLOAD_SHA256,
    DASHBOARD_RECORD_SHA256,
    SOURCE_URI,
    CampaignV2Revision2AccountPlanSupplementLedger,
    build_account_plan_supplement,
)


NOW = datetime.now(timezone.utc).replace(microsecond=0)
IDENTITY = "synthetic.owner@example.test"


def _html(identity=IDENTITY, source="github"):
    return (
        '<tr role="row"><td>Connected Account</td>'
        f'<td>{identity}<span>{source}</span></td></tr>'
    ).encode()


def _capture(**changes):
    value = {
        "account_captured_at": NOW - timedelta(seconds=23),
        "plan_row_reobserved_at": NOW,
        "source_uri": SOURCE_URI,
        "capture_kind": CAPTURE_KIND,
        "payload_bytes": _html(),
        "plan_row_payload_sha256": DASHBOARD_PAYLOAD_SHA256,
        "authenticated_session_attested": True,
        "same_browser_session_attested": True,
        "sole_connected_account_row_attested": True,
    }
    value.update(changes)
    return value


def _parent(**changes):
    value = {
        "evidence_id": DASHBOARD_EVIDENCE_ID,
        "record_hash": DASHBOARD_RECORD_SHA256,
        "dashboard_capture_evidence": {
            "payload_sha256": DASHBOARD_PAYLOAD_SHA256,
            "evidence_bundle_sha256": DASHBOARD_BUNDLE_SHA256,
        },
        **{field: False for field in FIXED_FALSE},
    }
    value.update(changes)
    return value


def _ledger(tmp_path, parent=None):
    return CampaignV2Revision2AccountPlanSupplementLedger(
        tmp_path / "private/evidence.jsonl",
        tmp_path / "private/blobs",
        repository_root=tmp_path,
        clock=lambda: NOW,
        dashboard_resolver=lambda: parent or _parent(),
    )


def test_seals_only_identity_hash_and_keeps_all_semantics_unresolved():
    evidence = build_account_plan_supplement(_capture(), assessed_at=NOW)
    assert evidence["account_identity_sha256"] == hashlib.sha256(IDENTITY.encode()).hexdigest()
    assert evidence["bracket_seconds"] == "23.000000"
    assert evidence["account_to_plan_same_session_locally_attested"] is True
    assert evidence["account_identity_cleartext_in_ledger_record"] is False
    assert evidence["account_identity_cleartext_retained_in_local_blob"] is True
    assert evidence["account_to_plan_binding_cryptographically_verified"] is False
    serialized = json.dumps(evidence)
    assert IDENTITY not in serialized
    for field in (
        "provider_origin_cryptographically_verified",
        "daily_bars_access_confirmed",
        "dividends_access_confirmed",
        "stock_splits_access_confirmed",
        "historical_lookback_start_proven",
        "zero_incremental_cost_proven",
        "evidence_complete_for_capture_activation",
    ):
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
    assert record["blob_reference"]["contains_cleartext_identity"] is True
    assert IDENTITY not in ledger.path.read_text()


@pytest.mark.parametrize("change", [
    {"account_captured_at": NOW + timedelta(microseconds=1)},
    {"account_captured_at": NOW - timedelta(seconds=31)},
    {"plan_row_reobserved_at": NOW + timedelta(seconds=1)},
    {"source_uri": "https://massive.com/dashboard/plans"},
    {"capture_kind": "AUTHENTICATED_DASHBOARD_PAGE_HTML"},
    {"plan_row_payload_sha256": "f" * 64},
    {"authenticated_session_attested": False},
    {"same_browser_session_attested": False},
    {"sole_connected_account_row_attested": False},
    {"payload_bytes": b"<tr><td>Connected Account</td><td>github</td></tr>"},
    {"payload_bytes": _html(identity="not-an-email")},
    {"payload_bytes": _html(source="google")},
    {"payload_bytes": _html() + _html()},
    {"payload_bytes": b'<tr><td>Connected Account</td><td>synthetic.owner@example.test github extra</td></tr>'},
])
def test_rejects_unqualified_ambiguous_or_out_of_bracket_capture(change):
    with pytest.raises(ValueError):
        build_account_plan_supplement(_capture(**change), assessed_at=NOW)


@pytest.mark.parametrize("change", [
    {"evidence_id": "substituted"},
    {"record_hash": "f" * 64},
    {"dashboard_capture_evidence": {
        "payload_sha256": "f" * 64,
        "evidence_bundle_sha256": DASHBOARD_BUNDLE_SHA256,
    }},
    {"data_calls_allowed": True},
])
def test_rejects_substituted_or_authorizing_dashboard_parent(tmp_path, change):
    with pytest.raises(LedgerIntegrityError, match="parent"):
        _ledger(tmp_path, _parent(**change)).register(_capture(), assessed_at=NOW)


def test_tampering_replacement_and_permissions_fail_closed(tmp_path):
    ledger = _ledger(tmp_path)
    record = ledger.register(_capture(), assessed_at=NOW)
    blob = ledger.blob_root / record["blob_reference"]["blob_relative_path"]
    blob.chmod(0o600)
    blob.write_bytes(_html(identity="attacker@example.test"))
    blob.chmod(0o400)
    with pytest.raises(LedgerIntegrityError):
        ledger.records()

    fresh = _ledger(tmp_path / "fresh")
    fresh.register(_capture(), assessed_at=NOW)
    with pytest.raises(LedgerIntegrityError, match="replace"):
        fresh.register(_capture(payload_bytes=_html().replace(b"role=", b"data-x=\"1\" role=")), assessed_at=NOW)
    os.chmod(fresh.path, 0o644)
    with pytest.raises(LedgerIntegrityError, match="unsafe"):
        fresh.records()


def test_rejects_rehashed_outer_field_injection(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.register(_capture(), assessed_at=NOW)
    row = json.loads(ledger.path.read_text())
    row["data_calls_allowed"] = True
    material = {key: value for key, value in row.items() if key != "record_hash"}
    row["record_hash"] = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    ledger.path.write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    os.chmod(ledger.path, 0o600)
    with pytest.raises(LedgerIntegrityError, match="changed"):
        ledger.records()


def test_rejects_unsafe_recording_clock(tmp_path):
    ledger = CampaignV2Revision2AccountPlanSupplementLedger(
        tmp_path / "private/evidence.jsonl",
        tmp_path / "private/blobs",
        repository_root=tmp_path,
        clock=lambda: NOW - timedelta(hours=1),
        dashboard_resolver=_parent,
    )
    with pytest.raises(ValueError, match="recording clock"):
        ledger.register(_capture(), assessed_at=NOW)


def test_snapshots_mapping_before_validation_and_persistence(tmp_path):
    class ChangingCapture(Mapping):
        def __init__(self, value):
            self.value = value
            self.reads = 0

        def __iter__(self):
            return iter(self.value)

        def __len__(self):
            return len(self.value)

        def __getitem__(self, key):
            value = self.value[key]
            if key == "payload_bytes":
                self.reads += 1
                return value if self.reads == 1 else _html(identity="attacker@example.test")
            return value

    capture = ChangingCapture(_capture())
    record = _ledger(tmp_path).register(capture, assessed_at=NOW)
    assert capture.reads == 1
    assert record["account_plan_supplement"]["payload_sha256"] == hashlib.sha256(_html()).hexdigest()


def test_recovers_identical_completed_orphan_blob(tmp_path):
    ledger = _ledger(tmp_path)
    digest = hashlib.sha256(_html()).hexdigest()
    blob = ledger.blob_root / "sha256" / digest[:2] / f"{digest}.html"
    blob.parent.mkdir(parents=True, mode=0o700)
    os.chmod(ledger.blob_root, 0o700)
    os.chmod(ledger.blob_root / "sha256", 0o700)
    os.chmod(blob.parent, 0o700)
    blob.write_bytes(_html())
    os.chmod(blob, 0o400)
    assert ledger.register(_capture(), assessed_at=NOW)["blob_reference"]["payload_sha256"] == digest


def test_creation_modes_are_independent_of_restrictive_umask(tmp_path):
    previous = os.umask(0o277)
    try:
        record = _ledger(tmp_path).register(_capture(), assessed_at=NOW)
    finally:
        os.umask(previous)
    ledger = _ledger(tmp_path)
    blob = ledger.blob_root / record["blob_reference"]["blob_relative_path"]
    assert ledger.path.stat().st_mode & 0o777 == 0o600
    assert blob.stat().st_mode & 0o777 == 0o400


def test_rejects_non_integer_blob_length_even_when_numerically_equal(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.register(_capture(), assessed_at=NOW)
    row = json.loads(ledger.path.read_text())
    row["blob_reference"]["byte_length"] = float(row["blob_reference"]["byte_length"])
    material = {key: value for key, value in row.items() if key != "record_hash"}
    row["record_hash"] = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    ledger.path.write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    os.chmod(ledger.path, 0o600)
    with pytest.raises(LedgerIntegrityError, match="unsafe"):
        ledger.records()


def test_missing_parent_and_malformed_timestamp_are_integrity_errors(tmp_path):
    missing_parent = CampaignV2Revision2AccountPlanSupplementLedger(
        tmp_path / "missing/evidence.jsonl",
        tmp_path / "missing/blobs",
        repository_root=tmp_path,
        clock=lambda: NOW,
        dashboard_resolver=lambda: ()[0],
    )
    with pytest.raises(LedgerIntegrityError, match="missing"):
        missing_parent.register(_capture(), assessed_at=NOW)

    ledger = _ledger(tmp_path / "malformed")
    ledger.register(_capture(), assessed_at=NOW)
    row = json.loads(ledger.path.read_text())
    row["recorded_at"] = "not-a-timestamp"
    material = {key: value for key, value in row.items() if key != "record_hash"}
    row["record_hash"] = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    ledger.path.write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    os.chmod(ledger.path, 0o600)
    with pytest.raises(LedgerIntegrityError, match="changed"):
        ledger.records()


def test_rejects_non_mapping_nested_parent_and_normalizes_seal_conflict(tmp_path):
    with pytest.raises(LedgerIntegrityError, match="invalid"):
        _ledger(tmp_path / "parent", _parent(dashboard_capture_evidence=None)).register(
            _capture(), assessed_at=NOW,
        )

    ledger = _ledger(tmp_path / "conflict")
    ledger.path.parent.mkdir(parents=True, mode=0o700)
    ledger.path.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(LedgerIntegrityError, match="could not be sealed"):
        ledger.register(_capture(), assessed_at=NOW)


def test_failed_ledger_write_never_publishes_partial_target(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    original = supplement_module._write_all

    def fail_ledger_write(descriptor, payload):
        if payload.startswith(b"{"):
            raise OSError("synthetic interrupted ledger write")
        return original(descriptor, payload)

    monkeypatch.setattr(supplement_module, "_write_all", fail_ledger_write)
    with pytest.raises(OSError, match="synthetic interrupted"):
        ledger.register(_capture(), assessed_at=NOW)
    assert not ledger.path.exists()
    assert not list(ledger.path.parent.glob(f".{ledger.path.name}.*.tmp"))


def test_idempotence_and_cleartext_disclosure_tamper_fail_closed(tmp_path):
    ledger = _ledger(tmp_path)
    record = ledger.register(_capture(), assessed_at=NOW)
    assert ledger.register(_capture(), assessed_at=NOW) == record

    row = json.loads(ledger.path.read_text())
    row["blob_reference"]["contains_cleartext_identity"] = False
    material = {key: value for key, value in row.items() if key != "record_hash"}
    row["record_hash"] = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    ledger.path.write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    os.chmod(ledger.path, 0o600)
    with pytest.raises(LedgerIntegrityError, match="unsafe"):
        ledger.records()
