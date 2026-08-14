from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from core.broker.alpaca_paper import PaperOrderProposalLedger
from core.broker.paper_account_snapshot import PaperBrokerAccountSnapshotLedger
from core.broker.provider_paper_execution_stress_evidence import (
    ProviderPaperExecutionStressEvidenceLedger,
)
from core.broker.provider_paper_execution_stress_policy import (
    ProviderPaperExecutionStressPolicyLedger,
)
from core.broker.provider_paper_kill_switch import ProviderPaperKillSwitchLedger
from core.broker.provider_paper_open_order_quantity_evidence import (
    ProviderPaperOpenOrderQuantityEvidenceLedger,
)
from core.broker.provider_paper_operational_assessment import (
    EXTERNAL_BLOCKERS,
    ProviderPaperOperationalAssessmentLedger,
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
BUY_ORDER = "1" * 64
SELL_ORDER = "2" * 64
BASE_NOW = datetime.now(timezone.utc).replace(microsecond=0)
SCENARIOS = {
    "BASE": {
        "commission_bps_per_side": "1",
        "spread_bps_per_side": "2",
        "slippage_bps_per_side": "10",
        "latency_bps_per_side": "1",
        "market_impact_bps_per_side": "2",
        "maximum_volume_participation_rate": "0.05",
        "maximum_order_age_minutes": "30",
    },
    "PESSIMISTIC": {
        "commission_bps_per_side": "2",
        "spread_bps_per_side": "4",
        "slippage_bps_per_side": "20",
        "latency_bps_per_side": "2",
        "market_impact_bps_per_side": "4",
        "maximum_volume_participation_rate": "0.02",
        "maximum_order_age_minutes": "60",
    },
}


def moment(seconds):
    return (BASE_NOW + timedelta(seconds=seconds)).isoformat()


def build(
    tmp_path,
    *,
    side="BUY",
    quantity=5,
    settled_cash="1000",
    max_order="1000",
    max_age=180,
    max_account_age=None,
    max_risk_age=None,
    pending_buy_ticker="AAPL",
    max_order_age_minutes="60",
    held_quantity="10",
    pending_sell_quantity="3",
    proposal_offset=-70,
    stress_offset=-40,
):
    account_ledger = PaperBrokerAccountSnapshotLedger(tmp_path / "account.jsonl")
    account = account_ledger.record(
        broker="Alpaca",
        account_reference_sha256=ACCOUNT,
        observed_at=moment(-120),
        recorded_at=moment(-118),
        cash=settled_cash,
        settled_cash=settled_cash,
        unsettled_cash="0",
        buying_power=settled_cash,
        equity=str(Decimal(settled_cash) + Decimal("800")),
        source_payload_sha256=ACCOUNT_PAYLOAD,
        paper_account_confirmed=True,
    )
    risk_ledger = ProviderPaperRiskSnapshotLedger(tmp_path / "risk.jsonl", account_ledger)
    risk = risk_ledger.record(
        account_snapshot_id=account["snapshot_id"],
        account_snapshot_record_hash=account["record_hash"],
        observed_at=moment(-90),
        recorded_at=moment(-88),
        previous_close_equity_usd=account["equity"],
        previous_close_observed_at=moment(-86520),
        previous_close_equity_source_payload_sha256=PRIOR_PAYLOAD,
        positions=[
            {"ticker": "AAPL", "long_market_value_usd": "500"},
            {"ticker": "MSFT", "long_market_value_usd": "300"},
        ],
        open_orders=[
            {
                "order_reference_sha256": BUY_ORDER,
                "ticker": pending_buy_ticker,
                "side": "BUY",
                "remaining_notional_usd": "200",
            },
            {
                "order_reference_sha256": SELL_ORDER,
                "ticker": "AAPL",
                "side": "SELL",
                "remaining_notional_usd": "150",
            },
        ],
        positions_source_payload_sha256=POSITIONS_PAYLOAD,
        open_orders_source_payload_sha256=ORDERS_PAYLOAD,
        paper_account_confirmed=True,
    )

    risk_policy_ledger = ProviderPaperRiskControlPolicyLedger(tmp_path / "risk-policy.jsonl")
    risk_policy = risk_policy_ledger.preregister(
        account_reference_sha256=ACCOUNT,
        portfolio_version="portfolio-v1",
        strategy_version="strategy-v1",
        max_order_notional_usd=max_order,
        max_position_notional_usd="2000",
        max_gross_exposure_usd="3000",
        max_daily_loss_usd="200",
        max_account_snapshot_age_seconds=(
            max_age if max_account_age is None else max_account_age
        ),
        max_risk_snapshot_age_seconds=(
            max_age if max_risk_age is None else max_risk_age
        ),
        kill_switch_identifier="paper-stop-v1",
        decided_by="Sam",
        decision_reference="synthetic-operational-risk-policy",
        human_decision_confirmed=True,
        effective_not_before=moment(-500),
        git_revision="abc123",
        recorded_at=moment(-900),
    )
    proposal_ledger = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    proposal = proposal_ledger.propose(
        decision_id=f"decision-{side.lower()}",
        portfolio_version="portfolio-v1",
        ticker="AAPL",
        side=side,
        quantity=quantity,
        reference_price=100,
        target_weight=0.2,
        strategy_version="strategy-v1",
        model_versions=[{"component": "research", "version": "1.0"}],
        created_at=moment(proposal_offset),
        git_revision="abc123",
    )
    kill_ledger = ProviderPaperKillSwitchLedger(tmp_path / "stops.jsonl", risk_policy_ledger)
    shadow_ledger = ProviderPaperShadowRiskAssessmentLedger(
        tmp_path / "shadow.jsonl",
        proposal_ledger,
        risk_policy_ledger,
        risk_ledger,
        kill_ledger,
    )
    shadow = shadow_ledger.assess(
        order_id=proposal["order_id"],
        proposal_record_hash=proposal["record_hash"],
        policy_id=risk_policy["policy_id"],
        policy_record_hash=risk_policy["record_hash"],
        risk_snapshot_id=risk["snapshot_id"],
        risk_snapshot_record_hash=risk["record_hash"],
        assessed_at=moment(-60),
        recorded_at=moment(-55),
    )

    position_ledger = ProviderPaperPositionQuantityEvidenceLedger(
        tmp_path / "position-quantity.jsonl", risk_ledger
    )
    position = position_ledger.record(
        risk_snapshot_id=risk["snapshot_id"],
        risk_snapshot_record_hash=risk["record_hash"],
        positions=[
            {
                "ticker": "AAPL",
                "long_quantity": held_quantity,
                "mark_price_usd": str(Decimal("500") / Decimal(held_quantity)),
            },
            {"ticker": "MSFT", "long_quantity": "3", "mark_price_usd": "100"},
        ],
        recorded_at=moment(-85),
    )
    order_ledger = ProviderPaperOpenOrderQuantityEvidenceLedger(
        tmp_path / "order-quantity.jsonl", risk_ledger
    )
    order = order_ledger.record(
        risk_snapshot_id=risk["snapshot_id"],
        risk_snapshot_record_hash=risk["record_hash"],
        open_orders=[
            {
                "order_reference_sha256": BUY_ORDER,
                "ticker": pending_buy_ticker,
                "side": "BUY",
                "remaining_quantity": "2",
                "risk_mark_price_usd": "100",
            },
            {
                "order_reference_sha256": SELL_ORDER,
                "ticker": "AAPL",
                "side": "SELL",
                "remaining_quantity": pending_sell_quantity,
                "risk_mark_price_usd": str(
                    Decimal("150") / Decimal(pending_sell_quantity)
                ),
            },
        ],
        recorded_at=moment(-84),
    )
    sell_ledger = ProviderPaperSellQuantityAssessmentLedger(
        tmp_path / "sell.jsonl", shadow_ledger, position_ledger, order_ledger, kill_ledger
    )
    sell = None
    if side == "SELL":
        sell = sell_ledger.assess(
            shadow_assessment_id=shadow["assessment_id"],
            shadow_assessment_record_hash=shadow["record_hash"],
            position_quantity_snapshot_id=position["quantity_snapshot_id"],
            position_quantity_record_hash=position["record_hash"],
            order_quantity_snapshot_id=order["order_quantity_snapshot_id"],
            order_quantity_record_hash=order["record_hash"],
            assessed_at=moment(-50),
            recorded_at=moment(-45),
        )

    stress_policy_ledger = ProviderPaperExecutionStressPolicyLedger(
        tmp_path / "stress-policy.jsonl"
    )
    stress_scenarios = deepcopy(SCENARIOS)
    stress_scenarios["BASE"]["maximum_order_age_minutes"] = max_order_age_minutes
    stress_scenarios["PESSIMISTIC"]["maximum_order_age_minutes"] = (
        max_order_age_minutes
    )
    stress_policy = stress_policy_ledger.preregister(
        account_reference_sha256=ACCOUNT,
        portfolio_version="portfolio-v1",
        strategy_version="strategy-v1",
        scenarios=stress_scenarios,
        effective_not_before=moment(-500),
        decided_by="Sam",
        decision_reference="synthetic-operational-cost-policy",
        human_decision_confirmed=True,
        git_revision="abc123",
        recorded_at=moment(-900),
    )
    stress_ledger = ProviderPaperExecutionStressEvidenceLedger(
        tmp_path / "stress.jsonl", proposal_ledger, stress_policy_ledger
    )
    stress = stress_ledger.calculate(
        order_id=proposal["order_id"],
        proposal_record_hash=proposal["record_hash"],
        execution_stress_policy_id=stress_policy["execution_stress_policy_id"],
        execution_stress_policy_record_hash=stress_policy["record_hash"],
        assessed_at=moment(stress_offset),
        recorded_at=moment(-35),
    )
    operational_ledger = ProviderPaperOperationalAssessmentLedger(
        tmp_path / "operational.jsonl",
        shadow_ledger,
        sell_ledger,
        stress_ledger,
        kill_ledger,
    )
    return {
        "account": account,
        "risk": risk,
        "risk_policy": risk_policy,
        "proposal": proposal,
        "shadow": shadow,
        "position": position,
        "order": order,
        "sell": sell,
        "stress": stress,
        "kill_ledger": kill_ledger,
        "operational_ledger": operational_ledger,
    }


def assess(values, **changes):
    current = datetime.now(timezone.utc).isoformat()
    inputs = {
        "shadow_assessment_id": values["shadow"]["assessment_id"],
        "shadow_assessment_record_hash": values["shadow"]["record_hash"],
        "execution_stress_evidence_id": values["stress"]["execution_stress_evidence_id"],
        "execution_stress_evidence_record_hash": values["stress"]["record_hash"],
        "sell_quantity_assessment_id": (
            values["sell"]["sell_quantity_assessment_id"] if values["sell"] else None
        ),
        "sell_quantity_assessment_record_hash": (
            values["sell"]["record_hash"] if values["sell"] else None
        ),
        "assessed_at": current,
        "recorded_at": current,
    }
    inputs.update(changes)
    return values["operational_ledger"].assess(**inputs)


def rewrite(path, **changes):
    from core.broker import provider_paper_operational_assessment as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_buy_combines_stressed_limits_and_settled_cash_but_stays_blocked(tmp_path):
    values = build(tmp_path)
    result = assess(values)
    assert result["status"] == "BLOCKED_EXTERNAL_EVIDENCE_REQUIRED"
    assert result["stressed_conservative_order_notional_usd"] == "501.6003"
    assert result["current_ticker_long_exposure_usd"] == "500"
    assert result["pending_ticker_buy_exposure_usd"] == "200"
    assert result["projected_ticker_exposure_usd"] == "1201.6003"
    assert result["current_conservative_gross_exposure_usd"] == "1000"
    assert result["projected_conservative_gross_exposure_usd"] == "1501.6003"
    assert result["settled_cash_usd"] == "1000"
    assert result["pending_account_buy_notional_usd"] == "200"
    assert result["pending_account_buy_stressed_cash_commitment_usd"] == "200.64012"
    assert result["required_buy_cash_debit_usd"] == "501.6003"
    assert result["total_buy_cash_commitment_usd"] == "702.24042"
    assert result["buy_cash_check_applicable"] is True
    assert result["buy_settled_cash_sufficient"] is True
    assert result["sell_quantity_check_applicable"] is False
    assert result["internal_mathematical_checks_pass"] is True
    assert result["operational_paper_readiness"] is False
    assert result["open_internal_checks"] == []
    assert result["external_blockers"] == EXTERNAL_BLOCKERS
    assert result["previous_hash"] == GENESIS_HASH
    for field in (
        "policy_active",
        "paper_account_connected",
        "broker_reconciliation_complete",
        "reference_price_source_authenticated",
        "position_order_cash_evidence_authenticated",
        "all_applicable_fees_complete",
        "risk_limits_enforced",
        "broker_access_enabled",
        "network_request_enabled",
        "order_route_exists",
        "paper_order_submission_allowed",
        "live_order_submission_allowed",
        "human_review_eligible",
        "recommendation_provided",
        "live_trading_enabled",
    ):
        assert result[field] is False


def test_sell_requires_unreserved_quantity_and_uses_stressed_order_limit(tmp_path):
    values = build(tmp_path, side="SELL", quantity=2)
    result = assess(values)
    assert result["status"] == "BLOCKED_EXTERNAL_EVIDENCE_REQUIRED"
    assert result["stressed_conservative_order_notional_usd"] == "200.03988"
    assert result["available_sell_quantity"] == "7"
    assert result["proposed_sell_quantity"] == "2"
    assert result["sell_quantity_check_applicable"] is True
    assert result["sell_quantity_within_available"] is True
    assert result["buy_cash_check_applicable"] is False
    assert result["projected_ticker_exposure_usd"] == "700"
    assert result["projected_conservative_gross_exposure_usd"] == "1000"
    assert result["internal_mathematical_checks_pass"] is True
    assert result["operational_paper_readiness"] is False


@pytest.mark.parametrize(
    "build_changes,expected_blocker",
    [
        ({"max_order": "500"}, "STRESSED_ORDER_LIMIT_BREACH"),
        ({"settled_cash": "400"}, "BUY_SETTLED_CASH_INSUFFICIENT"),
        ({"max_age": 40}, "RISK_SNAPSHOT_STALE"),
    ],
)
def test_stressed_limit_cash_and_current_freshness_fail_closed(
    tmp_path, build_changes, expected_blocker
):
    values = build(tmp_path, **build_changes)
    result = assess(values)
    assert result["status"] == "BLOCKED_INTERNAL_MATHEMATICAL_CHECKS"
    assert expected_blocker in result["open_internal_checks"]
    assert result["internal_mathematical_checks_pass"] is False
    assert result["operational_paper_readiness"] is False


def test_oversell_fails_composite_even_when_stressed_money_limits_pass(tmp_path):
    values = build(tmp_path, side="SELL", quantity=8)
    result = assess(values)
    assert result["stressed_order_notional_within_limit"] is True
    assert result["sell_quantity_within_available"] is False
    assert "SELL_QUANTITY_INSUFFICIENT" in result["open_internal_checks"]
    assert result["status"] == "BLOCKED_INTERNAL_MATHEMATICAL_CHECKS"


def test_pending_sell_overhang_is_recorded_as_blocked_not_unassessable(tmp_path):
    values = build(
        tmp_path,
        side="SELL",
        quantity=2,
        held_quantity="2",
        pending_sell_quantity="3",
    )
    result = assess(values)
    assert result["available_sell_quantity"] == "-1"
    assert result["sell_quantity_within_available"] is False
    assert "SELL_QUANTITY_INSUFFICIENT" in result["open_internal_checks"]
    assert result["status"] == "BLOCKED_INTERNAL_MATHEMATICAL_CHECKS"


def test_existing_account_wide_buy_orders_reserve_settled_cash(tmp_path):
    values = build(tmp_path, settled_cash="650", pending_buy_ticker="MSFT")
    result = assess(values)
    assert result["pending_ticker_buy_exposure_usd"] == "0"
    assert result["required_buy_cash_debit_usd"] == "501.6003"
    assert result["pending_account_buy_notional_usd"] == "200"
    assert result["pending_account_buy_stressed_cash_commitment_usd"] == "200.64012"
    assert result["total_buy_cash_commitment_usd"] == "702.24042"
    assert result["buy_settled_cash_sufficient"] is False
    assert "BUY_SETTLED_CASH_INSUFFICIENT" in result["open_internal_checks"]


def test_account_and_risk_snapshot_freshness_are_checked_separately(tmp_path):
    values = build(tmp_path, max_age=180, max_account_age=110, max_risk_age=180)
    result = assess(values)
    assert result["risk_snapshot_fresh"] is True
    assert result["account_snapshot_fresh"] is False
    assert "ACCOUNT_SNAPSHOT_STALE" in result["open_internal_checks"]


def test_backdated_recording_cannot_make_stale_evidence_look_fresh(tmp_path):
    values = build(tmp_path, max_age=180, max_account_age=180, max_risk_age=85)
    result = assess(values, recorded_at=moment(-10))
    assert result["risk_snapshot_fresh"] is False
    assert "RISK_SNAPSHOT_STALE" in result["open_internal_checks"]


def test_stale_proposal_reference_is_blocked_by_execution_policy_age(tmp_path):
    values = build(
        tmp_path,
        max_order_age_minutes="2",
        proposal_offset=-300,
        stress_offset=-40,
    )
    result = assess(values)
    assert result["proposal_reference_fresh"] is False
    assert result["execution_stress_evidence_fresh"] is True
    assert "PROPOSAL_REFERENCE_STALE" in result["open_internal_checks"]
    assert result["internal_mathematical_checks_pass"] is False


def test_sell_boolean_is_recomputed_from_exact_quantities(tmp_path, monkeypatch):
    values = build(tmp_path, side="SELL", quantity=2)
    forged = deepcopy(values["sell"])
    forged["sell_quantity_within_available"] = False
    monkeypatch.setattr(values["operational_ledger"].sell_quantity_ledger, "verify", lambda: [forged])
    with pytest.raises(ValueError, match="contradicts"):
        assess(values)


def test_negative_stressed_notional_is_rejected_defensively(tmp_path, monkeypatch):
    values = build(tmp_path)
    forged = deepcopy(values["stress"])
    forged["exact_fractions"]["conservative_risk_notional_usd"] = {
        "numerator": "-1",
        "denominator": "1",
    }
    monkeypatch.setattr(
        values["operational_ledger"].execution_stress_evidence_ledger,
        "verify",
        lambda: [forged],
    )
    with pytest.raises(ValueError, match="must be non-negative"):
        assess(values)


def test_permanent_stop_added_after_inputs_blocks_composite(tmp_path):
    values = build(tmp_path)
    stop = values["kill_ledger"].trigger(
        policy_id=values["risk_policy"]["policy_id"],
        kill_switch_identifier="paper-stop-v1",
        trigger_source="HUMAN",
        reason="Stop before operational assessment.",
        triggered_by="Sam",
        triggered_at=moment(-25),
    )
    result = assess(values)
    assert result["status"] == "BLOCKED_LATCHED_STOP"
    assert result["matching_stop_id"] == stop["stop_id"]
    assert result["kill_switch_latched"] is True
    assert "PERMANENT_STOP_LATCHED" in result["open_internal_checks"]
    assert result["internal_mathematical_checks_pass"] is False


def test_sell_requires_exact_quantity_assessment_and_buy_rejects_it(tmp_path):
    sell_values = build(tmp_path / "sell", side="SELL")
    with pytest.raises(ValueError, match="SELL quantity"):
        assess(
            sell_values,
            sell_quantity_assessment_id=None,
            sell_quantity_assessment_record_hash=None,
        )
    buy_values = build(tmp_path / "buy", side="BUY")
    with pytest.raises(ValueError, match="cannot include SELL"):
        assess(
            buy_values,
            sell_quantity_assessment_id="fake",
            sell_quantity_assessment_record_hash="0" * 64,
        )


@pytest.mark.parametrize(
    "field",
    [
        "shadow_assessment_record_hash",
        "execution_stress_evidence_record_hash",
    ],
)
def test_wrong_input_hashes_fail_closed(tmp_path, field):
    values = build(tmp_path)
    with pytest.raises(ValueError, match="hash"):
        assess(values, **{field: "0" * 64})


def test_stale_and_future_recording_times_are_rejected(tmp_path):
    values = build(tmp_path)
    with pytest.raises(ValueError, match="within five minutes"):
        assess(values, recorded_at=moment(-600))
    with pytest.raises(ValueError, match="future"):
        assess(values, recorded_at="2099-01-01T00:00:00+00:00")


def test_concurrent_retry_is_idempotent(tmp_path):
    values = build(tmp_path)
    assessed_at = datetime.now(timezone.utc).isoformat()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(
            executor.map(
                lambda _: assess(values, assessed_at=assessed_at), range(2)
            )
        )
    assert first == second
    assert len(values["operational_ledger"].verify()) == 1


def test_later_stop_does_not_rewrite_historical_assessment(tmp_path):
    values = build(tmp_path)
    result = assess(values)
    values["kill_ledger"].trigger(
        policy_id=values["risk_policy"]["policy_id"],
        kill_switch_identifier="paper-stop-v1",
        trigger_source="HUMAN",
        reason="Later stop.",
        triggered_by="Sam",
        triggered_at=moment(-2),
    )
    assert values["operational_ledger"].verify() == [result]


def test_later_backdated_stop_does_not_rewrite_historical_assessment(tmp_path):
    values = build(tmp_path)
    result = assess(values)
    values["kill_ledger"].trigger(
        policy_id=values["risk_policy"]["policy_id"],
        kill_switch_identifier="paper-stop-v1",
        trigger_source="RISK_MONITOR",
        reason="Recorded later with an earlier claimed trigger time.",
        triggered_by="test",
        triggered_at=moment(-25),
    )
    assert values["operational_ledger"].verify() == [result]


def test_later_backdated_stop_preserves_sell_and_operational_history(tmp_path):
    values = build(tmp_path, side="SELL")
    result = assess(values)
    sell_result = values["sell"]
    values["kill_ledger"].trigger(
        policy_id=values["risk_policy"]["policy_id"],
        kill_switch_identifier="paper-stop-v1",
        trigger_source="RISK_MONITOR",
        reason="Later recorded stop with earlier trigger time.",
        triggered_by="test",
        triggered_at=moment(-25),
    )
    assert values["operational_ledger"].sell_quantity_ledger.verify() == [sell_result]
    assert values["operational_ledger"].verify() == [result]


def test_rehashed_kill_prefix_truncation_is_rejected(tmp_path):
    values = build(tmp_path)
    values["kill_ledger"].trigger(
        policy_id=values["risk_policy"]["policy_id"],
        kill_switch_identifier="paper-stop-v1",
        trigger_source="HUMAN",
        reason="Stop before final assessment.",
        triggered_by="Sam",
        triggered_at=moment(-25),
    )
    result = assess(values)
    assert result["kill_switch_record_count"] == 1
    rewrite(
        values["operational_ledger"].path,
        kill_switch_record_count=0,
        kill_switch_head_hash=GENESIS_HASH,
        matching_stop_id=None,
        matching_stop_record_hash=None,
        kill_switch_latched=False,
        internal_mathematical_checks_pass=True,
        status="BLOCKED_EXTERNAL_EVIDENCE_REQUIRED",
        open_internal_checks=[],
    )
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        values["operational_ledger"].verify()


def test_assessment_and_stop_writes_serialize(tmp_path):
    values = build(tmp_path)

    def trigger():
        return values["kill_ledger"].trigger(
            policy_id=values["risk_policy"]["policy_id"],
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
        assert result["status"] == "BLOCKED_LATCHED_STOP"
    assert values["operational_ledger"].verify() == [result]


def test_multi_record_chain_moves_forward(tmp_path):
    values = build(tmp_path)
    first = assess(values)
    second = assess(values)
    assert second["previous_hash"] == first["record_hash"]
    assert len(values["operational_ledger"].verify()) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "READY"},
        {"stressed_conservative_order_notional_usd": "1"},
        {"open_internal_checks": []},
        {"external_blockers": []},
        {"internal_mathematical_checks_pass": True},
        {"operational_paper_readiness": True},
        {"broker_reconciliation_complete": True},
        {"reference_price_source_authenticated": True},
        {"risk_limits_enforced": True},
        {"broker_access_enabled": True},
        {"network_request_enabled": True},
        {"order_route_exists": True},
        {"paper_order_submission_allowed": True},
        {"human_review_eligible": True},
        {"recommendation_provided": True},
        {"live_trading_enabled": True},
        {"unexpected": True},
    ],
)
def test_rehashed_semantic_or_authority_tampering_is_detected(tmp_path, changes):
    values = build(tmp_path, max_order="500")
    assess(values)
    rewrite(values["operational_ledger"].path, **changes)
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        values["operational_ledger"].verify()
