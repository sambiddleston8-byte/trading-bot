from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from core.broker.alpaca_paper import PaperOrderProposalLedger
from core.broker.paper_account_snapshot import PaperBrokerAccountSnapshotLedger
from core.broker.provider_paper_kill_switch import ProviderPaperKillSwitchLedger
from core.broker.provider_paper_risk_policy import ProviderPaperRiskControlPolicyLedger
from core.broker.provider_paper_risk_snapshot import ProviderPaperRiskSnapshotLedger
from core.broker.provider_paper_shadow_risk_assessment import (
    ProviderPaperShadowRiskAssessmentLedger,
)
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


ACCOUNT = "a" * 64
PAYLOAD = "b" * 64
POSITIONS_PAYLOAD = "c" * 64
ORDERS_PAYLOAD = "d" * 64
PRIOR_CLOSE_PAYLOAD = "e" * 64
ORDER_REFERENCE = "1" * 64
BASE_NOW = datetime.now(timezone.utc).replace(microsecond=0)


def moment(seconds):
    return (BASE_NOW + timedelta(seconds=seconds)).isoformat()


def build(
    tmp_path, *, policy_changes=None, proposal_changes=None, risk_age_seconds=30
):
    risk_offset = -risk_age_seconds
    account_offset = risk_offset - 30
    proposal_offset = risk_offset + 10
    effective_offset = risk_offset - 270
    account_ledger = PaperBrokerAccountSnapshotLedger(tmp_path / "account.jsonl")
    account = account_ledger.record(
        broker="Alpaca",
        account_reference_sha256=ACCOUNT,
        observed_at=moment(account_offset),
        recorded_at=moment(account_offset + 5),
        cash="700",
        settled_cash="650",
        unsettled_cash="50",
        buying_power="650",
        equity="1500",
        source_payload_sha256=PAYLOAD,
        paper_account_confirmed=True,
    )
    snapshot_ledger = ProviderPaperRiskSnapshotLedger(
        tmp_path / "risk-snapshot.jsonl", account_ledger
    )
    snapshot = snapshot_ledger.record(
        account_snapshot_id=account["snapshot_id"],
        account_snapshot_record_hash=account["record_hash"],
        observed_at=moment(risk_offset),
        recorded_at=moment(risk_offset + 5),
        previous_close_equity_usd="1600",
        previous_close_observed_at=moment(account_offset - 86400),
        previous_close_equity_source_payload_sha256=PRIOR_CLOSE_PAYLOAD,
        positions=[
            {"ticker": "AAPL", "long_market_value_usd": "500"},
            {"ticker": "MSFT", "long_market_value_usd": "300"},
        ],
        open_orders=[
            {
                "order_reference_sha256": ORDER_REFERENCE,
                "ticker": "AAPL",
                "side": "BUY",
                "remaining_notional_usd": "200",
            }
        ],
        positions_source_payload_sha256=POSITIONS_PAYLOAD,
        open_orders_source_payload_sha256=ORDERS_PAYLOAD,
        paper_account_confirmed=True,
    )
    policy_values = {
        "account_reference_sha256": ACCOUNT,
        "portfolio_version": "portfolio-v1",
        "strategy_version": "strategy-v1",
        "max_order_notional_usd": "1000",
        "max_position_notional_usd": "2000",
        "max_gross_exposure_usd": "3000",
        "max_daily_loss_usd": "200",
        "max_account_snapshot_age_seconds": 120,
        "max_risk_snapshot_age_seconds": 120,
        "kill_switch_identifier": "paper-stop-v1",
        "decided_by": "Sam",
        "decision_reference": "synthetic-shadow-policy",
        "human_decision_confirmed": True,
        "effective_not_before": moment(effective_offset),
        "git_revision": "abc123",
        "recorded_at": moment(effective_offset - 300),
    }
    policy_values.update(policy_changes or {})
    policy_ledger = ProviderPaperRiskControlPolicyLedger(tmp_path / "policy.jsonl")
    policy = policy_ledger.preregister(**policy_values)
    proposal_values = {
        "decision_id": "decision-v1",
        "portfolio_version": "portfolio-v1",
        "ticker": "AAPL",
        "side": "BUY",
        "quantity": 5,
        "reference_price": 100,
        "target_weight": 0.2,
        "strategy_version": "strategy-v1",
        "model_versions": [{"component": "research", "version": "1.0"}],
        "created_at": moment(proposal_offset),
        "git_revision": "abc123",
    }
    proposal_values.update(proposal_changes or {})
    proposal_ledger = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    proposal = proposal_ledger.propose(**proposal_values)
    kill_ledger = ProviderPaperKillSwitchLedger(tmp_path / "stops.jsonl", policy_ledger)
    assessment_ledger = ProviderPaperShadowRiskAssessmentLedger(
        tmp_path / "assessments.jsonl",
        proposal_ledger,
        policy_ledger,
        snapshot_ledger,
        kill_ledger,
    )
    return {
        "account": account,
        "snapshot": snapshot,
        "snapshot_ledger": snapshot_ledger,
        "policy": policy,
        "policy_ledger": policy_ledger,
        "proposal": proposal,
        "proposal_ledger": proposal_ledger,
        "kill_ledger": kill_ledger,
        "assessment_ledger": assessment_ledger,
    }


def assess(values, **changes):
    inputs = {
        "order_id": values["proposal"]["order_id"],
        "proposal_record_hash": values["proposal"]["record_hash"],
        "policy_id": values["policy"]["policy_id"],
        "policy_record_hash": values["policy"]["record_hash"],
        "risk_snapshot_id": values["snapshot"]["snapshot_id"],
        "risk_snapshot_record_hash": values["snapshot"]["record_hash"],
        "assessed_at": moment(-10),
        "recorded_at": moment(-5),
    }
    inputs.update(changes)
    return values["assessment_ledger"].assess(**inputs)


def rewrite(path, **changes):
    from core.broker import provider_paper_shadow_risk_assessment as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_buy_within_limits_is_shadow_only_and_never_submittable(tmp_path):
    values = build(tmp_path)
    result = assess(values)
    assert result["status"] == "SHADOW_LIMITS_WITHIN_INACTIVE_UNRECONCILED"
    assert result["order_notional_usd"] == "500"
    assert result["current_ticker_long_exposure_usd"] == "500"
    assert result["pending_ticker_buy_exposure_usd"] == "200"
    assert result["projected_ticker_exposure_usd"] == "1200"
    assert result["current_conservative_gross_exposure_usd"] == "1000"
    assert result["projected_conservative_gross_exposure_usd"] == "1500"
    assert result["daily_loss_usd"] == "100"
    expected_age = (
        datetime.fromisoformat(result["recorded_at"])
        - datetime.fromisoformat(values["snapshot"]["observed_at"])
    ).total_seconds()
    assert Decimal(result["risk_snapshot_age_seconds"]) == Decimal(str(expected_age))
    assert result["mathematical_shadow_checks_pass"] is True
    assert result["previous_hash"] == GENESIS_HASH
    for field in (
        "policy_active",
        "risk_snapshot_broker_reconciled",
        "risk_snapshot_cryptographically_authenticated",
        "risk_limits_enforced",
        "execution_price_stress_applied",
        "fees_included",
        "fill_price_uncertainty_resolved",
        "external_head_anchor_present",
        "cryptographic_authentication_present",
        "broker_access_enabled",
        "broker_credentials_accessed",
        "order_route_exists",
        "paper_order_submission_allowed",
        "live_order_submission_allowed",
        "human_review_eligible",
        "recommendation_provided",
        "live_trading_enabled",
    ):
        assert result[field] is False


@pytest.mark.parametrize(
    "policy_changes,false_field",
    [
        ({"max_order_notional_usd": "400"}, "order_notional_within_limit"),
        ({"max_position_notional_usd": "1100"}, "position_notional_within_limit"),
        (
            {
                "max_position_notional_usd": "1300",
                "max_gross_exposure_usd": "1400",
            },
            "gross_exposure_within_limit",
        ),
        ({"max_daily_loss_usd": "50"}, "daily_loss_within_limit"),
        ({"max_risk_snapshot_age_seconds": 10}, "risk_snapshot_fresh"),
    ],
)
def test_each_limit_breach_is_reported_without_submission(
    tmp_path, policy_changes, false_field
):
    values = build(tmp_path, policy_changes=policy_changes)
    result = assess(values)
    assert result["status"] == "SHADOW_LIMIT_BREACH_INACTIVE_UNRECONCILED"
    assert result[false_field] is False
    assert result["mathematical_shadow_checks_pass"] is False
    assert result["paper_order_submission_allowed"] is False


def test_sell_never_reduces_exposure_and_requires_quantity_evidence(tmp_path):
    values = build(tmp_path, proposal_changes={"side": "SELL", "quantity": 2})
    result = assess(values)
    assert result["status"] == "SHADOW_INCOMPLETE_SELL_QUANTITY_EVIDENCE"
    assert result["projected_ticker_exposure_usd"] == "700"
    assert result["projected_conservative_gross_exposure_usd"] == "1000"
    assert result["sell_quantity_evidence_complete"] is False
    assert result["mathematical_shadow_checks_pass"] is False


def test_matching_latched_stop_blocks_shadow_result(tmp_path):
    values = build(tmp_path)
    stop = values["kill_ledger"].trigger(
        policy_id=values["policy"]["policy_id"],
        kill_switch_identifier="paper-stop-v1",
        trigger_source="RISK_MONITOR",
        reason="Synthetic threshold breach.",
        triggered_by="test-monitor",
        triggered_at=moment(-15),
    )
    result = assess(values)
    assert result["status"] == "SHADOW_BLOCKED_LATCHED_STOP"
    assert result["kill_switch_latched"] is True
    assert result["matching_stop_id"] == stop["stop_id"]
    assert result["mathematical_shadow_checks_pass"] is False


def test_existing_stop_blocks_even_when_trigger_time_follows_assessed_time(tmp_path):
    values = build(tmp_path)
    values["kill_ledger"].trigger(
        policy_id=values["policy"]["policy_id"],
        kill_switch_identifier="paper-stop-v1",
        trigger_source="HUMAN",
        reason="Permanent stop recorded before assessment write.",
        triggered_by="Sam",
        triggered_at=moment(-2),
    )
    result = assess(values)
    assert result["status"] == "SHADOW_BLOCKED_LATCHED_STOP"
    assert result["kill_switch_latched"] is True
    assert result["mathematical_shadow_checks_pass"] is False


@pytest.mark.parametrize(
    "build_changes,assessment_changes,fragment",
    [
        ({"proposal_changes": {"portfolio_version": "wrong"}}, {}, "portfolio"),
        ({"proposal_changes": {"strategy_version": "wrong"}}, {}, "strategy"),
        ({"policy_changes": {"account_reference_sha256": "f" * 64}}, {}, "account"),
        ({}, {"assessed_at": moment(-40)}, "predates"),
        ({}, {"proposal_record_hash": "0" * 64}, "hash"),
        ({}, {"risk_snapshot_record_hash": "0" * 64}, "hash"),
    ],
)
def test_identity_time_and_pin_mismatches_fail_closed(
    tmp_path, build_changes, assessment_changes, fragment
):
    values = build(tmp_path, **build_changes)
    with pytest.raises(ValueError, match=fragment):
        assess(values, **assessment_changes)


def test_later_kill_append_does_not_invalidate_pinned_empty_prefix(tmp_path):
    values = build(tmp_path)
    result = assess(values)
    assert result["kill_switch_record_count"] == 0
    values["kill_ledger"].trigger(
        policy_id=values["policy"]["policy_id"],
        kill_switch_identifier="paper-stop-v1",
        trigger_source="HUMAN",
        reason="Later synthetic stop.",
        triggered_by="Sam",
        triggered_at=moment(-2),
    )
    assert values["assessment_ledger"].verify() == [result]


def test_stale_caller_clock_cannot_claim_fresh_snapshot(tmp_path):
    values = build(tmp_path)
    with pytest.raises(ValueError, match="within five minutes"):
        assess(values, recorded_at=moment(-600))


def test_real_recording_time_marks_old_snapshot_stale(tmp_path):
    values = build(tmp_path, risk_age_seconds=3600)
    result = assess(values)
    assert result["risk_snapshot_fresh"] is False
    assert result["status"] == "SHADOW_LIMIT_BREACH_INACTIVE_UNRECONCILED"
    assert Decimal(result["risk_snapshot_age_seconds"]) >= Decimal("3600")


def test_assessment_and_stop_writes_serialize_on_shared_kill_lock(tmp_path):
    values = build(tmp_path)

    def trigger_stop():
        return values["kill_ledger"].trigger(
            policy_id=values["policy"]["policy_id"],
            kill_switch_identifier="paper-stop-v1",
            trigger_source="SYSTEM_FAILURE_GUARD",
            reason="Concurrent synthetic stop.",
            triggered_by="test-guard",
            triggered_at=moment(-15),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        assessment_future = executor.submit(assess, values)
        stop_future = executor.submit(trigger_stop)
        result = assessment_future.result()
        stop_future.result()
    assert result["kill_switch_record_count"] in {0, 1}
    if result["kill_switch_record_count"] == 1:
        assert result["status"] == "SHADOW_BLOCKED_LATCHED_STOP"
    else:
        assert result["status"] == "SHADOW_LIMITS_WITHIN_INACTIVE_UNRECONCILED"
    assert values["assessment_ledger"].verify() == [result]


def test_concurrent_retry_is_idempotent(tmp_path):
    values = build(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: assess(values), range(2)))
    assert first == second
    assert len(values["assessment_ledger"].verify()) == 1


def test_equal_time_with_changed_kill_prefix_is_rejected_before_append(tmp_path):
    values = build(tmp_path)
    assess(values)
    values["kill_ledger"].trigger(
        policy_id=values["policy"]["policy_id"],
        kill_switch_identifier="paper-stop-v1",
        trigger_source="HUMAN",
        reason="Future relative to assessment.",
        triggered_by="Sam",
        triggered_at=moment(-2),
    )
    with pytest.raises(ValueError, match="move forward"):
        assess(values)
    assert len(values["assessment_ledger"].verify()) == 1


def test_multi_record_chain_moves_forward(tmp_path):
    values = build(tmp_path)
    first = assess(values)
    second = assess(
        values,
        assessed_at=moment(-8),
        recorded_at=moment(-4),
    )
    assert second["previous_hash"] == first["record_hash"]
    assert values["assessment_ledger"].verify() == [first, second]


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "SHADOW_LIMIT_BREACH_INACTIVE_UNRECONCILED"},
        {"order_notional_usd": "1"},
        {"mathematical_shadow_checks_pass": False},
        {"risk_limits_enforced": True},
        {"execution_price_stress_applied": True},
        {"order_route_exists": True},
        {"paper_order_submission_allowed": True},
        {"live_trading_enabled": True},
        {"kill_switch_record_count": 99},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    values = build(tmp_path)
    assess(values)
    rewrite(values["assessment_ledger"].path, **changes)
    with pytest.raises(LedgerIntegrityError):
        values["assessment_ledger"].verify()


def test_rehashed_unexpected_field_is_rejected(tmp_path):
    values = build(tmp_path)
    assess(values)
    rewrite(values["assessment_ledger"].path, approved=True)
    with pytest.raises(LedgerIntegrityError):
        values["assessment_ledger"].verify()
