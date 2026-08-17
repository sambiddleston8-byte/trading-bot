from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from core.broker.alpaca_paper import PaperOrderProposalLedger
from core.broker.paper_account_snapshot import PaperBrokerAccountSnapshotLedger
from core.broker.provider_paper_kill_switch import ProviderPaperKillSwitchLedger
from core.broker.provider_paper_open_order_quantity_evidence import (
    ProviderPaperOpenOrderQuantityEvidenceLedger,
)
from core.broker.provider_paper_position_quantity_evidence import (
    ProviderPaperPositionQuantityEvidenceLedger,
)
from core.broker.provider_paper_risk_policy import ProviderPaperRiskControlPolicyLedger
from core.broker.provider_paper_risk_snapshot import ProviderPaperRiskSnapshotLedger
from core.broker.provider_paper_sell_quantity_assessment import (
    ProviderPaperSellQuantityAssessmentLedger,
)
from core.broker.provider_paper_shadow_risk_assessment import (
    ProviderPaperShadowRiskAssessmentLedger,
)
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


ACCOUNT = "a" * 64
ACCOUNT_PAYLOAD = "b" * 64
POSITIONS_PAYLOAD = "c" * 64
ORDERS_PAYLOAD = "d" * 64
PRIOR_PAYLOAD = "e" * 64
SELL_ORDER = "1" * 64
BUY_ORDER = "2" * 64
BASE_NOW = datetime.now(timezone.utc).replace(microsecond=0)


def moment(seconds):
    return (BASE_NOW + timedelta(seconds=seconds)).isoformat()


def build(
    tmp_path,
    *,
    proposal_side="SELL",
    proposal_quantity=2,
    held_quantity="10",
    pending_sell_quantity="3",
    policy_changes=None,
):
    global BASE_NOW
    BASE_NOW = datetime.now(timezone.utc).replace(microsecond=0)
    account_ledger = PaperBrokerAccountSnapshotLedger(tmp_path / "account.jsonl")
    account = account_ledger.record(
        broker="Alpaca",
        account_reference_sha256=ACCOUNT,
        observed_at=moment(-90),
        recorded_at=moment(-88),
        cash="1000",
        settled_cash="1000",
        unsettled_cash="0",
        buying_power="1000",
        equity="1500",
        source_payload_sha256=ACCOUNT_PAYLOAD,
        paper_account_confirmed=True,
    )
    risk_ledger = ProviderPaperRiskSnapshotLedger(tmp_path / "risk.jsonl", account_ledger)
    positions = []
    if held_quantity is not None:
        positions = [{"ticker": "AAPL", "long_market_value_usd": "500"}]
    open_orders = [
        {
            "order_reference_sha256": BUY_ORDER,
            "ticker": "MSFT",
            "side": "BUY",
            "remaining_notional_usd": "100",
        }
    ]
    if pending_sell_quantity is not None:
        open_orders.append(
            {
                "order_reference_sha256": SELL_ORDER,
                "ticker": "AAPL",
                "side": "SELL",
                "remaining_notional_usd": "150",
            }
        )
    risk = risk_ledger.record(
        account_snapshot_id=account["snapshot_id"],
        account_snapshot_record_hash=account["record_hash"],
        observed_at=moment(-60),
        recorded_at=moment(-58),
        previous_close_equity_usd="1500",
        previous_close_observed_at=moment(-86490),
        previous_close_equity_source_payload_sha256=PRIOR_PAYLOAD,
        positions=positions,
        open_orders=open_orders,
        positions_source_payload_sha256=POSITIONS_PAYLOAD,
        open_orders_source_payload_sha256=ORDERS_PAYLOAD,
        paper_account_confirmed=True,
    )

    position_ledger = ProviderPaperPositionQuantityEvidenceLedger(
        tmp_path / "position-quantities.jsonl", risk_ledger
    )
    position_inputs = []
    if held_quantity is not None:
        position_inputs = [
            {
                "ticker": "AAPL",
                "long_quantity": held_quantity,
                "mark_price_usd": str(500 / float(held_quantity)),
            }
        ]
    position = position_ledger.record(
        risk_snapshot_id=risk["snapshot_id"],
        risk_snapshot_record_hash=risk["record_hash"],
        positions=position_inputs,
        recorded_at=moment(-56),
    )
    order_ledger = ProviderPaperOpenOrderQuantityEvidenceLedger(
        tmp_path / "order-quantities.jsonl", risk_ledger
    )
    order_inputs = [
        {
            "order_reference_sha256": BUY_ORDER,
            "ticker": "MSFT",
            "side": "BUY",
            "remaining_quantity": "1",
            "risk_mark_price_usd": "100",
        }
    ]
    if pending_sell_quantity is not None:
        order_inputs.append(
            {
                "order_reference_sha256": SELL_ORDER,
                "ticker": "AAPL",
                "side": "SELL",
                "remaining_quantity": pending_sell_quantity,
                "risk_mark_price_usd": str(150 / float(pending_sell_quantity)),
            }
        )
    order = order_ledger.record(
        risk_snapshot_id=risk["snapshot_id"],
        risk_snapshot_record_hash=risk["record_hash"],
        open_orders=order_inputs,
        recorded_at=moment(-55),
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
        "decision_reference": "synthetic-sell-policy",
        "human_decision_confirmed": True,
        "effective_not_before": moment(-300),
        "git_revision": "abc123",
        "recorded_at": moment(-610),
    }
    policy_values.update(policy_changes or {})
    policy_ledger = ProviderPaperRiskControlPolicyLedger(tmp_path / "policy.jsonl")
    policy = policy_ledger.preregister(**policy_values)
    proposal_ledger = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    proposal = proposal_ledger.propose(
        decision_id="decision-v1",
        portfolio_version="portfolio-v1",
        ticker="AAPL",
        side=proposal_side,
        quantity=proposal_quantity,
        reference_price=100,
        target_weight=0.2,
        strategy_version="strategy-v1",
        model_versions=[{"component": "research", "version": "1.0"}],
        created_at=moment(-50),
        git_revision="abc123",
    )
    kill_ledger = ProviderPaperKillSwitchLedger(tmp_path / "stops.jsonl", policy_ledger)
    shadow_ledger = ProviderPaperShadowRiskAssessmentLedger(
        tmp_path / "shadow.jsonl",
        proposal_ledger,
        policy_ledger,
        risk_ledger,
        kill_ledger,
    )
    shadow = shadow_ledger.assess(
        order_id=proposal["order_id"],
        proposal_record_hash=proposal["record_hash"],
        policy_id=policy["policy_id"],
        policy_record_hash=policy["record_hash"],
        risk_snapshot_id=risk["snapshot_id"],
        risk_snapshot_record_hash=risk["record_hash"],
        assessed_at=moment(-40),
        recorded_at=moment(-35),
    )
    sell_ledger = ProviderPaperSellQuantityAssessmentLedger(
        tmp_path / "sell-quantity.jsonl",
        shadow_ledger,
        position_ledger,
        order_ledger,
        kill_ledger,
    )
    return {
        "risk": risk,
        "position": position,
        "order": order,
        "policy": policy,
        "policy_ledger": policy_ledger,
        "proposal": proposal,
        "shadow": shadow,
        "shadow_ledger": shadow_ledger,
        "kill_ledger": kill_ledger,
        "sell_ledger": sell_ledger,
    }


def assess(values, **changes):
    inputs = {
        "shadow_assessment_id": values["shadow"]["assessment_id"],
        "shadow_assessment_record_hash": values["shadow"]["record_hash"],
        "position_quantity_snapshot_id": values["position"]["quantity_snapshot_id"],
        "position_quantity_record_hash": values["position"]["record_hash"],
        "order_quantity_snapshot_id": values["order"]["order_quantity_snapshot_id"],
        "order_quantity_record_hash": values["order"]["record_hash"],
        "assessed_at": moment(-20),
        "recorded_at": moment(-5),
    }
    inputs.update(changes)
    return values["sell_ledger"].assess(**inputs)


def rewrite(path, **changes):
    from core.broker import provider_paper_sell_quantity_assessment as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_sell_uses_held_minus_reserved_quantity_and_remains_inactive(tmp_path):
    values = build(tmp_path)
    assert values["shadow"]["status"] == "SHADOW_INCOMPLETE_SELL_QUANTITY_EVIDENCE"
    result = assess(values)
    assert result["status"] == "SHADOW_LIMITS_WITHIN_INACTIVE_UNRECONCILED"
    assert result["held_long_quantity"] == "10"
    assert result["pending_sell_quantity"] == "3"
    assert result["available_sell_quantity"] == "7"
    assert result["proposed_sell_quantity"] == "2"
    expected_age = (
        datetime.fromisoformat(result["recorded_at"])
        - datetime.fromisoformat(values["risk"]["observed_at"])
    ).total_seconds()
    assert Decimal(result["risk_snapshot_age_seconds"]) == Decimal(str(expected_age))
    assert result["max_risk_snapshot_age_seconds"] == "120"
    assert result["sell_quantity_evidence_complete"] is True
    assert result["sell_quantity_within_available"] is True
    assert result["mathematical_shadow_checks_pass"] is True
    assert result["internal_quantity_arithmetic_only"] is True
    assert result["broker_quantity_sufficiency_proven"] is False
    assert result["previous_hash"] == GENESIS_HASH
    for field in (
        "policy_active",
        "risk_snapshot_broker_reconciled",
        "risk_limits_enforced",
        "execution_price_stress_applied",
        "fees_included",
        "broker_access_enabled",
        "order_route_exists",
        "paper_order_submission_allowed",
        "live_order_submission_allowed",
        "human_review_eligible",
        "recommendation_provided",
        "live_trading_enabled",
    ):
        assert result[field] is False


def test_pending_buy_quantity_never_reduces_sell_availability(tmp_path):
    values = build(tmp_path, pending_sell_quantity=None)
    result = assess(values)
    assert result["pending_sell_quantity"] == "0"
    assert result["available_sell_quantity"] == "10"


@pytest.mark.parametrize(
    "changes,available",
    [
        ({"proposal_quantity": 8}, "7"),
        ({"held_quantity": "2", "pending_sell_quantity": "3"}, "-1"),
        ({"held_quantity": None, "pending_sell_quantity": None}, "0"),
    ],
)
def test_oversell_reserved_overhang_and_missing_position_fail_closed(
    tmp_path, changes, available
):
    values = build(tmp_path, **changes)
    result = assess(values)
    assert result["status"] == "SHADOW_SELL_QUANTITY_BREACH_INACTIVE_UNRECONCILED"
    assert result["available_sell_quantity"] == available
    assert result["sell_quantity_within_available"] is False
    assert result["mathematical_shadow_checks_pass"] is False


def test_base_limit_failure_remains_a_composite_failure(tmp_path):
    values = build(tmp_path, policy_changes={"max_order_notional_usd": "100"})
    result = assess(values)
    assert result["base_order_notional_within_limit"] is False
    assert result["sell_quantity_within_available"] is True
    assert result["status"] == "SHADOW_LIMIT_BREACH_INACTIVE_UNRECONCILED"
    assert result["mathematical_shadow_checks_pass"] is False


def test_fresh_base_shadow_cannot_reuse_stale_quantities(tmp_path, monkeypatch):
    values = build(
        tmp_path, policy_changes={"max_risk_snapshot_age_seconds": 180}
    )
    assert values["shadow"]["risk_snapshot_fresh"] is True
    from core.broker import provider_paper_sell_quantity_assessment as module

    class LaterDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return BASE_NOW + timedelta(seconds=240)

    monkeypatch.setattr(module, "datetime", LaterDateTime)
    result = assess(values)
    assert result["base_risk_snapshot_fresh"] is True
    assert result["current_risk_snapshot_fresh"] is False
    assert result["status"] == "SHADOW_LIMIT_BREACH_INACTIVE_UNRECONCILED"
    assert result["mathematical_shadow_checks_pass"] is False


def test_stop_added_after_base_shadow_blocks_quantity_assessment(tmp_path):
    values = build(tmp_path)
    stop = values["kill_ledger"].trigger(
        policy_id=values["policy"]["policy_id"],
        kill_switch_identifier="paper-stop-v1",
        trigger_source="HUMAN",
        reason="Stop after base shadow.",
        triggered_by="Sam",
        triggered_at=moment(-25),
    )
    result = assess(values)
    assert result["status"] == "SHADOW_BLOCKED_LATCHED_STOP"
    assert result["matching_stop_id"] == stop["stop_id"]
    assert result["kill_switch_latched"] is True
    assert result["mathematical_shadow_checks_pass"] is False


def test_stop_shared_by_replacement_policy_identity_cannot_be_dropped(tmp_path):
    values = build(tmp_path)
    replacement = values["policy_ledger"].preregister(
        account_reference_sha256="f" * 64,
        portfolio_version="replacement-portfolio",
        strategy_version="replacement-strategy",
        max_order_notional_usd="1000",
        max_position_notional_usd="2000",
        max_gross_exposure_usd="3000",
        max_daily_loss_usd="200",
        max_account_snapshot_age_seconds=120,
        max_risk_snapshot_age_seconds=120,
        kill_switch_identifier="paper-stop-v1",
        decided_by="Sam",
        decision_reference="replacement-policy-same-permanent-stop",
        human_decision_confirmed=True,
        effective_not_before=moment(-300),
        git_revision="abc123",
        recorded_at=moment(-610),
    )
    values["kill_ledger"].trigger(
        policy_id=replacement["policy_id"],
        kill_switch_identifier="paper-stop-v1",
        trigger_source="HUMAN",
        reason="Shared stop identity was latched.",
        triggered_by="Sam",
        triggered_at=moment(-30),
    )
    values["shadow"] = values["shadow_ledger"].assess(
        order_id=values["proposal"]["order_id"],
        proposal_record_hash=values["proposal"]["record_hash"],
        policy_id=values["policy"]["policy_id"],
        policy_record_hash=values["policy"]["record_hash"],
        risk_snapshot_id=values["risk"]["snapshot_id"],
        risk_snapshot_record_hash=values["risk"]["record_hash"],
        assessed_at=moment(-25),
        recorded_at=moment(-24),
    )
    assert values["shadow"]["status"] == "SHADOW_BLOCKED_LATCHED_STOP"
    result = assess(values)
    assert result["status"] == "SHADOW_BLOCKED_LATCHED_STOP"
    assert result["kill_switch_latched"] is True
    assert result["mathematical_shadow_checks_pass"] is False


def test_buy_proposal_is_rejected_by_sell_only_boundary(tmp_path):
    values = build(tmp_path, proposal_side="BUY")
    with pytest.raises(ValueError, match="SELL"):
        assess(values)
    assert values["sell_ledger"].records() == []


@pytest.mark.parametrize(
    "field",
    [
        "shadow_assessment_record_hash",
        "position_quantity_record_hash",
        "order_quantity_record_hash",
    ],
)
def test_wrong_pinned_hashes_fail_closed(tmp_path, field):
    values = build(tmp_path)
    with pytest.raises(ValueError, match="hash"):
        assess(values, **{field: "0" * 64})


def test_concurrent_retry_is_idempotent(tmp_path):
    values = build(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: assess(values), range(2)))
    assert first == second
    assert len(values["sell_ledger"].verify()) == 1


def test_later_stop_does_not_rewrite_pinned_historical_prefix(tmp_path):
    values = build(tmp_path)
    result = assess(values)
    values["kill_ledger"].trigger(
        policy_id=values["policy"]["policy_id"],
        kill_switch_identifier="paper-stop-v1",
        trigger_source="HUMAN",
        reason="Later permanent stop.",
        triggered_by="Sam",
        triggered_at=moment(-2),
    )
    assert values["sell_ledger"].verify() == [result]


def test_assessment_and_stop_writes_serialize_on_shared_stop_lock(tmp_path):
    values = build(tmp_path)

    def trigger():
        return values["kill_ledger"].trigger(
            policy_id=values["policy"]["policy_id"],
            kill_switch_identifier="paper-stop-v1",
            trigger_source="RISK_MONITOR",
            reason="Concurrent stop.",
            triggered_by="test",
            triggered_at=moment(-25),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        assessment_future = executor.submit(assess, values)
        stop_future = executor.submit(trigger)
        result = assessment_future.result()
        stop_future.result()
    assert result["kill_switch_record_count"] in {0, 1}
    if result["kill_switch_record_count"] == 1:
        assert result["status"] == "SHADOW_BLOCKED_LATCHED_STOP"
    assert values["sell_ledger"].verify() == [result]


def test_multi_record_chain_requires_forward_assessment_time(tmp_path):
    values = build(tmp_path)
    first = assess(values)
    second = assess(values, assessed_at=moment(-10), recorded_at=moment(-4))
    assert second["previous_hash"] == first["record_hash"]
    values["kill_ledger"].trigger(
        policy_id=values["policy"]["policy_id"],
        kill_switch_identifier="paper-stop-v1",
        trigger_source="HUMAN",
        reason="Changed prefix at an already-used assessment time.",
        triggered_by="Sam",
        triggered_at=moment(-2),
    )
    with pytest.raises(ValueError, match="already-recorded"):
        assess(values, assessed_at=moment(-10), recorded_at=moment(-3))


def test_rehashed_stop_prefix_cannot_edit_out_a_known_permanent_stop(tmp_path):
    from core.broker import provider_paper_sell_quantity_assessment as module

    values = build(tmp_path)
    values["kill_ledger"].trigger(
        policy_id=values["policy"]["policy_id"],
        kill_switch_identifier="paper-stop-v1",
        trigger_source="HUMAN",
        reason="Known stop must remain in the prefix.",
        triggered_by="Sam",
        triggered_at=moment(-25),
    )
    result = assess(values)
    assert result["kill_switch_record_count"] == 1
    record = json.loads(values["sell_ledger"].path.read_text())
    record.update(
        {
            "kill_switch_record_count": 0,
            "kill_switch_head_hash": GENESIS_HASH,
            "matching_stop_id": None,
            "matching_stop_record_hash": None,
            "kill_switch_latched": False,
            "status": "SHADOW_LIMITS_WITHIN_INACTIVE_UNRECONCILED",
            "mathematical_shadow_checks_pass": True,
        }
    )
    record["sell_quantity_assessment_id"] = module._identity(
        values["shadow"],
        values["position"],
        values["order"],
        0,
        GENESIS_HASH,
        record["assessed_at"],
    )
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    values["sell_ledger"].path.write_text(json.dumps(record) + "\n")
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        values["sell_ledger"].verify()


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "SHADOW_LIMITS_WITHIN_INACTIVE_UNRECONCILED"},
        {"available_sell_quantity": "999"},
        {"sell_quantity_within_available": True},
        {"risk_limits_enforced": True},
        {"execution_price_stress_applied": True},
        {"paper_order_submission_allowed": True},
        {"live_trading_enabled": True},
        {"kill_switch_record_count": 99},
        {"unexpected": True},
    ],
)
def test_rehashed_semantic_or_boundary_tampering_is_detected(tmp_path, changes):
    values = build(tmp_path, proposal_quantity=8)
    assess(values)
    rewrite(values["sell_ledger"].path, **changes)
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        values["sell_ledger"].verify()


def test_stale_or_future_recording_time_is_rejected(tmp_path):
    values = build(tmp_path)
    with pytest.raises(ValueError, match="within five minutes"):
        assess(values, recorded_at=moment(-600))
    with pytest.raises(ValueError, match="future"):
        assess(values, recorded_at="2099-01-01T00:00:00+00:00")
