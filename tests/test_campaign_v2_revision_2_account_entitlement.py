from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from core.decision_ledger import LedgerIntegrityError
from core.orchestration.campaign_v2_revision_2_account_entitlement import (
    ACQUISITION_START,
    FIXED_FALSE,
    PUBLIC_DOCUMENTATION_BUNDLE_SHA256,
    PUBLIC_DOCUMENTATION_EVIDENCE_ID,
    PUBLIC_DOCUMENTATION_RECORD_SHA256,
    PREREGISTRATION_ID,
    PREREGISTRATION_RECORD_SHA256,
    PROPOSAL_SHA256,
    CampaignV2Revision2AccountEntitlementLedger,
    build_revision_2_account_entitlement_evidence,
)


NOW = datetime.now(timezone.utc).replace(microsecond=0)
POINTERS = {
    "account_identity": "/account/id",
    "plan_name": "/account/plan/name",
    "daily_bars_access": "/account/plan/entitlements/daily_bars",
    "dividends_access": "/account/plan/entitlements/dividends",
    "stock_splits_access": "/account/plan/entitlements/stock_splits",
    "historical_lookback_start": "/account/plan/history_start",
    "incremental_cost_usd": "/account/plan/incremental_cost_usd",
}


def _payload(**changes) -> bytes:
    value = {
        "account": {
            "id": "synthetic-account-123",
            "plan": {
                "name": "Synthetic Free",
                "entitlements": {
                    "daily_bars": True,
                    "dividends": True,
                    "stock_splits": True,
                },
                "history_start": "2024-08-16",
                "incremental_cost_usd": "0.00",
            },
        }
    }
    for path, replacement in changes.items():
        target = value
        parts = path.split("__")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = replacement
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _capture(**changes):
    result = {
        "captured_at": NOW - timedelta(minutes=2),
        "source_kind": "AUTHENTICATED_DASHBOARD_EXPORT",
        "source_uri": "https://massive.com/",
        "payload_bytes": _payload(),
        "fact_json_pointers": dict(POINTERS),
        "authenticated_session_attested": True,
        "market_data_response_used_for_account_binding": False,
        "request_id_used_for_account_binding": False,
    }
    result.update(changes)
    return result


def _public_parent(**changes):
    result = {
        "evidence_id": PUBLIC_DOCUMENTATION_EVIDENCE_ID,
        "record_hash": PUBLIC_DOCUMENTATION_RECORD_SHA256,
        "preregistration_id": PREREGISTRATION_ID,
        "preregistration_record_sha256": PREREGISTRATION_RECORD_SHA256,
        "proposal_sha256": PROPOSAL_SHA256,
        "public_documentation_evidence": {
            "evidence_bundle_sha256": PUBLIC_DOCUMENTATION_BUNDLE_SHA256
        },
        "authenticated_account_evidence_collected": False,
        "data_calls_allowed": False,
        "evaluation_allowed": False,
        "orders_submitted": False,
    }
    result.update(changes)
    return result


def _ledger(tmp_path: Path, *, parent=None):
    return CampaignV2Revision2AccountEntitlementLedger(
        tmp_path / "private" / "evidence.jsonl",
        tmp_path / "private" / "blobs",
        repository_root=tmp_path,
        clock=lambda: NOW,
        public_evidence_resolver=lambda: parent or _public_parent(),
    )


def _register(ledger, capture=None):
    return ledger.register(
        capture or _capture(), assessed_at=NOW - timedelta(minutes=1)
    )


def test_derives_exact_revision_2_facts_without_clear_account_id():
    evidence = build_revision_2_account_entitlement_evidence(
        _capture(), assessed_at=NOW - timedelta(minutes=1)
    )

    assert evidence["historical_lookback_start"] <= ACQUISITION_START
    assert evidence["rolling_lookback_buffer_days"] == 46
    assert evidence["capture_window_must_be_revalidated_at_activation"] is True
    assert evidence["provider_origin_cryptographically_verified"] is False
    assert evidence["authentication_basis"] == (
        "LOCAL_OPERATOR_CAPTURE_ATTESTATION_ONLY_PROVIDER_ORIGIN_UNVERIFIED"
    )
    assert evidence["daily_bars_access_confirmed"] is True
    assert evidence["dividends_access_confirmed"] is True
    assert evidence["stock_splits_access_confirmed"] is True
    assert evidence["zero_incremental_cost_proven"] is True
    assert evidence["incremental_cost_usd"] == "0.00"
    assert evidence["account_identity_sha256"] == hashlib.sha256(
        b"synthetic-account-123"
    ).hexdigest()
    assert "synthetic-account-123" not in json.dumps(evidence)


def test_registers_owner_only_byte_bound_record_with_no_authority(tmp_path):
    ledger = _ledger(tmp_path)
    record = _register(ledger)

    assert record["preregistration_id"] == PREREGISTRATION_ID
    assert record["public_documentation_evidence_id"] == (
        PUBLIC_DOCUMENTATION_EVIDENCE_ID
    )
    assert all(record[field] is False for field in FIXED_FALSE)
    assert ledger.require(record["evidence_id"]) == record
    assert ledger.path.stat().st_mode & 0o777 == 0o600
    assert ledger.path.parent.stat().st_mode & 0o777 == 0o700
    blob = ledger.blob_root / record["blob_reference"]["blob_relative_path"]
    assert blob.stat().st_mode & 0o777 == 0o400
    assert blob.read_bytes() == _capture()["payload_bytes"]


@pytest.mark.parametrize(
    ("path", "replacement", "message"),
    [
        ("account__plan__entitlements__daily_bars", False, "daily_bars"),
        ("account__plan__entitlements__dividends", False, "dividends"),
        ("account__plan__entitlements__stock_splits", False, "stock_splits"),
        ("account__plan__history_start", "2024-09-02", "rolling buffer"),
        ("account__plan__history_start", "2024-10-01", "rolling buffer"),
        ("account__plan__incremental_cost_usd", "0.01", "zero incremental"),
    ],
)
def test_rejects_unproven_scope_lookback_or_zero_cost(path, replacement, message):
    with pytest.raises(ValueError, match=message):
        build_revision_2_account_entitlement_evidence(
            _capture(payload_bytes=_payload(**{path: replacement})),
            assessed_at=NOW - timedelta(minutes=1),
        )


@pytest.mark.parametrize(
    "change",
    [
        {"source_kind": "PUBLIC_DOCUMENTATION"},
        {"authenticated_session_attested": False},
        {"market_data_response_used_for_account_binding": True},
        {"request_id_used_for_account_binding": True},
        {"source_uri": "https://example.com/account"},
        {"source_uri": "https://massive.com/dashboard?apiKey=secret"},
    ],
)
def test_rejects_non_account_binding_sources(change):
    with pytest.raises(ValueError):
        build_revision_2_account_entitlement_evidence(
            _capture(**change), assessed_at=NOW - timedelta(minutes=1)
        )


def test_accepts_exact_30_day_buffer_boundary():
    evidence = build_revision_2_account_entitlement_evidence(
        _capture(payload_bytes=_payload(account__plan__history_start="2024-09-01")),
        assessed_at=NOW - timedelta(minutes=1),
    )
    assert evidence["rolling_lookback_buffer_days"] == 30


def test_accepts_account_endpoint_origin_and_rejects_ports_or_paths():
    evidence = build_revision_2_account_entitlement_evidence(
        _capture(
            source_kind="OFFICIAL_AUTHENTICATED_ACCOUNT_ENDPOINT",
            source_uri="https://api.massive.com/",
        ),
        assessed_at=NOW - timedelta(minutes=1),
    )
    assert evidence["source_kind"] == "OFFICIAL_AUTHENTICATED_ACCOUNT_ENDPOINT"

    for uri in (
        "https://massive.com:8443/",
        "https://massive.com/dashboard/account-id-123",
        "https://api.massive.com/account/account-id-123",
    ):
        with pytest.raises(ValueError, match="official Massive origin"):
            build_revision_2_account_entitlement_evidence(
                _capture(source_uri=uri), assessed_at=NOW - timedelta(minutes=1)
            )


@pytest.mark.parametrize(
    "credential_payload",
    [
        _payload(account__api_key="long-secret-key-material-123456"),
        _payload(account__x_massive_api_key="long-secret-key-material-123456"),
        _payload(account__authorization="Bearer secret-value"),
        _payload(account__refresh_token="secret-value"),
    ],
)
def test_rejects_credential_material_before_any_write(tmp_path, credential_payload):
    ledger = _ledger(tmp_path)
    with pytest.raises(ValueError, match="credential material"):
        _register(ledger, _capture(payload_bytes=credential_payload))
    assert not ledger.path.exists()


def test_rejects_missing_or_redirected_json_pointer():
    missing = dict(POINTERS)
    missing.pop("dividends_access")
    with pytest.raises(ValueError, match="exact Revision-2 facts"):
        build_revision_2_account_entitlement_evidence(
            _capture(fact_json_pointers=missing),
            assessed_at=NOW - timedelta(minutes=1),
        )

    redirected = dict(POINTERS)
    redirected["dividends_access"] = "/account/plan/missing"
    with pytest.raises(ValueError, match="does not resolve"):
        build_revision_2_account_entitlement_evidence(
            _capture(fact_json_pointers=redirected),
            assessed_at=NOW - timedelta(minutes=1),
        )

    duplicated = dict(POINTERS)
    duplicated["dividends_access"] = duplicated["daily_bars_access"]
    with pytest.raises(ValueError, match="distinct JSON pointer"):
        build_revision_2_account_entitlement_evidence(
            _capture(fact_json_pointers=duplicated),
            assessed_at=NOW - timedelta(minutes=1),
        )


def test_rejects_non_json_or_non_object_payload():
    for payload in (b"not-json", b"[]"):
        with pytest.raises(ValueError, match="JSON"):
            build_revision_2_account_entitlement_evidence(
                _capture(payload_bytes=payload),
                assessed_at=NOW - timedelta(minutes=1),
            )


@pytest.mark.parametrize(
    "parent_change",
    [
        {"record_hash": "f" * 64},
        {"proposal_sha256": "f" * 64},
        {"authenticated_account_evidence_collected": True},
        {"data_calls_allowed": True},
        {"evaluation_allowed": True},
        {"orders_submitted": True},
    ],
)
def test_rejects_substituted_or_authorizing_public_parent(tmp_path, parent_change):
    with pytest.raises(LedgerIntegrityError, match="parent"):
        _register(_ledger(tmp_path, parent=_public_parent(**parent_change)))


def test_identical_registration_is_idempotent_and_different_bytes_cannot_replace(tmp_path):
    ledger = _ledger(tmp_path)
    first = _register(ledger)
    assert _register(ledger) == first
    changed = _capture(payload_bytes=_payload(account__plan__name="Other Plan"))
    with pytest.raises(LedgerIntegrityError, match="Different"):
        _register(ledger, changed)


def test_empty_preexisting_ledger_fails_with_integrity_error(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.path.parent.mkdir(parents=True, mode=0o700)
    ledger.path.touch(mode=0o600)
    with pytest.raises(LedgerIntegrityError, match="empty or unsafe"):
        _register(ledger)


def test_tampered_ledger_or_blob_fails_closed(tmp_path):
    ledger = _ledger(tmp_path)
    record = _register(ledger)
    row = json.loads(ledger.path.read_text())
    row["data_calls_allowed"] = True
    ledger.path.chmod(0o600)
    ledger.path.write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    ledger.path.chmod(0o600)
    with pytest.raises(LedgerIntegrityError, match="violated"):
        ledger.records()

    fresh = _ledger(tmp_path / "fresh")
    fresh_record = _register(fresh)
    blob = fresh.blob_root / fresh_record["blob_reference"]["blob_relative_path"]
    blob.chmod(0o600)
    blob.write_bytes(b"tampered")
    blob.chmod(0o400)
    with pytest.raises(LedgerIntegrityError, match="blob bytes changed"):
        fresh.records()


def test_unsafe_permissions_and_symlink_fail_closed(tmp_path):
    ledger = _ledger(tmp_path)
    record = _register(ledger)
    os.chmod(ledger.path, 0o644)
    with pytest.raises(LedgerIntegrityError, match="ledger is unsafe"):
        ledger.records()

    fresh = _ledger(tmp_path / "fresh")
    fresh_record = _register(fresh)
    blob = fresh.blob_root / fresh_record["blob_reference"]["blob_relative_path"]
    target = tmp_path / "replacement"
    target.write_bytes(blob.read_bytes())
    blob.unlink()
    blob.symlink_to(target)
    with pytest.raises(LedgerIntegrityError, match="missing or unsafe"):
        fresh.records()


def test_module_has_no_network_provider_replay_broker_or_order_imports():
    path = Path(
        "core/orchestration/campaign_v2_revision_2_account_entitlement.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
    forbidden = {
        "requests",
        "httpx",
        "urllib.request",
        "alpaca",
        "vectorbt",
        "core.backtest",
        "core.guardrailed_backtest",
        "core.research.massive_historical_quarantine",
    }
    assert imports.isdisjoint(forbidden)
    source = path.read_text(encoding="utf-8")
    assert "submit_order" not in source
    assert "apiKey=" not in source
