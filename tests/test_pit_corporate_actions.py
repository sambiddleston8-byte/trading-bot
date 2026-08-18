from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json

import pytest

import core.orchestration.pit_corporate_actions as module
from core.decision_ledger import LedgerIntegrityError
from core.guardrailed_backtest import (
    ACTION_ENTER_LONG,
    ACTION_HOLD,
    BacktestConfig,
    ExchangeFeeSchedule,
    ExchangeFeeTier,
    GuardrailedBacktestEngine,
    MarketBar,
    ResearchExemptionDataAttestation,
    UniverseEvent,
)
from core.orchestration.pit_corporate_actions import (
    PITCorporateActionLedger,
    PITCorporateActionReconciliation,
    PITCorporateActionResearchInputs,
)
from core.portfolio.pit_security_master import PointInTimeSecurityMasterLedger


UTC = timezone.utc
CLOCK = datetime(2026, 8, 18, 12, tzinfo=UTC)
SOURCE_HASH = hashlib.sha256(b"deterministic synthetic corporate-action fixture").hexdigest()
SECURITY_ID = "SYNTHETIC:AAA:0001"


def record_security(master, *, event_type, effective_at, ticker="AAA", **changes):
    arguments = {
        "security_id": SECURITY_ID,
        "event_type": event_type,
        "ticker": ticker,
        "issuer_name": "Synthetic AAA Inc",
        "exchange_mic": "XNYS",
        "effective_at": effective_at,
        "reported_at": "2019-01-01T00:00:00+00:00",
        "available_at": "2019-01-01T00:00:00+00:00",
        "retrieved_at": "2019-01-02T00:00:00+00:00",
        "recorded_at": "2019-01-03T00:00:00+00:00",
        "source_uri": "https://security.example.invalid/synthetic-aaa",
        "source_input_sha256": "a" * 64,
        "source_locator": f"security:{event_type.lower()}",
    }
    arguments.update(changes)
    return master.record_event(**arguments)


def ledgers(tmp_path, *, delisting_changes=None):
    master = PointInTimeSecurityMasterLedger(tmp_path / "security.jsonl")
    record_security(master, event_type="LISTED", effective_at="2019-01-02T14:30:00+00:00")
    record_security(
        master,
        event_type="INDEX_ADDED",
        effective_at="2019-01-03T14:30:00+00:00",
        universe="SP500",
    )
    delisting_arguments = {
        "reported_at": "2020-01-06T12:00:00+00:00",
        "available_at": "2020-01-06T13:00:00+00:00",
        "retrieved_at": "2020-01-06T14:00:00+00:00",
        "recorded_at": "2020-01-06T15:00:00+00:00",
        "terminal_outcome_treatment": "BANKRUPTCY_OR_LIQUIDATION_OUTCOME_REQUIRED",
    }
    delisting_arguments.update(delisting_changes or {})
    delisting = record_security(
        master,
        event_type="DELISTED",
        effective_at="2020-01-08T14:30:00+00:00",
        **delisting_arguments,
    )
    return (
        master,
        PITCorporateActionLedger(
            tmp_path / "actions.jsonl", master, clock=lambda: CLOCK
        ),
        delisting,
    )


def event_common(source_event_id, event_type, effective_at, **changes):
    value = {
        "source_event_id": source_event_id,
        "event_type": event_type,
        "effective_at": effective_at,
        "reported_at": "2019-12-01T12:00:00+00:00",
        "available_at": "2019-12-01T13:00:00+00:00",
        "retrieved_at": "2020-01-09T12:00:00+00:00",
        "recorded_at": "2020-01-09T13:00:00+00:00",
        "source_locator": f"fixture:{source_event_id}",
    }
    value.update(changes)
    return value


def split(**changes):
    value = event_common(
        "split-1", "SPLIT", "2020-01-04T14:30:00+00:00", split_ratio="2"
    )
    value.update(changes)
    return value


def dividend(**changes):
    value = event_common(
        "dividend-1",
        "CASH_DIVIDEND",
        "2020-01-05T14:30:00+00:00",
        cash_per_share="0.50",
        currency="USD",
        cash_paid_at="2020-01-07T14:30:00+00:00",
    )
    value.update(changes)
    return value


def terminal(delisting, **changes):
    value = event_common(
        "terminal-1",
        "TERMINAL_OUTCOME",
        "2020-01-08T14:30:00+00:00",
        reported_at="2020-01-06T12:00:00+00:00",
        available_at="2020-01-06T13:00:00+00:00",
        retrieved_at="2020-01-09T12:00:00+00:00",
        recorded_at="2020-01-09T13:00:00+00:00",
        terminal_type="BANKRUPT",
        recovery_per_share="0",
        currency="USD",
        cash_settled_at="2020-01-08T14:30:00+00:00",
        delisting_event_id=delisting["event_id"],
        delisting_event_record_hash=delisting["record_hash"],
    )
    value.update(changes)
    return value


def snapshot(ledger, delisting, *, events=None, **changes):
    arguments = {
        "security_id": SECURITY_ID,
        "ticker": "AAA",
        "covers_from_at": "2020-01-01T00:00:00+00:00",
        "through_at": "2020-01-09T00:00:00+00:00",
        "events": events if events is not None else [terminal(delisting), dividend(), split()],
        "source_uri": "https://actions.example.invalid/synthetic-aaa",
        "source_locator": "fixture:synthetic-aaa-v1",
        "source_payload_sha256": SOURCE_HASH,
        "synthetic_fixture": True,
    }
    arguments.update(changes)
    return ledger.append_snapshot(**arguments)


def test_snapshot_is_deterministic_provider_neutral_five_timestamp_and_research_only(tmp_path):
    _, first_ledger, delisting = ledgers(tmp_path / "first")
    _, second_ledger, second_delisting = ledgers(tmp_path / "second")
    first = snapshot(first_ledger, delisting)
    second = snapshot(
        second_ledger,
        second_delisting,
        events=[split(), dividend(), terminal(second_delisting)],
    )

    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["record_hash"] == second["record_hash"]
    assert first["point_in_time_contract"] == (
        "effective_at/reported_at/available_at/retrieved_at/recorded_at"
    )
    assert [item["event_type"] for item in first["events"]] == [
        "SPLIT", "CASH_DIVIDEND", "TERMINAL_OUTCOME"
    ]
    assert first["all_events_available_by_effective_at"] is True
    assert first["synthetic_fixture"] is True
    assert first["provider_request_made"] is False
    assert first["current_ticker_lookup_used"] is False
    master_records = first_ledger.security_master.verify()
    assert first["security_master_record_count"] == len(master_records)
    assert first["security_master_record_hash"] == master_records[-1]["record_hash"]
    identity = master_records[0]
    assert first["events"][0]["identity_event_id"] == identity["event_id"]
    assert first["events"][0]["identity_event_record_hash"] == identity["record_hash"]
    assert first["events"][-1]["identity_event_record_hash"] == delisting["record_hash"]
    for authority in (
        "coverage_completeness_proven",
        "qualified",
        "train_admitted",
        "validation_admitted",
        "test_admitted",
        "performance_claim_allowed",
        "candidate_freeze_allowed",
        "promotion_allowed",
        "broker_submission_enabled",
        "live_trading_enabled",
    ):
        assert first[authority] is False


def test_materializes_only_bounded_engine_types_with_no_authority(tmp_path):
    _, ledger, delisting = ledgers(tmp_path)
    record = snapshot(ledger, delisting)
    result = ledger.materialize_research_inputs(record["snapshot_id"])

    assert [item.action_type for item in result.corporate_actions] == [
        "SPLIT", "CASH_DIVIDEND"
    ]
    assert result.corporate_actions[0].split_ratio == Decimal("2")
    assert result.corporate_actions[1].cash_per_share == Decimal("0.50")
    assert result.terminal_outcomes[0].terminal_type == "BANKRUPT"
    assert result.terminal_outcomes[0].recovery_per_share == 0
    assert result.snapshot_record_hash == record["record_hash"]
    assert result.synthetic_fixture is True
    assert result.qualified is False
    assert result.admitted is False
    assert result.performance_claim_allowed is False
    assert result.promotion_allowed is False
    assert result.broker_submission_enabled is False
    assert result.live_trading_enabled is False


def test_late_knowledge_is_preserved_but_fails_closed_at_replay_boundary(tmp_path):
    _, ledger, delisting = ledgers(tmp_path)
    late = split(
        reported_at="2020-01-04T15:00:00+00:00",
        available_at="2020-01-04T16:00:00+00:00",
    )
    record = snapshot(ledger, delisting, events=[late])
    assert record["events"][0]["available_by_effective_at"] is False
    assert record["all_events_available_by_effective_at"] is False
    with pytest.raises(ValueError, match="late corporate-action knowledge"):
        ledger.materialize_research_inputs(record["snapshot_id"])


def test_mixed_timely_and_late_snapshot_fails_closed_as_one_unit(tmp_path):
    _, ledger, delisting = ledgers(tmp_path)
    late = split(
        reported_at="2020-01-04T15:00:00+00:00",
        available_at="2020-01-04T16:00:00+00:00",
    )
    record = snapshot(ledger, delisting, events=[dividend(), late])
    assert [item["available_by_effective_at"] for item in record["events"]] == [
        False,
        True,
    ]
    with pytest.raises(ValueError, match="late corporate-action knowledge"):
        ledger.materialize_research_inputs(record["snapshot_id"])


@pytest.mark.parametrize(
    "events, message",
    [
        ([split(recorded_at="2020-01-08T00:00:00+00:00", retrieved_at="2020-01-09T00:00:00+00:00")], "reported <= available"),
        ([split(split_ratio="1")], "cannot be one"),
        ([dividend(cash_paid_at="2020-01-04T00:00:00+00:00")], "cannot be paid before"),
        ([dividend(currency="EUR")], "qualified FX model"),
        ([split(source_event_id="same"), dividend(source_event_id="same")], "must be unique"),
    ],
)
def test_invalid_event_economics_or_provenance_fail_closed(tmp_path, events, message):
    _, ledger, delisting = ledgers(tmp_path)
    with pytest.raises(ValueError, match=message):
        snapshot(ledger, delisting, events=events)
    assert ledger.records() == []


def test_permanent_identity_and_exact_delisting_link_are_required(tmp_path):
    _, ledger, delisting = ledgers(tmp_path)
    with pytest.raises(ValueError, match="known active permanent identity"):
        snapshot(ledger, delisting, events=[split()], security_id="SYNTHETIC:OTHER:0001")
    with pytest.raises(ValueError, match="exact permanent-identity delisting"):
        snapshot(
            ledger,
            delisting,
            events=[terminal(delisting, delisting_event_record_hash="f" * 64)],
        )


def test_post_delisting_action_and_late_delisting_knowledge_fail_closed(tmp_path):
    master, ledger, delisting = ledgers(tmp_path / "post-delist")
    post_delisting = split(effective_at="2020-01-09T14:30:00+00:00")
    with pytest.raises(ValueError, match="known active permanent identity"):
        snapshot(
            ledger,
            delisting,
            events=[post_delisting],
            through_at="2020-01-10T00:00:00+00:00",
        )
    with pytest.raises(LedgerIntegrityError, match="cannot be listed twice"):
        record_security(
            master,
            event_type="LISTED",
            effective_at="2020-01-09T14:30:00+00:00",
            reported_at="2020-01-08T12:00:00+00:00",
            available_at="2020-01-08T13:00:00+00:00",
            retrieved_at="2020-01-08T14:00:00+00:00",
            recorded_at="2020-01-08T15:00:00+00:00",
        )

    _, late_ledger, late_delisting = ledgers(
        tmp_path / "late-delist",
        delisting_changes={
            "reported_at": "2020-05-31T12:00:00+00:00",
            "available_at": "2020-06-01T12:00:00+00:00",
            "retrieved_at": "2020-06-01T13:00:00+00:00",
            "recorded_at": "2020-06-01T14:00:00+00:00",
        },
    )
    with pytest.raises(ValueError, match="exact permanent-identity delisting"):
        snapshot(late_ledger, late_delisting, events=[terminal(late_delisting)])


def test_later_master_backfill_cannot_rewrite_or_brick_immutable_snapshot(tmp_path):
    master = PointInTimeSecurityMasterLedger(tmp_path / "security.jsonl")
    record_security(master, event_type="LISTED", effective_at="2019-01-02T14:30:00+00:00")
    record_security(
        master,
        event_type="INDEX_ADDED",
        effective_at="2019-01-03T14:30:00+00:00",
        universe="SP500",
    )
    ledger = PITCorporateActionLedger(
        tmp_path / "actions.jsonl", master, clock=lambda: CLOCK
    )
    stored = snapshot(ledger, None, events=[split()])
    pinned_hash = stored["security_master_record_hash"]

    record_security(
        master,
        event_type="DELISTED",
        effective_at="2020-01-02T14:30:00+00:00",
        reported_at="2020-01-02T12:00:00+00:00",
        available_at="2020-01-03T12:00:00+00:00",
        retrieved_at="2020-01-03T13:00:00+00:00",
        recorded_at="2020-01-03T14:00:00+00:00",
        terminal_outcome_treatment="BANKRUPTCY_OR_LIQUIDATION_OUTCOME_REQUIRED",
    )

    assert len(master.verify()) == stored["security_master_record_count"] + 1
    assert ledger.verify()[0]["security_master_record_hash"] == pinned_hash
    inputs = ledger.materialize_research_inputs(stored["snapshot_id"])
    assert [item.action_type for item in inputs.corporate_actions] == ["SPLIT"]
    reconciliation = ledger.reconcile_snapshot(stored["snapshot_id"])
    assert reconciliation.status == "STALE_MASTER_EVIDENCE"
    assert reconciliation.reason_code == "SECURITY_MASTER_EVOLVED"
    assert reconciliation.current_security_master_record_count == len(master.verify())
    assert reconciliation.current_security_master_record_hash == master.verify()[-1]["record_hash"]
    assert reconciliation.dataset_admitted is False
    assert reconciliation.performance_claim_allowed is False
    assert reconciliation.promotion_allowed is False


def test_explicit_supersession_preserves_history_and_only_leaf_materializes(tmp_path):
    master = PointInTimeSecurityMasterLedger(tmp_path / "security.jsonl")
    record_security(master, event_type="LISTED", effective_at="2019-01-02T14:30:00+00:00")
    ledger = PITCorporateActionLedger(tmp_path / "actions.jsonl", master, clock=lambda: CLOCK)
    original = snapshot(ledger, None, events=[split()])

    record_security(
        master,
        event_type="DELISTED",
        effective_at="2020-01-02T14:30:00+00:00",
        reported_at="2020-01-02T12:00:00+00:00",
        available_at="2020-01-03T12:00:00+00:00",
        retrieved_at="2020-01-03T13:00:00+00:00",
        recorded_at="2020-01-03T14:00:00+00:00",
        terminal_outcome_treatment="BANKRUPTCY_OR_LIQUIDATION_OUTCOME_REQUIRED",
    )
    replacement = snapshot(
        ledger,
        None,
        events=[],
        source_locator="fixture:synthetic-aaa-master-backfill",
        source_payload_sha256="b" * 64,
        supersedes_snapshot_id=original["snapshot_id"],
        supersession_reason="MASTER_BACKFILL",
    )

    records = ledger.verify()
    assert [item["snapshot_id"] for item in records] == [
        original["snapshot_id"],
        replacement["snapshot_id"],
    ]
    assert records[0]["record_hash"] == original["record_hash"]
    with pytest.raises(ValueError, match="superseded.*cannot be materialized"):
        ledger.materialize_research_inputs(original["snapshot_id"])
    assert ledger.materialize_research_inputs(replacement["snapshot_id"]).corporate_actions == ()

    original_status = ledger.reconcile_snapshot(original["snapshot_id"])
    assert original_status.status == "SUPERSEDED"
    assert original_status.superseded_by_snapshot_id == replacement["snapshot_id"]
    assert original_status.reason_code == "MASTER_BACKFILL"
    replacement_status = ledger.reconcile_snapshot(replacement["snapshot_id"])
    assert replacement_status.status == "CURRENT"
    assert replacement_status.superseded_by_snapshot_id is None
    assert replacement_status.reason_code is None


def test_supersession_contract_rejects_missing_unknown_forked_or_mismatched_targets(tmp_path):
    _, ledger, delisting = ledgers(tmp_path)
    original = snapshot(ledger, delisting)

    with pytest.raises(ValueError, match="id and reason.*together"):
        snapshot(
            ledger,
            delisting,
            events=[],
            source_payload_sha256="b" * 64,
            supersedes_snapshot_id=original["snapshot_id"],
        )
    with pytest.raises(ValueError, match="unsupported"):
        snapshot(
            ledger,
            delisting,
            events=[],
            source_payload_sha256="b" * 64,
            supersedes_snapshot_id=original["snapshot_id"],
            supersession_reason="OPTIMIZE_RESULT",
        )
    with pytest.raises(LedgerIntegrityError, match="target does not exist"):
        snapshot(
            ledger,
            delisting,
            events=[],
            source_payload_sha256="b" * 64,
            supersedes_snapshot_id="PCAS-UNKNOWN",
            supersession_reason="SOURCE_CORRECTION",
        )
    with pytest.raises(LedgerIntegrityError, match="exact identity and coverage"):
        snapshot(
            ledger,
            delisting,
            events=[],
            through_at="2020-01-10T00:00:00+00:00",
            source_payload_sha256="b" * 64,
            supersedes_snapshot_id=original["snapshot_id"],
            supersession_reason="COVERAGE_RECAPTURE",
        )

    replacement = snapshot(
        ledger,
        delisting,
        events=[],
        source_payload_sha256="b" * 64,
        supersedes_snapshot_id=original["snapshot_id"],
        supersession_reason="SOURCE_CORRECTION",
    )
    with pytest.raises(LedgerIntegrityError, match="cannot fork"):
        snapshot(
            ledger,
            delisting,
            events=[],
            source_payload_sha256="c" * 64,
            supersedes_snapshot_id=original["snapshot_id"],
            supersession_reason="SOURCE_CORRECTION",
        )

    leaf = snapshot(
        ledger,
        delisting,
        events=[split()],
        source_payload_sha256="d" * 64,
        supersedes_snapshot_id=replacement["snapshot_id"],
        supersession_reason="COVERAGE_RECAPTURE",
    )
    assert ledger.materialize_research_inputs(leaf["snapshot_id"]).corporate_actions
    with pytest.raises(ValueError, match="superseded.*cannot be materialized"):
        ledger.materialize_research_inputs(replacement["snapshot_id"])


def test_coverage_event_and_append_boundaries_fail_closed(tmp_path):
    _, ledger, delisting = ledgers(tmp_path / "coverage")
    with pytest.raises(ValueError, match="outside declared snapshot coverage"):
        snapshot(
            ledger,
            delisting,
            events=[split()],
            covers_from_at="2020-01-05T00:00:00+00:00",
        )
    with pytest.raises(ValueError, match="cannot follow the immutable append time"):
        snapshot(
            ledger,
            delisting,
            events=[split(recorded_at="2026-08-19T00:00:00+00:00")],
        )


def test_overlapping_snapshots_and_projected_oversize_append_fail_closed(tmp_path, monkeypatch):
    _, ledger, delisting = ledgers(tmp_path / "overlap")
    snapshot(ledger, delisting)
    with pytest.raises(LedgerIntegrityError, match="overlapping.*ambiguous"):
        snapshot(
            ledger,
            delisting,
            events=[],
            source_payload_sha256="b" * 64,
        )

    _, small_ledger, small_delisting = ledgers(tmp_path / "oversize")
    monkeypatch.setattr(module, "MAX_LEDGER_BYTES", 100)
    with pytest.raises(LedgerIntegrityError, match="target is unsafe"):
        snapshot(small_ledger, small_delisting)
    assert small_ledger.records() == []


def test_research_input_carrier_cannot_assert_authority():
    with pytest.raises(ValueError, match="cannot assert"):
        PITCorporateActionResearchInputs(
            snapshot_id="PCAS-TEST",
            snapshot_record_hash="a" * 64,
            corporate_actions=(),
            terminal_outcomes=(),
            promotion_allowed=True,
        )


def test_reconciliation_carrier_cannot_assert_authority_or_invalid_state():
    arguments = {
        "snapshot_id": "PCAS-TEST",
        "snapshot_record_hash": "a" * 64,
        "status": "CURRENT",
        "current_security_master_record_count": 1,
        "current_security_master_record_hash": "b" * 64,
    }
    with pytest.raises(ValueError, match="cannot assert"):
        PITCorporateActionReconciliation(**arguments, promotion_allowed=True)
    with pytest.raises(ValueError, match="cannot assert a reason"):
        PITCorporateActionReconciliation(**arguments, reason_code="SOURCE_CORRECTION")
    with pytest.raises(ValueError, match="requires an allowed reason"):
        PITCorporateActionReconciliation(
            **{**arguments, "status": "SUPERSEDED"},
            superseded_by_snapshot_id="PCAS-CHILD",
            reason_code="SECURITY_MASTER_EVOLVED",
        )


def test_snapshot_is_idempotent_and_hash_chain_detects_tampering(tmp_path):
    _, ledger, delisting = ledgers(tmp_path)
    first = snapshot(ledger, delisting)
    assert snapshot(ledger, delisting) == first
    assert len(ledger.verify()) == 1

    value = ledger.records()[0]
    value["events"][0]["split_ratio"] = "3"
    ledger.path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        ledger.verify()


class EnterAndHold:
    version = "synthetic-delisted-name-replay-v1"

    def decide(self, symbol, history_through_signal_close, parameters):
        if len(history_through_signal_close) == 4:
            return ACTION_ENTER_LONG
        return ACTION_HOLD


def bars():
    result = []
    start = datetime(2020, 1, 1, 14, 30, tzinfo=UTC)
    for index in range(8):
        opened = start + timedelta(days=index)
        price = Decimal("100") + index
        result.append(
            MarketBar(
                "AAA",
                opened,
                opened + timedelta(hours=6, minutes=30),
                opened + timedelta(hours=6, minutes=30),
                price,
                price + 1,
                price - 1,
                price,
                Decimal("100000"),
            )
        )
    return result


def engine():
    attestation = ResearchExemptionDataAttestation._from_explicit_research_exemption(
        source_id="RESEARCH_EXEMPTION:SYNTHETIC:PIT_CORPORATE_ACTIONS",
        source_content_sha256="1" * 64,
        validation_receipt_sha256="2" * 64,
        derivation_policy_version="pit-corporate-action-research-v2",
        evidence_role_hashes=(("ASSUMED_SYNTHETIC_CORPORATE_ACTIONS", "3" * 64),),
        exemption_id="SYNTHETIC-PIT-CORPORATE-ACTIONS",
        exemption_record_sha256="4" * 64,
    )
    return GuardrailedBacktestEngine(
        config=BacktestConfig(
            initial_cash=Decimal("100000"), atr_window=2, lagged_liquidity_lookback=2
        ),
        fee_schedule=ExchangeFeeSchedule(
            "SYNTHETIC-FEES", (ExchangeFeeTier(None, Decimal("1"), Decimal("0")),)
        ),
        data_attestation=attestation,
    )


def test_delisted_name_fixture_replays_exactly_through_guardrailed_engine(tmp_path):
    _, ledger, delisting = ledgers(tmp_path)
    record = snapshot(ledger, delisting)
    inputs = ledger.materialize_research_inputs(record["snapshot_id"])
    market = bars()
    arguments = {
        "bars": market,
        "universe_events": [
            UniverseEvent(
                "AAA",
                "ADD",
                market[0].open_at - timedelta(days=1),
                market[0].open_at - timedelta(days=2),
                "synthetic:membership",
            )
        ],
        "terminal_outcomes": inputs.terminal_outcomes,
        "corporate_actions": inputs.corporate_actions,
        "prices_are_unadjusted": True,
        "strategy": EnterAndHold(),
        "parameters": {},
        "evaluation_start": market[0].close_at,
        "evaluation_end": market[-1].close_at,
    }
    first = engine().run(**arguments)
    second = engine().run(**arguments)

    assert first == second
    trade = first.completed_trades[0]
    assert trade.exit_reason == "BANKRUPT"
    assert trade.exit_net_proceeds == 0
    assert trade.return_rate == -1
    assert first.performance_claim_allowed is False
    assert first.broker_connection_allowed is False
    assert first.orders_submitted is False
    assert first.live_trading_enabled is False
