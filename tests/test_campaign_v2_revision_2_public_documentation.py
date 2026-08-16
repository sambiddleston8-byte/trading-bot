from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest

from core.decision_ledger import LedgerIntegrityError
from core.orchestration.campaign_v2_revision_2_public_documentation import (
    FIXED_FALSE,
    PREREGISTRATION_ID,
    PREREGISTRATION_RECORD_SHA256,
    PROPOSAL_SHA256,
    CampaignV2Revision2PublicDocumentationLedger,
)
from core.orchestration.massive_entitlement_evidence import (
    PUBLIC_DOCUMENT_CONTRACTS,
)


NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _parent(**changes):
    parent = {
        "preregistration_id": PREREGISTRATION_ID,
        "record_hash": PREREGISTRATION_RECORD_SHA256,
        "proposal_sha256": PROPOSAL_SHA256,
        "public_documentation_evidence_collected": False,
        "data_calls_allowed": False,
        "evaluation_allowed": False,
        "orders_submitted": False,
    }
    parent.update(changes)
    return parent


def _documents():
    result = []
    for document_id, contract in PUBLIC_DOCUMENT_CONTRACTS.items():
        fragments = [
            fragment
            for claim in contract["fact_claims"].values()
            for fragment in claim["required_fragments"]
        ]
        result.append(
            {
                "document_id": document_id,
                "uri": contract["uri"],
                "retrieved_at": NOW - timedelta(minutes=2),
                "payload_bytes": (
                    "<html><body>" + " ".join(fragments) + "</body></html>"
                ).encode("utf-8"),
            }
        )
    return result


def _ledger(tmp_path: Path, *, parent=None):
    return CampaignV2Revision2PublicDocumentationLedger(
        tmp_path / "private" / "evidence.jsonl",
        tmp_path / "private" / "blobs",
        repository_root=tmp_path,
        clock=lambda: NOW,
        parent_resolver=lambda: parent or _parent(),
    )


def _register(ledger):
    return ledger.register(_documents(), assessed_at=NOW - timedelta(minutes=1))


def test_registers_byte_bound_campaign_evidence_with_no_authority(tmp_path):
    ledger = _ledger(tmp_path)
    record = _register(ledger)

    assert record["preregistration_id"] == PREREGISTRATION_ID
    assert record["preregistration_record_sha256"] == PREREGISTRATION_RECORD_SHA256
    assert record["proposal_sha256"] == PROPOSAL_SHA256
    assert len(record["blob_references"]) == len(PUBLIC_DOCUMENT_CONTRACTS)
    assert all(record[field] is False for field in FIXED_FALSE)
    evidence = record["public_documentation_evidence"]
    assert evidence["public_documentation_proves_account_identity"] is False
    assert evidence["public_documentation_proves_account_plan"] is False
    assert evidence["public_documentation_proves_current_account_entitlement"] is False
    assert ledger.require(record["evidence_id"]) == record


def test_ledger_and_exact_blobs_are_owner_only_and_content_addressed(tmp_path):
    ledger = _ledger(tmp_path)
    record = _register(ledger)

    assert ledger.path.stat().st_mode & 0o777 == 0o600
    assert ledger.path.parent.stat().st_mode & 0o777 == 0o700
    assert ledger.blob_root.stat().st_mode & 0o777 == 0o700
    for reference in record["blob_references"]:
        path = ledger.blob_root / reference["blob_relative_path"]
        assert path.stat().st_mode & 0o777 == 0o400
        assert path.read_bytes() == next(
            item["payload_bytes"]
            for item in _documents()
            if item["document_id"] == reference["document_id"]
        )


def test_identical_registration_is_idempotent(tmp_path):
    ledger = _ledger(tmp_path)
    first = _register(ledger)
    second = _register(ledger)
    assert second == first
    assert len(ledger.records()) == 1


def test_different_bundle_cannot_replace_registered_evidence(tmp_path):
    ledger = _ledger(tmp_path)
    _register(ledger)
    changed = _documents()
    changed[0]["payload_bytes"] += b" changed public footer"
    with pytest.raises(LedgerIntegrityError, match="different"):
        ledger.register(changed, assessed_at=NOW - timedelta(minutes=1))


@pytest.mark.parametrize(
    "parent_change",
    [
        {"record_hash": "f" * 64},
        {"proposal_sha256": "f" * 64},
        {"public_documentation_evidence_collected": True},
        {"data_calls_allowed": True},
        {"evaluation_allowed": True},
        {"orders_submitted": True},
    ],
)
def test_rejects_substituted_or_authorizing_parent(tmp_path, parent_change):
    with pytest.raises(LedgerIntegrityError, match="parent"):
        _register(_ledger(tmp_path, parent=_parent(**parent_change)))


def test_rejects_assessment_after_recording(tmp_path):
    ledger = _ledger(tmp_path)
    with pytest.raises(ValueError, match="assessment"):
        ledger.register(_documents(), assessed_at=NOW + timedelta(seconds=1))


def test_rejects_credential_material_before_persisting_bytes(tmp_path):
    ledger = _ledger(tmp_path)
    documents = _documents()
    documents[0]["payload_bytes"] += b" apiKey=accountSecret1234567890abcdef"
    with pytest.raises(ValueError, match="credential material"):
        ledger.register(documents, assessed_at=NOW - timedelta(minutes=1))
    assert not ledger.path.exists()


def test_tampered_ledger_fails_closed(tmp_path):
    ledger = _ledger(tmp_path)
    _register(ledger)
    row = json.loads(ledger.path.read_text())
    row["data_calls_allowed"] = True
    ledger.path.chmod(0o600)
    ledger.path.write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    ledger.path.chmod(0o600)
    with pytest.raises(LedgerIntegrityError, match="violates"):
        ledger.records()


def test_tampered_blob_fails_closed(tmp_path):
    ledger = _ledger(tmp_path)
    record = _register(ledger)
    reference = record["blob_references"][0]
    path = ledger.blob_root / reference["blob_relative_path"]
    path.chmod(0o600)
    path.write_bytes(b"tampered")
    path.chmod(0o400)
    with pytest.raises(LedgerIntegrityError, match="blob bytes changed"):
        ledger.records()


def test_symlinked_blob_fails_closed(tmp_path):
    ledger = _ledger(tmp_path)
    record = _register(ledger)
    reference = record["blob_references"][0]
    path = ledger.blob_root / reference["blob_relative_path"]
    target = tmp_path / "replacement"
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(LedgerIntegrityError, match="missing or unsafe"):
        ledger.records()


def test_requires_complete_exact_contract_and_rejects_duplicate_identity(tmp_path):
    ledger = _ledger(tmp_path)
    with pytest.raises(ValueError, match="all exact"):
        ledger.register(_documents()[:-1], assessed_at=NOW - timedelta(minutes=1))

    duplicate = _documents()
    duplicate[-1] = deepcopy(duplicate[0])
    with pytest.raises(ValueError, match="duplicate or unsupported"):
        ledger.register(duplicate, assessed_at=NOW - timedelta(minutes=1))


def test_module_has_no_network_provider_fetch_broker_or_evaluation_imports():
    path = Path(
        "core/orchestration/campaign_v2_revision_2_public_documentation.py"
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
        "core.research.massive_historical_quarantine",
    }
    assert imports.isdisjoint(forbidden)
    source = path.read_text(encoding="utf-8")
    assert "api_key" not in source.lower().replace("api_key_request_allowed", "")
    assert "submit_order" not in source
    assert "GuardrailedBacktestEngine" not in source


def test_unsafe_ledger_permissions_fail_closed(tmp_path):
    ledger = _ledger(tmp_path)
    _register(ledger)
    os.chmod(ledger.path, 0o644)
    with pytest.raises(LedgerIntegrityError, match="ledger is unsafe"):
        ledger.records()


def test_nonprivate_blob_directory_fails_closed(tmp_path):
    ledger = _ledger(tmp_path)
    _register(ledger)
    os.chmod(ledger.blob_root, 0o755)
    with pytest.raises(LedgerIntegrityError, match="directory is missing or unsafe"):
        ledger.records()
