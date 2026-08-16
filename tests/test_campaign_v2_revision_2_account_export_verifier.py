from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from core.orchestration.campaign_v2_revision_2_account_entitlement import (
    PROPOSAL_SHA256,
)
from core.orchestration.campaign_v2_revision_2_account_export_verifier import (
    ACCOUNT_EXPORT_FILE_NAME,
    CAPTURE_MANIFEST_FILE_NAME,
    INBOX_RELATIVE_PATH,
    verify_account_export_offline,
)


POINTERS = {
    "account_identity": "/account/id",
    "plan_name": "/account/plan/name",
    "daily_bars_access": "/account/plan/entitlements/daily_bars",
    "dividends_access": "/account/plan/entitlements/dividends",
    "stock_splits_access": "/account/plan/entitlements/stock_splits",
    "historical_lookback_start": "/account/plan/history_start",
    "incremental_cost_usd": "/account/plan/incremental_cost_usd",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _export(account_id="synthetic-account-123", **plan_changes) -> bytes:
    plan = {
        "name": "Synthetic Free",
        "entitlements": {
            "daily_bars": True,
            "dividends": True,
            "stock_splits": True,
        },
        "history_start": "2024-08-16",
        "incremental_cost_usd": "0.00",
    }
    plan.update(plan_changes)
    return json.dumps(
        {"account": {"id": account_id, "plan": plan}},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _manifest(export_sha256=None, **changes) -> bytes:
    value = {
        "captured_at": (_now() - timedelta(minutes=2)).isoformat(),
        "source_kind": "AUTHENTICATED_DASHBOARD_EXPORT",
        "source_uri": "https://massive.com/",
        "export_sha256": export_sha256 or hashlib.sha256(_export()).hexdigest(),
        "fact_json_pointers": POINTERS,
        "authenticated_session_attested": True,
        "market_data_response_used_for_account_binding": False,
        "request_id_used_for_account_binding": False,
    }
    value.update(changes)
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _inbox(tmp_path: Path, export=None, manifest=None) -> Path:
    inbox = tmp_path / INBOX_RELATIVE_PATH
    inbox.mkdir(parents=True, mode=0o700)
    os.chmod(inbox, 0o700)
    export_payload = _export() if export is None else export
    manifest_payload = (
        _manifest(hashlib.sha256(export_payload).hexdigest())
        if manifest is None
        else manifest
    )
    for name, payload in (
        (ACCOUNT_EXPORT_FILE_NAME, export_payload),
        (CAPTURE_MANIFEST_FILE_NAME, manifest_payload),
    ):
        path = inbox / name
        path.write_bytes(payload)
        os.chmod(path, 0o600)
    return inbox


def test_verifies_fixed_private_inbox_without_exposing_account_identity(tmp_path):
    _inbox(tmp_path)
    result = verify_account_export_offline(tmp_path, assessed_at=_now())

    assert result["verification_status"] == "PASS"
    assert result["verification_mode"] == "OFFLINE_UNREGISTERED"
    assert result["proposal_sha256"] == PROPOSAL_SHA256
    assert result["proposal_binding_status"] == (
        "MODULE_CONSTANT_ONLY_NOT_LEDGER_VERIFIED"
    )
    assert result["plan_name_sha256"] == hashlib.sha256(
        b"Synthetic Free"
    ).hexdigest()
    assert result["daily_bars_access_confirmed"] is True
    assert result["dividends_access_confirmed"] is True
    assert result["stock_splits_access_confirmed"] is True
    assert result["zero_incremental_cost_proven"] is True
    assert result["provider_origin_cryptographically_verified"] is False
    assert result["account_entitlement_evidence_registered"] is False
    assert result["capture_activation_issued"] is False
    assert result["data_calls_allowed"] is False
    assert result["api_key_request_allowed"] is False
    assert result["broker_connection_allowed"] is False
    assert result["aws_resource_creation_allowed"] is False
    assert "synthetic-account-123" not in json.dumps(result)
    assert "Synthetic Free" not in json.dumps(result)


@pytest.mark.parametrize(
    ("export", "manifest", "message"),
    [
        (b"<html>saved page</html>", None, "strict UTF-8 JSON"),
        (_export(history_start="2024-09-02"), None, "rolling buffer"),
        (_export(incremental_cost_usd="0.01"), None, "zero incremental"),
        (_export(), _manifest("f" * 64), "not bound"),
        (
            json.dumps({"api_key": "secret-material-12345678901234567890"}).encode(),
            None,
            "credential material",
        ),
        (
            None,
            _manifest(source_uri="https://massive.com/dashboard"),
            "official Massive origin",
        ),
        (
            None,
            _manifest(market_data_response_used_for_account_binding=True),
            "market-data responses",
        ),
        (
            None,
            _manifest(request_id_used_for_account_binding=True),
            "request IDs",
        ),
    ],
)
def test_fails_closed_for_unqualified_or_unsafe_inputs(
    tmp_path, export, manifest, message
):
    _inbox(tmp_path, export=export, manifest=manifest)
    with pytest.raises(ValueError, match=message):
        verify_account_export_offline(tmp_path, assessed_at=_now())


def test_rejects_manifest_contract_changes(tmp_path):
    manifest = json.loads(_manifest())
    manifest.pop("export_sha256")
    _inbox(tmp_path, manifest=json.dumps(manifest).encode())
    with pytest.raises(ValueError, match="fixed contract"):
        verify_account_export_offline(tmp_path, assessed_at=_now())


def test_rejects_loose_permissions_and_symlinks(tmp_path):
    inbox = _inbox(tmp_path)
    export = inbox / ACCOUNT_EXPORT_FILE_NAME
    os.chmod(export, 0o644)
    with pytest.raises(ValueError, match="owner-only regular file"):
        verify_account_export_offline(tmp_path, assessed_at=_now())

    export.unlink()
    target = tmp_path / "replacement.json"
    target.write_bytes(_export())
    os.chmod(target, 0o600)
    export.symlink_to(target)
    with pytest.raises((OSError, ValueError)):
        verify_account_export_offline(tmp_path, assessed_at=_now())


def test_rejects_symlinked_or_group_writable_inbox_ancestor(tmp_path):
    inbox = _inbox(tmp_path)
    account_root = inbox.parent
    os.chmod(account_root, 0o770)
    with pytest.raises(ValueError, match="inbox path is unsafe"):
        verify_account_export_offline(tmp_path, assessed_at=_now())

    real_root = tmp_path / "real-account-root"
    account_root.rename(real_root)
    account_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(ValueError, match="inbox path is unsafe"):
        verify_account_export_offline(tmp_path, assessed_at=_now())


def test_rejects_backdated_forward_dated_or_naive_assessment_clock(tmp_path):
    _inbox(tmp_path)
    now = _now()
    for assessment in (
        now - timedelta(minutes=6),
        now + timedelta(minutes=6),
        now.replace(tzinfo=None),
    ):
        with pytest.raises(ValueError, match="verification time|timezone-aware"):
            verify_account_export_offline(tmp_path, assessed_at=assessment)


def test_verifier_and_cli_have_no_network_or_registration_capability():
    paths = [
        Path("core/orchestration/campaign_v2_revision_2_account_export_verifier.py"),
        Path("scripts/verify_campaign_v2_revision_2_account_export.py"),
    ]
    imports = set()
    source = ""
    for path in paths:
        text = path.read_text(encoding="utf-8")
        source += text
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
    forbidden = {"requests", "httpx", "urllib.request", "alpaca", "vectorbt"}
    assert imports.isdisjoint(forbidden)
    assert ".register(" not in source
    assert "submit_order" not in source
    assert "api_key" not in source.lower()
