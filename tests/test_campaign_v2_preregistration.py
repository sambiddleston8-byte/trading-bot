from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import stat

import pytest

import core.orchestration.campaign_v2_preregistration as registration_module
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration.campaign_v2_preregistration import (
    APPROVAL_TEXT,
    CONTROL_LEDGER_RELATIVE_PATH,
    EVENT_SEQUENCE,
    PROPOSAL_SHA256,
    QUARANTINE_ROOT_RELATIVE_PATH,
    V1_CLOSURE_RECORD_SHA256,
    V1_ENTITLEMENT_METADATA_SHA256,
    V1_PREREGISTRATION_RECORD_SHA256,
    CampaignV2ControlLedger,
    CONTROL_EVENT_SEQUENCE,
    SUPERSESSION_EVENT,
    _require_before_untouched_window,
    initialize_campaign_v2_quarantine_root,
)
from core.orchestration.massive_historical_quarantine import (
    MassiveHistoricalQuarantineFetcher,
    MassiveHistoricalQuarantineStore,
)
from core.research.conservative_baseline_campaign_v2_proposal import proposal_package


UTC = timezone.utc
PARENT_EXEMPTION_ID = "RIXA-E657C58EC99C067EAB6879FB035C0A0D"
PARENT_EXEMPTION_HASH = (
    "8eaf1d499213f0abc324b3265b1c6a184b4a083621a01ed0b2808ef89b624513"
)
PARENT_PREREGISTRATION_ID = "HQP-PARENT"
PARENT_PREREGISTRATION_HASH = V1_PREREGISTRATION_RECORD_SHA256
PARENT_ENTITLEMENT_HASH = V1_ENTITLEMENT_METADATA_SHA256


class VerifiedV1ExemptionLedger:
    def __init__(self, *, closed: bool = True, closure_overrides=None) -> None:
        exemption = {
            "event_type": "RESEARCH_EXEMPTION_REGISTERED",
            "record_hash": PARENT_EXEMPTION_HASH,
            "payload": {
                "exemption_id": PARENT_EXEMPTION_ID,
                "preregistration_id": PARENT_PREREGISTRATION_ID,
                "preregistration_record_hash": PARENT_PREREGISTRATION_HASH,
            },
        }
        events = [
            exemption,
            {"event_type": "TRAIN_VALIDATION_RESEARCH_ADMITTED", "record_hash": "3" * 64, "payload": {}},
            {"event_type": "UNTOUCHED_TEST_CONSUMPTION_STARTED", "record_hash": "4" * 64, "payload": {}},
            {"event_type": "UNTOUCHED_TEST_RESULT_RECORDED", "record_hash": "5" * 64, "payload": {}},
        ]
        if closed:
            events.append(
                {
                    "event_type": "CAMPAIGN_V1_CLOSED_PROTOCOL_NONCONFORMANT",
                    "record_hash": V1_CLOSURE_RECORD_SHA256,
                    "payload": {
                        "campaign_status": "CLOSED_PROTOCOL_NONCONFORMANT",
                        "campaign_v1_archived": True,
                        "campaign_v1_evaluation_allowed": False,
                        "campaign_v1_untouched_reusable": False,
                        "research_exemption_active_for_original_scope": True,
                        "research_exemption_scope_extension_granted": False,
                        "exemption_record_hash": PARENT_EXEMPTION_HASH,
                        "closure_reason_codes": [
                            "PREREGISTERED_PURGE_AND_EMBARGO_OMITTED",
                            "SPY_DIAGNOSTIC_TIMING_MISALIGNED_AND_NOT_REGISTERED_COMPLETE_RETURN",
                        ],
                        **(closure_overrides or {}),
                    },
                }
            )
        self._events = events

    def records(self):
        return list(self._events)


class VerifiedV1PreregistrationLedger:
    def require(self, preregistration_id: str):
        assert preregistration_id == PARENT_PREREGISTRATION_ID
        return {
            "preregistration_id": PARENT_PREREGISTRATION_ID,
            "record_hash": PARENT_PREREGISTRATION_HASH,
            "entitlement_metadata_sha256": PARENT_ENTITLEMENT_HASH,
            "target_basket": ["AAPL", "MSFT", "SPY"],
            "entitlement_metadata": {
                "plan_name": "STOCKS_BASIC_FREE",
                "terms_uri": "https://massive.com/stocks",
                "terms_retrieved_at": "2026-08-15T18:54:40.000000+00:00",
                "terms_payload_sha256": (
                    "167566b370022f7f7754588c015a95b884558b3b65dc563fef727131acae2eb6"
                ),
                "asserted_request_limit_per_minute": 5,
                "asserted_incremental_cost_usd": "0.00",
                "public_terms_assertions_authenticated": False,
                "account_entitlement_authenticated": False,
                "historical_replay_use_confirmed": False,
                "redistribution_allowed": False,
                "terms_bytes_authenticated": False,
            },
        }


def repository(tmp_path: Path) -> Path:
    proposal_source = (
        tmp_path / "core/research/conservative_baseline_campaign_v2_proposal.py"
    )
    revision_source = (
        tmp_path
        / "core/research/conservative_baseline_campaign_v2_revision_proposal.py"
    )
    strategy_source = tmp_path / "core/research/conservative_baseline_strategy.py"
    proposal_source.parent.mkdir(parents=True)
    proposal_source.write_text("PROPOSAL = 'approved'\n")
    revision_source.write_text("PROPOSAL = 'pending-revision'\n")
    strategy_source.write_text("class ConservativeBaselineStrategy:\n    pass\n")
    return tmp_path


def ledger(tmp_path: Path) -> CampaignV2ControlLedger:
    return CampaignV2ControlLedger(
        tmp_path / CONTROL_LEDGER_RELATIVE_PATH,
        repository_root=repository(tmp_path),
        git_revision_resolver=lambda _: "a" * 40,
        worktree_clean_resolver=lambda _: True,
    )


def register(tmp_path: Path):
    target = ledger(tmp_path)
    plan = target.register_approved_package(
        v1_exemption_ledger=VerifiedV1ExemptionLedger(),
        v1_preregistration_ledger=VerifiedV1PreregistrationLedger(),
        authorized_by="SAM_AND_PAT_USER_APPROVAL",
        approval_text=APPROVAL_TEXT,
    )
    return target, plan


def test_registers_exact_three_event_control_chain_without_data_authority(tmp_path):
    target, plan = register(tmp_path)
    records = target.records()

    assert [row["event_type"] for row in records] == list(EVENT_SEQUENCE)
    assert records[0]["payload"]["proposal_sha256"] == PROPOSAL_SHA256
    assert records[1]["payload"]["approval_record_hash"] == records[0]["record_hash"]
    assert records[1]["payload"]["v1_closure_record_hash"] == V1_CLOSURE_RECORD_SHA256
    assert records[1]["payload"]["parent_exemption_record_hash"] == PARENT_EXEMPTION_HASH
    assert records[1]["payload"]["applied_capture_slices"] == []
    assert records[1]["payload"]["future_assumptions_asserted"] is False
    assert plan["approval_record_hash"] == records[0]["record_hash"]
    assert plan["exemption_extension_record_hash"] == records[1]["record_hash"]
    assert plan["target_basket"] == ["AAPL", "MSFT", "SPY"]
    assert plan["splits"] == proposal_package()["splits"]
    assert plan["expected_request_count"] == 36
    assert plan["purge_observations"] == 1
    assert plan["embargo_observations"] == 1
    assert plan["maximum_untouched_test_evaluations"] == 1
    assert plan["untouched_test_start"] == "2026-09-01"
    assert plan["preregistered_before_untouched_window"] is True
    assert plan["capture_chain_binding_required"] is True
    assert plan["data_calls_allowed"] is False
    assert plan["evaluation_allowed"] is False
    assert plan["untouched_test_opened"] is False
    assert plan["broker_connection_allowed"] is False


def test_registration_is_idempotent_but_authority_and_hash_are_not_substitutable(tmp_path):
    target, first = register(tmp_path)
    second = target.register_approved_package(
        v1_exemption_ledger=VerifiedV1ExemptionLedger(),
        v1_preregistration_ledger=VerifiedV1PreregistrationLedger(),
        authorized_by="SAM_AND_PAT_USER_APPROVAL",
        approval_text=APPROVAL_TEXT,
    )
    assert first == second
    assert len(target.records()) == 3
    with pytest.raises(ValueError, match="approved proposal hash"):
        target.register_approved_package(
            v1_exemption_ledger=VerifiedV1ExemptionLedger(),
            v1_preregistration_ledger=VerifiedV1PreregistrationLedger(),
            authorized_by="SAM_AND_PAT_USER_APPROVAL",
            approval_text="I approve something else",
        )
    with pytest.raises(LedgerIntegrityError, match="authority differs"):
        target.register_approved_package(
            v1_exemption_ledger=VerifiedV1ExemptionLedger(),
            v1_preregistration_ledger=VerifiedV1PreregistrationLedger(),
            authorized_by="SUBSTITUTED_USER",
            approval_text=APPROVAL_TEXT,
        )


def test_open_or_invalid_v1_chain_cannot_parent_campaign_v2(tmp_path):
    target = ledger(tmp_path)
    with pytest.raises(LedgerIntegrityError, match="verified closed chain"):
        target.register_approved_package(
            v1_exemption_ledger=VerifiedV1ExemptionLedger(closed=False),
            v1_preregistration_ledger=VerifiedV1PreregistrationLedger(),
            authorized_by="SAM_AND_PAT_USER_APPROVAL",
            approval_text=APPROVAL_TEXT,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"campaign_v1_untouched_reusable": True},
        {"campaign_v1_evaluation_allowed": True},
        {"research_exemption_scope_extension_granted": True},
        {"research_exemption_active_for_original_scope": False},
        {"exemption_record_hash": "f" * 64},
        {"closure_reason_codes": []},
    ],
)
def test_invalid_v1_closure_payload_cannot_parent_v2(tmp_path, overrides):
    target = ledger(tmp_path)
    with pytest.raises(LedgerIntegrityError, match="does not authorize a v2 parent"):
        target.register_approved_package(
            v1_exemption_ledger=VerifiedV1ExemptionLedger(
                closure_overrides=overrides
            ),
            v1_preregistration_ledger=VerifiedV1PreregistrationLedger(),
            authorized_by="SAM_AND_PAT_USER_APPROVAL",
            approval_text=APPROVAL_TEXT,
        )


def _rewrite_rehashed_chain(path: Path, event_index: int, changes: dict) -> None:
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[event_index]["payload"].update(changes)
    if event_index == 2 and "entitlement_metadata" in changes:
        rows[2]["payload"]["preregistration_id"] = registration_module._plan_id(
            rows[2]["payload"]
        )
    previous = GENESIS_HASH
    for row in rows:
        row["previous_hash"] = previous
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
        previous = row["record_hash"]
    path.write_text(
        "".join(
            json.dumps(
                row,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
            for row in rows
        )
    )
    path.chmod(0o600)


@pytest.mark.parametrize(
    ("event_index", "changes", "message"),
    [
        (0, {"data_calls_allowed": True}, "approval payload"),
        (1, {"future_assumptions_asserted": True}, "exemption extension"),
        (2, {"evaluation_allowed": True}, "preregistration"),
        (2, {"untouched_test_opened": True}, "preregistration"),
        (2, {"proposal_sha256": "f" * 64}, "preregistration"),
        (2, {"approval_record_hash": "f" * 64}, "preregistration"),
        (2, {"v1_closure_record_hash": "f" * 64}, "preregistration"),
        (
            2,
            {
                "entitlement_metadata": {
                    "asserted_request_limit_per_minute": 500,
                    "redistribution_allowed": True,
                }
            },
            "preregistration",
        ),
        (2, {"preregistration_id": "HQP2-SUBSTITUTED"}, "preregistration"),
    ],
)
def test_fully_rehashed_semantic_rewrites_are_rejected(
    tmp_path, event_index, changes, message
):
    target, _ = register(tmp_path)
    _rewrite_rehashed_chain(target.path, event_index, changes)
    with pytest.raises(LedgerIntegrityError, match=message):
        target.records()


def test_registration_after_untouched_window_begins_is_rejected(tmp_path):
    target = CampaignV2ControlLedger(
        tmp_path / CONTROL_LEDGER_RELATIVE_PATH,
        repository_root=repository(tmp_path),
        clock=lambda: datetime(2026, 9, 1, tzinfo=UTC),
        git_revision_resolver=lambda _: "a" * 40,
        worktree_clean_resolver=lambda _: True,
    )
    with pytest.raises(ValueError, match="before its untouched window begins"):
        target.register_approved_package(
            v1_exemption_ledger=VerifiedV1ExemptionLedger(),
            v1_preregistration_ledger=VerifiedV1PreregistrationLedger(),
            authorized_by="SAM_AND_PAT_USER_APPROVAL",
            approval_text=APPROVAL_TEXT,
        )
    with pytest.raises(ValueError, match="before its untouched window begins"):
        _require_before_untouched_window(
            datetime(2027, 3, 1, tzinfo=UTC), proposal_package()
        )


def test_tampered_control_chain_fails_hash_verification(tmp_path):
    target, _ = register(tmp_path)
    raw = target.path.read_text()
    target.path.chmod(0o600)
    target.path.write_text(raw.replace("CONTROL_PACKAGE_ONLY", "CONTROL_PACKAGE_MORE"))
    target.path.chmod(0o600)
    with pytest.raises(LedgerIntegrityError, match="line 1 is invalid"):
        target.records()


def test_unknown_fourth_record_and_out_of_order_resume_are_rejected(tmp_path):
    target, _ = register(tmp_path)
    rows = [json.loads(line) for line in target.path.read_text().splitlines()]
    extra = dict(rows[-1])
    extra["event_type"] = "UNKNOWN_FOURTH_EVENT"
    extra["previous_hash"] = rows[-1]["record_hash"]
    material = {key: value for key, value in extra.items() if key != "record_hash"}
    extra["record_hash"] = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with target.path.open("a") as stream:
        stream.write(json.dumps(extra, sort_keys=True, separators=(",", ":")) + "\n")
    target.path.chmod(0o600)
    with pytest.raises(LedgerIntegrityError, match="line 4 is invalid"):
        target.records()

    other = ledger(tmp_path / "other")
    with pytest.raises(LedgerIntegrityError, match="out of order"):
        other._append_expected(EVENT_SEQUENCE[1], {})


def test_calendar_supersession_is_terminal_fail_closed_and_idempotent(tmp_path):
    target, plan = register(tmp_path)
    (tmp_path / QUARANTINE_ROOT_RELATIVE_PATH).mkdir(parents=True)

    first = target.supersede_calendar_conflict()
    second = target.supersede_calendar_conflict()
    assert first == second
    assert [row["event_type"] for row in target.records()] == list(
        CONTROL_EVENT_SEQUENCE
    )
    assert first["event_type"] == SUPERSESSION_EVENT
    payload = first["payload"]
    assert payload["superseded_preregistration_id"] == plan["preregistration_id"]
    assert payload["superseded_quarantine_file_count"] == 0
    assert payload["provider_request_made_under_superseded_controls"] is False
    assert payload["replacement_test_semantic_role"] == "SEALED_RETROSPECTIVE_TEST"
    assert payload["replacement_preregistration_registered"] is False
    assert payload["data_calls_allowed"] is False
    assert payload["evaluation_allowed"] is False
    with pytest.raises(ValueError, match="controls were superseded"):
        target.require(plan["preregistration_id"])
    with pytest.raises(ValueError, match="controls were superseded"):
        target.register_approved_package(
            v1_exemption_ledger=VerifiedV1ExemptionLedger(),
            v1_preregistration_ledger=VerifiedV1PreregistrationLedger(),
            authorized_by="SAM_AND_PAT_USER_APPROVAL",
            approval_text=APPROVAL_TEXT,
        )


def test_truncating_terminal_record_cannot_resurrect_retired_controls(tmp_path):
    target, plan = register(tmp_path)
    (tmp_path / QUARANTINE_ROOT_RELATIVE_PATH).mkdir(parents=True)
    target.supersede_calendar_conflict()
    rows = target.path.read_text().splitlines()
    target.path.write_text("\n".join(rows[:3]) + "\n")
    target.path.chmod(0o600)
    assert len(target.records()) == 3
    with pytest.raises(ValueError, match="controls were superseded"):
        target.require(plan["preregistration_id"])


def test_calendar_supersession_rejects_nonempty_quarantine(tmp_path):
    target, _ = register(tmp_path)
    raw = tmp_path / QUARANTINE_ROOT_RELATIVE_PATH
    raw.mkdir(parents=True)
    (raw / "provider-byte.bin").write_bytes(b"must not exist")
    with pytest.raises(LedgerIntegrityError, match="empty directory"):
        target.supersede_calendar_conflict()


@pytest.mark.parametrize(
    "changes",
    [
        {"acquisition_end": "2026-08-15"},
        {"acquisition_end": "2025-08-01"},
        {
            "splits": [
                {"role": "TRAIN", "start": "2024-08-01", "end": "2025-02-28"},
                {"role": "VALIDATION", "start": "2025-03-02", "end": "2025-04-30"},
                {"role": "UNTOUCHED_TEST", "start": "2025-05-01", "end": "2025-07-31"},
            ]
        },
    ],
)
def test_supersession_independently_rejects_bad_revision_dates(
    tmp_path, monkeypatch, changes
):
    target, _ = register(tmp_path)
    replacement = registration_module.revision_proposal_package()
    replacement.update(changes)
    monkeypatch.setattr(
        registration_module, "revision_proposal_package", lambda: replacement
    )
    with pytest.raises(LedgerIntegrityError, match="revision"):
        target.supersede_calendar_conflict()


def test_rehashed_calendar_supersession_authority_changes_are_rejected(tmp_path):
    target, _ = register(tmp_path)
    (tmp_path / QUARANTINE_ROOT_RELATIVE_PATH).mkdir(parents=True)
    target.supersede_calendar_conflict()
    _rewrite_rehashed_chain(target.path, 3, {"data_calls_allowed": True})
    with pytest.raises(LedgerIntegrityError, match="supersession"):
        target.records()


def test_registration_requires_clean_git_identity(tmp_path):
    root = repository(tmp_path)
    target = CampaignV2ControlLedger(
        tmp_path / CONTROL_LEDGER_RELATIVE_PATH,
        repository_root=root,
        git_revision_resolver=lambda _: "a" * 40,
        worktree_clean_resolver=lambda _: False,
    )
    with pytest.raises(ValueError, match="worktree must be clean"):
        target.register_approved_package(
            v1_exemption_ledger=VerifiedV1ExemptionLedger(),
            v1_preregistration_ledger=VerifiedV1PreregistrationLedger(),
            authorized_by="SAM_AND_PAT_USER_APPROVAL",
            approval_text=APPROVAL_TEXT,
        )


def test_initializes_only_empty_owner_only_disjoint_v2_quarantine(tmp_path):
    admitted = tmp_path / "data/research/admitted-source-blobs"
    admitted.mkdir(parents=True)
    target = initialize_campaign_v2_quarantine_root(
        tmp_path, admitted_store_roots=[admitted]
    )
    assert target == (tmp_path / QUARANTINE_ROOT_RELATIVE_PATH).resolve()
    assert list(target.iterdir()) == []
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    with pytest.raises(LedgerIntegrityError, match="admitted roots overlap"):
        initialize_campaign_v2_quarantine_root(
            tmp_path, admitted_store_roots=[target / "nested"]
        )


def test_quarantine_initializer_rejects_v1_overlap_nonempty_and_ledger_overlap(
    tmp_path, monkeypatch
):
    admitted = tmp_path / "admitted"
    admitted.mkdir()
    monkeypatch.setattr(
        registration_module,
        "V1_QUARANTINE_ROOT_RELATIVE_PATH",
        QUARANTINE_ROOT_RELATIVE_PATH,
    )
    with pytest.raises(LedgerIntegrityError, match="v1 quarantine roots overlap"):
        initialize_campaign_v2_quarantine_root(
            tmp_path, admitted_store_roots=[admitted]
        )
    monkeypatch.setattr(
        registration_module,
        "V1_QUARANTINE_ROOT_RELATIVE_PATH",
        Path("data/research/massive_quarantine/raw"),
    )
    target = tmp_path / QUARANTINE_ROOT_RELATIVE_PATH
    target.mkdir(parents=True)
    (target / "unexpected").write_text("not data")
    with pytest.raises(LedgerIntegrityError, match="must start empty"):
        initialize_campaign_v2_quarantine_root(
            tmp_path, admitted_store_roots=[admitted]
        )
    monkeypatch.setattr(
        registration_module,
        "CONTROL_LEDGER_RELATIVE_PATH",
        QUARANTINE_ROOT_RELATIVE_PATH / "preregistration.jsonl",
    )
    with pytest.raises(LedgerIntegrityError, match="control ledger overlaps"):
        initialize_campaign_v2_quarantine_root(
            tmp_path / "ledger-overlap", admitted_store_roots=[admitted]
        )


def test_control_module_has_no_runtime_replay_admission_broker_or_client_imports():
    source = Path(registration_module.__file__).read_text()
    tree = ast.parse(source)

    class RuntimeImportVisitor(ast.NodeVisitor):
        def __init__(self):
            self.imports = set()

        def visit_If(self, node):
            if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
                return
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            self.imports.add(node.module or "")

        def visit_Import(self, node):
            self.imports.update(alias.name for alias in node.names)

    visitor = RuntimeImportVisitor()
    visitor.visit(tree)
    runtime_imports = visitor.imports
    forbidden = {
        "core.research.massive_research_exempt_replay",
        "core.research.vectorbt_pilot",
        "core.orchestration.replay_dataset_admission",
        "core.orchestration.massive_historical_adapter",
        "core.orchestration.massive_historical_quarantine",
    }
    assert runtime_imports.isdisjoint(forbidden)
    assert not any(
        "broker" in name or "alpaca" in name for name in runtime_imports
    )


def test_approved_digest_is_anchored_in_both_authoritative_documents():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "docs/PROJECT_STATUS.md",
        "docs/MASTER_ROADMAP_COMPLETION_AUDIT.md",
    ):
        assert PROPOSAL_SHA256 in (root / relative).read_text()


class NoDataLedger:
    def require(self, preregistration_id):
        return {
            "preregistration_id": preregistration_id,
            "data_calls_allowed": False,
        }


class NoDataStore:
    preregistration_ledger = NoDataLedger()

    def verify(self):
        raise AssertionError("store must not be opened before data-call authorization")


class NoCallClient:
    def fetch_daily_bars(self, **kwargs):
        raise AssertionError("provider client must not be called")


def test_fetcher_rejects_v2_before_store_or_provider_access():
    fetcher = MassiveHistoricalQuarantineFetcher(
        store=NoDataStore(), client=NoCallClient()
    )
    with pytest.raises(ValueError, match="does not authorize provider data calls"):
        fetcher.fetch("HQP2-LOCKED")


class FuturePlanLedger:
    def __init__(self):
        tomorrow = date.today() + timedelta(days=1)
        self.plan = {
            "preregistration_id": "HQP2-FUTURE",
            "policy_version": "massive-future-window-preregistration-v2",
            "data_calls_allowed": True,
            "entitlement_metadata": {"asserted_request_limit_per_minute": 5},
            "acquisition_start": tomorrow.isoformat(),
            "acquisition_end": tomorrow.isoformat(),
            "maximum_chunk_days": 31,
            "target_basket": ["AAPL"],
            "splits": [
                {
                    "role": "TRAIN",
                    "start": tomorrow.isoformat(),
                    "end": tomorrow.isoformat(),
                }
            ],
            "request_slice_must_be_complete_and_historical_at_fetch_time": True,
        }

    def require(self, preregistration_id):
        assert preregistration_id == "HQP2-FUTURE"
        return self.plan


class EmptyFutureStore:
    def __init__(self):
        self.preregistration_ledger = FuturePlanLedger()

    def verify(self):
        return []

    def status(self, preregistration_id):
        return {"preregistration_id": preregistration_id, "complete": False}


def test_fetcher_skips_incomplete_future_slices_without_calling_provider():
    result = MassiveHistoricalQuarantineFetcher(
        store=EmptyFutureStore(), client=NoCallClient()
    ).fetch("HQP2-FUTURE")
    assert result == {"preregistration_id": "HQP2-FUTURE", "complete": False}


def test_store_refuses_v2_capture_while_control_plan_has_no_data_authority(tmp_path):
    store = MassiveHistoricalQuarantineStore(
        tmp_path / "raw",
        preregistration_ledger=NoDataLedger(),
        admitted_store_roots=[tmp_path / "admitted"],
    )
    with pytest.raises(ValueError, match="does not authorize provider data calls"):
        store.capture_response(
            preregistration_id="HQP2-LOCKED",
            symbol="AAPL",
            request_start="2026-03-01",
            request_end="2026-03-31",
            request_uri="https://api.massive.com/never-called",
            request_query_canonical="never-called",
            requested_at=datetime.now(UTC),
            retrieved_at=datetime.now(UTC),
            response_status_code=200,
            response_headers_sha256="0" * 64,
            payload=b"not-opened",
            provider_access={},
        )
