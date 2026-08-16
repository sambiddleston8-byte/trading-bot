from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
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


def _rewrite_with_valid_hash(path: Path, row: dict) -> None:
    material = {key: value for key, value in row.items() if key != "record_hash"}
    row["record_hash"] = hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    path.write_text(json.dumps(row, sort_keys=True) + "\n")
    os.chmod(path, 0o600)


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


def test_approval_identity_is_anchored_in_authoritative_documents(tmp_path):
    record = _record(tmp_path)
    for relative in (
        "docs/PROJECT_STATUS.md",
        "docs/MASTER_ROADMAP_COMPLETION_AUDIT.md",
    ):
        text = (ROOT / relative).read_text()
        assert record["proposal_sha256"] in text


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
        (("payload", "strategy_source_path"), "core/research/other.py"),
        (("payload", "approved_by"), 7),
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
    _rewrite_with_valid_hash(ledger.path, row)

    with pytest.raises(LedgerIntegrityError, match="capture approval"):
        ledger.records()


def test_unsafe_mode_symlink_and_second_record_fail_closed(tmp_path):
    mode_ledger = _ledger(tmp_path / "mode")
    mode_ledger.record_approval(
        approved_by="SAM_AND_PAT_USER_APPROVAL",
        approval_text=required_approval_text(),
    )
    os.chmod(mode_ledger.path, 0o644)
    with pytest.raises(LedgerIntegrityError, match="unsafe"):
        mode_ledger.records()

    link_ledger = _ledger(tmp_path / "link")
    link_ledger.record_approval(
        approved_by="SAM_AND_PAT_USER_APPROVAL",
        approval_text=required_approval_text(),
    )
    target = link_ledger.path.with_name("real.jsonl")
    link_ledger.path.rename(target)
    link_ledger.path.symlink_to(target)
    with pytest.raises(OSError):
        link_ledger.records()

    second_ledger = _ledger(tmp_path / "second")
    second_ledger.record_approval(
        approved_by="SAM_AND_PAT_USER_APPROVAL",
        approval_text=required_approval_text(),
    )
    line = second_ledger.path.read_bytes()
    second_ledger.path.write_bytes(line + line)
    os.chmod(second_ledger.path, 0o600)
    with pytest.raises(LedgerIntegrityError, match="exactly one"):
        second_ledger.records()


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
