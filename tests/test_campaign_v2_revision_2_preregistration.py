from __future__ import annotations

import ast
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import stat

import pytest

import core.orchestration.campaign_v2_revision_2_preregistration as module
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration.campaign_v2_revision_2_preregistration import (
    APPROVAL_TEXT,
    CONTROL_LEDGER_RELATIVE_PATH,
    EVENT_TYPE,
    FIXED_FALSE,
    PROPOSAL_SHA256,
    QUARANTINE_ROOT_RELATIVE_PATH,
    REVISION_1_CAPTURE_APPROVAL_ID,
    REVISION_1_CAPTURE_APPROVAL_RECORD_SHA256,
    REVISION_1_CAPTURE_PROPOSAL_SHA256,
    REVISION_1_PREREGISTRATION_ID,
    REVISION_1_PREREGISTRATION_RECORD_SHA256,
    CampaignV2Revision2PreregistrationLedger,
    _verified_proposal,
    initialize_campaign_v2_revision_2_quarantine_root,
)
from core.research.conservative_baseline_campaign_v2_revision_2_proposal import (
    proposal_package,
)


UTC = timezone.utc
FIXED_NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW if tz is not None else FIXED_NOW.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def freeze_registration_clock(monkeypatch):
    """Keep approval-window tests deterministic after the real proposal expiry."""

    monkeypatch.setattr(module, "datetime", FrozenDateTime)


def repository(tmp_path: Path) -> Path:
    proposal = (
        tmp_path
        / "core/research/conservative_baseline_campaign_v2_revision_2_proposal.py"
    )
    strategy = tmp_path / "core/research/conservative_baseline_strategy.py"
    proposal.parent.mkdir(parents=True)
    proposal.write_text("PROPOSAL = 'approved-revision-2'\n")
    strategy.write_text("class ConservativeBaselineStrategy:\n    pass\n")
    return tmp_path


def parents(*, capture_activation_issued: bool = False):
    return (
        {
            "preregistration_id": REVISION_1_PREREGISTRATION_ID,
            "record_hash": REVISION_1_PREREGISTRATION_RECORD_SHA256,
            "data_calls_allowed": False,
        },
        {
            "approval_record_id": REVISION_1_CAPTURE_APPROVAL_ID,
            "proposal_sha256": REVISION_1_CAPTURE_PROPOSAL_SHA256,
            "record_hash": REVISION_1_CAPTURE_APPROVAL_RECORD_SHA256,
            "capture_activation_issued": capture_activation_issued,
            "provider_bytes_accessed": False,
        },
    )


def ledger(tmp_path: Path, *, clean: bool = True, parent_values=None):
    return CampaignV2Revision2PreregistrationLedger(
        tmp_path / CONTROL_LEDGER_RELATIVE_PATH,
        repository_root=repository(tmp_path),
        clock=lambda: FIXED_NOW,
        git_revision_resolver=lambda _: "a" * 40,
        worktree_clean_resolver=lambda _: clean,
        parent_resolver=lambda: parent_values or parents(),
    )


def register(tmp_path: Path):
    target = ledger(tmp_path)
    record = target.register_approved_package(
        authorized_by="SAM_AND_PAT_USER_APPROVAL",
        approval_text=APPROVAL_TEXT,
    )
    return target, record


def test_exact_approval_registers_one_inert_stage_0_record(tmp_path):
    target, record = register(tmp_path)
    rows = target.records()

    assert len(rows) == 1
    assert rows[0]["event_type"] == EVENT_TYPE
    assert rows[0]["previous_hash"] == GENESIS_HASH
    assert record["proposal_sha256"] == PROPOSAL_SHA256
    assert record["approval_text"] == APPROVAL_TEXT
    assert record["splits"] == proposal_package()["splits"]
    assert record["all_partition_ends_on_or_before_registration_date"] is True
    assert record["superseded_revision_1_status"] == (
        "TERMINALLY_SUPERSEDED_NEVER_ACTIVATED_NOT_REUSABLE"
    )
    assert record["authoritative_evaluator"] == (
        "core.guardrailed_backtest:GuardrailedBacktestEngine"
    )
    assert record["vectorbt_on_critical_path"] is False
    assert set(record["capture_scope"]["endpoints"]) == {
        "DAILY_BARS",
        "DIVIDENDS",
        "STOCK_SPLITS",
    }
    assert record["next_required_gates"] == [
        "BYTE_BOUND_PUBLIC_DOCUMENTATION_EVIDENCE",
        "AUTHENTICATED_ACCOUNT_BOUND_ZERO_COST_ENTITLEMENT_EVIDENCE",
        "SEPARATE_BOUNDED_TRAIN_VALIDATION_CAPTURE_ACTIVATION",
    ]
    assert all(record[name] is False for name in FIXED_FALSE)


def test_registration_is_idempotent_but_actor_and_approval_are_exact(tmp_path):
    target, first = register(tmp_path)
    second = target.register_approved_package(
        authorized_by="SAM_AND_PAT_USER_APPROVAL",
        approval_text=APPROVAL_TEXT,
    )
    assert first == second
    assert len(target.records()) == 1
    with pytest.raises(ValueError, match="proposal hash"):
        target.register_approved_package(
            authorized_by="SAM_AND_PAT_USER_APPROVAL",
            approval_text="I approve another proposal",
        )
    with pytest.raises(LedgerIntegrityError, match="authority differs"):
        target.register_approved_package(
            authorized_by="SUBSTITUTED_ACTOR",
            approval_text=APPROVAL_TEXT,
        )


def test_requires_clean_git_and_inactive_exact_revision_1_parents(tmp_path):
    with pytest.raises(ValueError, match="worktree must be clean"):
        ledger(tmp_path, clean=False).register_approved_package(
            authorized_by="SAM_AND_PAT_USER_APPROVAL",
            approval_text=APPROVAL_TEXT,
        )
    with pytest.raises(LedgerIntegrityError, match="inactive supersession target"):
        ledger(
            tmp_path / "activated",
            parent_values=parents(capture_activation_issued=True),
        ).register_approved_package(
            authorized_by="SAM_AND_PAT_USER_APPROVAL",
            approval_text=APPROVAL_TEXT,
        )


@pytest.mark.parametrize("offset_days", [-1, 1])
def test_rejects_stale_or_future_registration_clock(tmp_path, offset_days):
    target = CampaignV2Revision2PreregistrationLedger(
        tmp_path / CONTROL_LEDGER_RELATIVE_PATH,
        repository_root=repository(tmp_path),
        clock=lambda: datetime.fromtimestamp(
            FIXED_NOW.timestamp() + offset_days * 86400,
            UTC,
        ),
        git_revision_resolver=lambda _: "a" * 40,
        worktree_clean_resolver=lambda _: True,
        parent_resolver=parents,
    )
    with pytest.raises(ValueError, match="registration clock"):
        target.register_approved_package(
            authorized_by="SAM_AND_PAT_USER_APPROVAL",
            approval_text=APPROVAL_TEXT,
        )


@pytest.mark.parametrize(
    "current_date",
    [date(2025, 7, 30), date(2026, 9, 2)],
)
def test_date_guard_rejects_future_partitions_or_expired_capture_window(current_date):
    with pytest.raises(LedgerIntegrityError, match="historical window"):
        _verified_proposal(current_real_world_date=current_date)


def test_date_guard_accepts_completed_window_before_proposal_expiry():
    proposal = _verified_proposal(current_real_world_date=date(2026, 8, 16))
    assert proposal["splits"][-1]["end"] == "2025-07-31"


def _rewrite_rehashed(path: Path, changes: dict) -> None:
    row = json.loads(path.read_text())
    row["payload"].update(changes)
    material = {key: value for key, value in row.items() if key != "record_hash"}
    row["record_hash"] = hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    path.write_text(
        json.dumps(
            row,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )
    path.chmod(0o600)


@pytest.mark.parametrize(
    "changes",
    [
        {"api_key_request_allowed": True},
        {"data_calls_allowed": True},
        {"evaluation_allowed": True},
        {"vectorbt_on_critical_path": True},
        {"authoritative_evaluator": "other.Engine"},
        {"superseded_revision_1_status": "ACTIVE"},
        {"authorized_by": " PADDED_ACTOR "},
        {"superseded_revision_1_capture_approval_record_sha256": "f" * 64},
        {"injected_performance_metric": "2.1"},
    ],
)
def test_fully_rehashed_semantic_tampering_is_rejected(tmp_path, changes):
    target, _ = register(tmp_path)
    _rewrite_rehashed(target.path, changes)
    with pytest.raises(LedgerIntegrityError, match="revision-2"):
        target.records()


def test_truncation_and_second_record_are_rejected(tmp_path):
    target, _ = register(tmp_path)
    original = target.path.read_text()
    target.path.write_text(original.rstrip("\n"))
    target.path.chmod(0o600)
    with pytest.raises(LedgerIntegrityError, match="incomplete line"):
        target.records()
    target.path.write_text(original + original)
    target.path.chmod(0o600)
    with pytest.raises(LedgerIntegrityError, match="only one record"):
        target.records()


def test_rejects_group_readable_or_symlinked_ledger(tmp_path):
    target, _ = register(tmp_path)
    target.path.chmod(0o640)
    with pytest.raises(LedgerIntegrityError, match="unsafe"):
        target.records()

    symlink_area = tmp_path / "symlink"
    actual = symlink_area / "actual.jsonl"
    actual.parent.mkdir()
    actual.write_text("{}\n")
    actual.chmod(0o600)
    linked = symlink_area / "linked.jsonl"
    linked.symlink_to(actual)
    linked_ledger = CampaignV2Revision2PreregistrationLedger(
        linked,
        repository_root=tmp_path,
        parent_resolver=parents,
    )
    with pytest.raises(OSError):
        linked_ledger.records()


def test_existing_record_remains_verifiable_after_proposal_expiry(
    tmp_path,
    monkeypatch,
):
    target, record = register(tmp_path)

    class LaterDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 1)

    monkeypatch.setattr(module, "date", LaterDate)
    assert target.require(record["preregistration_id"])["record_hash"] == record[
        "record_hash"
    ]


def test_initializes_only_empty_private_disjoint_revision_2_root(tmp_path):
    admitted = tmp_path / "admitted"
    admitted.mkdir()
    target = initialize_campaign_v2_revision_2_quarantine_root(
        tmp_path,
        admitted_store_roots=[admitted],
    )
    assert target == (tmp_path / QUARANTINE_ROOT_RELATIVE_PATH).resolve()
    assert list(target.iterdir()) == []
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    with pytest.raises(LedgerIntegrityError, match="overlaps admitted"):
        initialize_campaign_v2_revision_2_quarantine_root(
            tmp_path / "overlap",
            admitted_store_roots=[
                tmp_path / "overlap" / QUARANTINE_ROOT_RELATIVE_PATH / "nested"
            ],
        )
    relative_overlap = tmp_path / "relative-overlap"
    with pytest.raises(LedgerIntegrityError, match="overlaps admitted"):
        initialize_campaign_v2_revision_2_quarantine_root(
            relative_overlap,
            admitted_store_roots=[QUARANTINE_ROOT_RELATIVE_PATH / "nested"],
        )
    prior_overlap = tmp_path / "prior-overlap"
    monkeypatch_target = QUARANTINE_ROOT_RELATIVE_PATH / "nested-prior"
    original = module.REVISION_1_ROOT_RELATIVE_PATH
    try:
        module.REVISION_1_ROOT_RELATIVE_PATH = monkeypatch_target
        with pytest.raises(LedgerIntegrityError, match="boundaries overlap"):
            initialize_campaign_v2_revision_2_quarantine_root(
                prior_overlap,
                admitted_store_roots=[admitted],
            )
    finally:
        module.REVISION_1_ROOT_RELATIVE_PATH = original


def test_quarantine_root_rejects_symlinked_ancestor_escape(tmp_path):
    repository_root = tmp_path / "repository"
    outside = tmp_path / "outside"
    repository_root.mkdir()
    outside.mkdir()
    (repository_root / "data").symlink_to(outside, target_is_directory=True)
    with pytest.raises(LedgerIntegrityError, match="escapes the repository"):
        initialize_campaign_v2_revision_2_quarantine_root(
            repository_root,
            admitted_store_roots=[tmp_path / "admitted"],
        )


def test_control_module_has_no_provider_replay_broker_or_network_imports():
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
        "urllib.request",
        "httpx",
        "core.orchestration.massive_historical_quarantine",
        "core.research.vectorbt_pilot",
        "core.guardrailed_backtest",
    }
    assert imports.isdisjoint(forbidden)
    assert not any("broker" in name or "alpaca" in name for name in imports)
