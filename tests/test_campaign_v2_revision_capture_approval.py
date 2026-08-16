from __future__ import annotations

import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat

import pytest

import core.orchestration.campaign_v2_revision_capture_approval as module
from core.decision_ledger import LedgerIntegrityError
from core.orchestration.campaign_v2_revision_capture_approval import (
    CampaignV2RevisionCaptureApprovalLedger,
    FIXED_FALSE,
)
from core.orchestration.massive_historical_quarantine import (
    _provider_data_calls_authorized,
)
from core.research.campaign_v2_revision_capture_activation_proposal import (
    PARENT_PREREGISTRATION_ID,
    PARENT_PREREGISTRATION_RECORD_SHA256,
    required_approval_text,
)


ROOT = Path(__file__).resolve().parents[1]
APPROVAL_AT = datetime.now(timezone.utc)
GIT_REVISION = "a" * 40


def _parent() -> dict[str, str]:
    return {
        "preregistration_id": PARENT_PREREGISTRATION_ID,
        "record_hash": PARENT_PREREGISTRATION_RECORD_SHA256,
        "policy_version": "massive-completed-history-preregistration-v2-revision-1",
    }


def _ledger(tmp_path: Path, **overrides):
    arguments = {
        "repository_root": ROOT,
        "clock": lambda: APPROVAL_AT,
        "git_revision_resolver": lambda _: GIT_REVISION,
        "worktree_clean_resolver": lambda _: True,
        "parent_resolver": _parent,
    }
    arguments.update(overrides)
    return CampaignV2RevisionCaptureApprovalLedger(
        tmp_path / "capture_approval.jsonl",
        **arguments,
    )


def _record(tmp_path: Path) -> dict:
    return _ledger(tmp_path).record_approval(
        approved_by="SAM_AND_PAT_USER_APPROVAL",
        approval_text=required_approval_text(),
    )


def test_exact_approval_is_recorded_once_with_every_authority_false(tmp_path):
    ledger = _ledger(tmp_path)
    record = ledger.record_approval(
        approved_by="SAM_AND_PAT_USER_APPROVAL",
        approval_text=required_approval_text(),
    )
    repeated = ledger.record_approval(
        approved_by="SAM_AND_PAT_USER_APPROVAL",
        approval_text=required_approval_text(),
    )

    assert repeated == record
    assert record["status"] == "APPROVED_ENTITLEMENT_AND_ACTIVATION_PENDING"
    assert record["approval_text"] == required_approval_text()
    assert record["proposal_sha256"] == (
        "ac80cd34ccfd9a620daa7d81812261f97a144402e56e7b3eb20825565266d02a"
    )
    assert record["parent_preregistration_id"] == PARENT_PREREGISTRATION_ID
    assert record["parent_preregistration_record_sha256"] == (
        PARENT_PREREGISTRATION_RECORD_SHA256
    )
    assert record["authorized_request_count"] == 27
    assert {item[1] for item in record["authorized_request_slices"]} == {
        "TRAIN",
        "VALIDATION",
    }
    assert record["sealed_split"]["role"] == "UNTOUCHED_TEST"
    assert record["sealed_split_semantic_role"] == "SEALED_RETROSPECTIVE_TEST"
    assert record["entitlement_evidence_status"] == "NOT_COLLECTED"
    assert all(record[name] is False for name in FIXED_FALSE)
    assert len(ledger.records()) == 1
    assert stat.S_IMODE(ledger.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(ledger.path.parent.stat().st_mode) == 0o700


def test_approval_cannot_be_misrepresented_as_fetch_authority(tmp_path):
    record = _record(tmp_path)

    assert _provider_data_calls_authorized(record) is False
    assert record["capture_activation_issued"] is False
    assert record["entitlement_revalidated"] is False
    assert record["data_calls_allowed"] is False
    assert record["provider_bytes_accessed"] is False
    assert record["untouched_test_opened"] is False


def test_wrong_text_dirty_git_or_wrong_parent_fails_before_append(tmp_path):
    ledger = _ledger(tmp_path)
    with pytest.raises(ValueError, match="does not match"):
        ledger.record_approval(
            approved_by="SAM_AND_PAT_USER_APPROVAL",
            approval_text="I authorize a different proposal",
        )
    assert not ledger.path.exists()

    dirty = _ledger(tmp_path / "dirty", worktree_clean_resolver=lambda _: False)
    with pytest.raises(ValueError, match="worktree must be clean"):
        dirty.record_approval(
            approved_by="SAM_AND_PAT_USER_APPROVAL",
            approval_text=required_approval_text(),
        )
    assert not dirty.path.exists()

    wrong_parent = _ledger(
        tmp_path / "parent",
        parent_resolver=lambda: {
            **_parent(),
            "record_hash": "b" * 64,
        },
    )
    with pytest.raises(LedgerIntegrityError, match="parent preregistration differs"):
        wrong_parent.record_approval(
            approved_by="SAM_AND_PAT_USER_APPROVAL",
            approval_text=required_approval_text(),
        )
    assert not wrong_parent.path.exists()


def test_existing_approval_cannot_change_authority(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.record_approval(
        approved_by="SAM_AND_PAT_USER_APPROVAL",
        approval_text=required_approval_text(),
    )
    with pytest.raises(LedgerIntegrityError, match="authority differs"):
        ledger.record_approval(
            approved_by="DIFFERENT_AUTHORITY",
            approval_text=required_approval_text(),
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("payload", "data_calls_allowed"), True),
        (("payload", "entitlement_evidence_status"), "COLLECTED"),
        (("payload", "authorized_request_count"), 36),
        (("payload", "sealed_split", "role"), "TRAIN"),
        (("previous_hash",), "f" * 64),
    ],
)
def test_tampering_is_detected(tmp_path, path, value):
    ledger = _ledger(tmp_path)
    _record(tmp_path)
    row = json.loads(ledger.path.read_text())
    target = row
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    ledger.path.write_text(json.dumps(row, sort_keys=True) + "\n")
    os.chmod(ledger.path, 0o600)

    with pytest.raises(LedgerIntegrityError, match="inert boundary"):
        ledger.records()


def test_module_has_no_network_credential_quarantine_replay_or_broker_capability():
    tree = ast.parse(Path(module.__file__).read_text())
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imports.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    forbidden = {
        "requests",
        "httpx",
        "urllib.request",
        "core.orchestration.massive_historical_adapter",
        "core.orchestration.massive_historical_quarantine",
        "core.research.vectorbt_pilot",
        "core.guardrailed_backtest",
    }
    assert imports.isdisjoint(forbidden)
    assert not any("broker" in value or "alpaca" in value for value in imports)
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls & {"open", "eval", "exec", "__import__"}
