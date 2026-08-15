from __future__ import annotations

import ast
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import stat

import pytest

import core.orchestration.campaign_v2_revision_preregistration as module
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration.campaign_v2_revision_preregistration import (
    APPROVAL_TEXT,
    CONTROL_LEDGER_RELATIVE_PATH,
    EVENT_TYPE,
    FIXED_FALSE,
    PROPOSAL_SHA256,
    QUARANTINE_ROOT_RELATIVE_PATH,
    SUPERSESSION_RECORD_SHA256,
    CampaignV2RevisionPreregistrationLedger,
    _verified_proposal,
    initialize_campaign_v2_revision_quarantine_root,
)
from core.orchestration.massive_historical_quarantine import (
    MassiveHistoricalQuarantineFetcher,
)
from core.research.conservative_baseline_campaign_v2_revision_proposal import (
    proposal_package,
)


UTC = timezone.utc


def repository(tmp_path: Path) -> Path:
    proposal = (
        tmp_path
        / "core/research/conservative_baseline_campaign_v2_revision_proposal.py"
    )
    strategy = tmp_path / "core/research/conservative_baseline_strategy.py"
    proposal.parent.mkdir(parents=True)
    proposal.write_text("PROPOSAL = 'approved-revision'\n")
    strategy.write_text("class ConservativeBaselineStrategy:\n    pass\n")
    return tmp_path


def ledger(tmp_path: Path, *, clean: bool = True):
    return CampaignV2RevisionPreregistrationLedger(
        tmp_path / CONTROL_LEDGER_RELATIVE_PATH,
        repository_root=repository(tmp_path),
        clock=lambda: datetime.now(UTC),
        git_revision_resolver=lambda _: "a" * 40,
        worktree_clean_resolver=lambda _: clean,
    )


def register(tmp_path: Path):
    target = ledger(tmp_path)
    plan = target.register_approved_package(
        authorized_by="SAM_AND_PAT_USER_APPROVAL",
        approval_text=APPROVAL_TEXT,
    )
    return target, plan


def test_exact_approval_registers_one_inert_completed_history_record(tmp_path):
    target, plan = register(tmp_path)
    rows = target.records()

    assert len(rows) == 1
    assert rows[0]["event_type"] == EVENT_TYPE
    assert rows[0]["previous_hash"] == GENESIS_HASH
    assert plan["proposal_sha256"] == PROPOSAL_SHA256
    assert plan["supersession_record_hash"] == SUPERSESSION_RECORD_SHA256
    assert plan["approval_text"] == APPROVAL_TEXT
    assert plan["splits"] == proposal_package()["splits"]
    assert plan["current_real_world_date_at_registration"] == date.today().isoformat()
    assert plan["all_partition_ends_on_or_before_registration_date"] is True
    assert plan["retrospective_test_semantic_role"] == "SEALED_RETROSPECTIVE_TEST"
    assert plan["preregistration_timing_truth"] == (
        "PREREGISTERED_BEFORE_PROVIDER_BYTE_ACCESS_NOT_BEFORE_MARKET_DATES"
    )
    assert plan["expected_request_count"] == 36
    assert plan["quarantine_only"] is True
    assert plan["entitlement_revalidation_required_before_data_call"] is True
    assert all(plan[name] is False for name in FIXED_FALSE)


def test_registration_is_idempotent_but_approval_and_actor_are_exact(tmp_path):
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
            approval_text="I approve a different package",
        )
    with pytest.raises(LedgerIntegrityError, match="authority differs"):
        target.register_approved_package(
            authorized_by="SUBSTITUTED_ACTOR",
            approval_text=APPROVAL_TEXT,
        )


@pytest.mark.parametrize(
    "current_date",
    [date(2025, 2, 27), date(2025, 4, 29), date(2025, 7, 30)],
)
def test_system_guardrail_rejects_any_partition_ending_after_current_date(
    current_date,
):
    with pytest.raises(LedgerIntegrityError, match="historical date"):
        _verified_proposal(current_real_world_date=current_date)


def test_system_guardrail_accepts_partition_end_equal_to_current_date():
    proposal = _verified_proposal(current_real_world_date=date(2025, 7, 31))
    assert proposal["splits"][-1]["end"] == "2025-07-31"


def test_registration_requires_clean_git_identity(tmp_path):
    target = ledger(tmp_path, clean=False)
    with pytest.raises(ValueError, match="worktree must be clean"):
        target.register_approved_package(
            authorized_by="SAM_AND_PAT_USER_APPROVAL",
            approval_text=APPROVAL_TEXT,
        )


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
        {"data_calls_allowed": True},
        {"evaluation_allowed": True},
        {"current_real_world_date_at_registration": "2025-01-01"},
        {"retrospective_test_semantic_role": "GENUINELY_FUTURE_UNTOUCHED"},
        {"supersession_record_hash": "f" * 64},
        {"entitlement_revalidation_required_before_data_call": False},
        {"strategy_entrypoint": "attacker.module:Strategy"},
        {"injected_capture_grant": True},
        {"sortino_ratio": "2.1"},
    ],
)
def test_fully_rehashed_semantic_tampering_is_rejected(tmp_path, changes):
    target, _ = register(tmp_path)
    _rewrite_rehashed(target.path, changes)
    with pytest.raises(LedgerIntegrityError, match="Campaign v2 revision"):
        target.records()


def test_second_record_and_truncated_line_are_rejected(tmp_path):
    target, _ = register(tmp_path)
    original = target.path.read_text()
    with target.path.open("a") as stream:
        stream.write(original)
    with pytest.raises(LedgerIntegrityError, match="only one preregistration"):
        target.records()
    target.path.write_text(original.rstrip("\n"))
    target.path.chmod(0o600)
    with pytest.raises(LedgerIntegrityError, match="incomplete line"):
        target.records()


class NoCallStore:
    def __init__(self, preregistration_ledger):
        self.preregistration_ledger = preregistration_ledger

    def verify(self):
        raise AssertionError("store must not open before capture activation")


class NoCallClient:
    def fetch_daily_bars(self, **kwargs):
        raise AssertionError("provider must not be called")


def test_fetcher_rejects_revision_before_store_or_provider_access(tmp_path):
    target, plan = register(tmp_path)
    fetcher = MassiveHistoricalQuarantineFetcher(
        store=NoCallStore(target), client=NoCallClient()
    )
    with pytest.raises(ValueError, match="does not authorize provider data calls"):
        fetcher.fetch(plan["preregistration_id"])


def test_initializes_only_empty_private_disjoint_revision_root(tmp_path):
    admitted = tmp_path / "admitted"
    admitted.mkdir()
    target = initialize_campaign_v2_revision_quarantine_root(
        tmp_path, admitted_store_roots=[admitted]
    )
    assert target == (tmp_path / QUARANTINE_ROOT_RELATIVE_PATH).resolve()
    assert list(target.iterdir()) == []
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    overlap_root = tmp_path / "overlap"
    with pytest.raises(LedgerIntegrityError, match="overlaps admitted"):
        initialize_campaign_v2_revision_quarantine_root(
            overlap_root,
            admitted_store_roots=[
                overlap_root / QUARANTINE_ROOT_RELATIVE_PATH / "nested"
            ],
        )
    symlink_root = tmp_path / "symlink"
    actual = tmp_path / "actual-raw"
    actual.mkdir()
    revision_path = symlink_root / QUARANTINE_ROOT_RELATIVE_PATH
    revision_path.parent.mkdir(parents=True)
    revision_path.symlink_to(actual, target_is_directory=True)
    with pytest.raises(LedgerIntegrityError, match="must not be a symlink"):
        initialize_campaign_v2_revision_quarantine_root(
            symlink_root, admitted_store_roots=[admitted]
        )


def test_control_module_has_no_provider_client_replay_broker_or_network_imports():
    tree = ast.parse(Path(module.__file__).read_text())
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imports.update(
        f"{node.module}.{alias.name}" if node.module else alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    )
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
