from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest

from core.broker.alpaca_paper import PaperOrderProposalLedger
from core.broker.provider_paper_execution_stress_evidence import (
    ProviderPaperExecutionStressEvidenceLedger,
)
from core.broker.provider_paper_execution_stress_policy import (
    ProviderPaperExecutionStressPolicyLedger,
)
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


ACCOUNT = "a" * 64
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


def policy_values(**changes):
    values = {
        "account_reference_sha256": ACCOUNT,
        "portfolio_version": "portfolio-v1",
        "strategy_version": "strategy-v1",
        "scenarios": SCENARIOS,
        "effective_not_before": moment(-300),
        "decided_by": "Sam",
        "decision_reference": "synthetic-paper-execution-stress",
        "human_decision_confirmed": True,
        "git_revision": "abc123",
        "recorded_at": moment(-610),
    }
    values.update(changes)
    return values


def register(ledger, **changes):
    return ledger.preregister(**policy_values(**changes))


def build(tmp_path, *, side="BUY", quantity=5, proposal_changes=None, policy_changes=None):
    policy_ledger = ProviderPaperExecutionStressPolicyLedger(tmp_path / "policy.jsonl")
    policy = register(policy_ledger, **(policy_changes or {}))
    proposal_ledger = PaperOrderProposalLedger(tmp_path / "proposals.jsonl")
    proposal_values = {
        "decision_id": f"decision-{side.lower()}",
        "portfolio_version": "portfolio-v1",
        "ticker": "AAPL",
        "side": side,
        "quantity": quantity,
        "reference_price": 100,
        "target_weight": 0.2,
        "strategy_version": "strategy-v1",
        "model_versions": [{"component": "research", "version": "1.0"}],
        "created_at": moment(-60),
        "git_revision": "abc123",
    }
    proposal_values.update(proposal_changes or {})
    proposal = proposal_ledger.propose(**proposal_values)
    evidence_ledger = ProviderPaperExecutionStressEvidenceLedger(
        tmp_path / "evidence.jsonl", proposal_ledger, policy_ledger
    )
    return {
        "policy": policy,
        "policy_ledger": policy_ledger,
        "proposal": proposal,
        "proposal_ledger": proposal_ledger,
        "evidence_ledger": evidence_ledger,
    }


def calculate(values, **changes):
    inputs = {
        "order_id": values["proposal"]["order_id"],
        "proposal_record_hash": values["proposal"]["record_hash"],
        "execution_stress_policy_id": values["policy"]["execution_stress_policy_id"],
        "execution_stress_policy_record_hash": values["policy"]["record_hash"],
        "assessed_at": moment(-30),
        "recorded_at": moment(-5),
    }
    inputs.update(changes)
    return values["evidence_ledger"].calculate(**inputs)


def rewrite(path, module, **changes):
    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_policy_reuses_base_pessimistic_rules_and_activates_nothing(tmp_path):
    ledger = ProviderPaperExecutionStressPolicyLedger(tmp_path / "policy.jsonl")
    result = register(ledger)
    assert result["status"] == "PREREGISTERED_INACTIVE"
    assert result["scenarios"]["BASE"]["slippage_bps_per_side"] == "10"
    assert result["scenarios"]["PESSIMISTIC"]["slippage_bps_per_side"] == "20"
    assert result["shadow_calculation_only"] is True
    assert result["previous_hash"] == GENESIS_HASH
    assert "SUBMIT_BROKER_ORDER" in result["denied_actions"]
    for field in (
        "policy_active",
        "broker_access_enabled",
        "paper_order_submission_enabled",
        "live_order_submission_enabled",
        "automatic_activation_allowed",
        "self_cost_change_allowed",
        "execution_costs_enforced",
        "broker_reconciliation_complete",
        "order_route_exists",
        "live_trading_enabled",
    ):
        assert result[field] is False
    assert ledger.verify() == [result]


@pytest.mark.parametrize(
    "mutation,fragment",
    [
        (lambda value: value["BASE"].update({"slippage_bps_per_side": "9"}), "0.10%"),
        (lambda value: value["PESSIMISTIC"].update({"slippage_bps_per_side": "19"}), "twice"),
        (lambda value: value["BASE"].update({"extra": "1"}), "exact"),
        (lambda value: value.pop("PESSIMISTIC"), "exactly BASE"),
        (lambda value: value["BASE"].update({"spread_bps_per_side": 2.0}), "not a float"),
        (lambda value: value["PESSIMISTIC"].update({"commission_bps_per_side": "10000"}), "below 100%"),
    ],
)
def test_weak_inexact_or_malformed_cost_policies_are_rejected(
    tmp_path, mutation, fragment
):
    ledger = ProviderPaperExecutionStressPolicyLedger(tmp_path / "policy.jsonl")
    scenarios = {name: dict(values) for name, values in SCENARIOS.items()}
    mutation(scenarios)
    with pytest.raises(ValueError, match=fragment):
        register(ledger, scenarios=scenarios)
    assert ledger.records() == []


def test_policy_requires_human_lead_time_and_valid_account(tmp_path):
    ledger = ProviderPaperExecutionStressPolicyLedger(tmp_path / "policy.jsonl")
    with pytest.raises(ValueError, match="human"):
        register(ledger, human_decision_confirmed=False)
    with pytest.raises(ValueError, match="five minutes"):
        register(ledger, effective_not_before=moment(-400))
    with pytest.raises(ValueError, match="SHA-256"):
        register(ledger, account_reference_sha256="not-a-hash")


def test_policy_concurrent_retry_is_idempotent(tmp_path):
    ledger = ProviderPaperExecutionStressPolicyLedger(tmp_path / "policy.jsonl")
    values = policy_values()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: ledger.preregister(**values), range(2)))
    assert first == second
    assert len(ledger.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "ACTIVE"},
        {"broker": "OTHER"},
        {"scenarios": {}},
        {"denied_actions": []},
        {"policy_active": True},
        {"broker_access_enabled": True},
        {"paper_order_submission_enabled": True},
        {"execution_costs_enforced": True},
        {"order_route_exists": True},
        {"live_trading_enabled": True},
        {"unexpected": True},
    ],
)
def test_rehashed_policy_semantic_tampering_is_detected(tmp_path, changes):
    from core.broker import provider_paper_execution_stress_policy as module

    ledger = ProviderPaperExecutionStressPolicyLedger(tmp_path / "policy.jsonl")
    register(ledger)
    rewrite(ledger.path, module, **changes)
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_buy_uses_worse_price_and_configured_fee_without_permission(tmp_path):
    values = build(tmp_path)
    result = calculate(values)
    assert result["scenario"] == "PESSIMISTIC"
    assert result["pessimistic_adverse_price_bps"] == "30"
    assert result["configured_commission_bps"] == "2"
    assert result["reference_notional_usd"] == "500"
    assert result["stressed_execution_price_usd"] == "100.3"
    assert result["stressed_gross_notional_usd"] == "501.5"
    assert result["configured_commission_fee_usd"] == "0.1003"
    assert result["estimated_cash_debit_usd"] == "501.6003"
    assert result["estimated_cash_credit_usd"] == "0"
    assert result["conservative_risk_notional_usd"] == "501.6003"
    assert result["execution_price_stress_applied"] is True
    assert result["fees_included"] is True
    assert result["all_applicable_fees_complete"] is False
    assert result["previous_hash"] == GENESIS_HASH
    for field in (
        "risk_policy_assessed",
        "risk_limits_enforced",
        "position_quantity_assessed",
        "cash_sufficiency_assessed",
        "broker_access_enabled",
        "order_route_exists",
        "paper_order_submission_allowed",
        "live_order_submission_allowed",
        "human_review_eligible",
        "recommendation_provided",
        "live_trading_enabled",
    ):
        assert result[field] is False


def test_sell_uses_worse_price_net_credit_and_non_reducing_risk_notional(tmp_path):
    values = build(tmp_path, side="SELL")
    result = calculate(values)
    assert result["stressed_execution_price_usd"] == "99.7"
    assert result["stressed_gross_notional_usd"] == "498.5"
    assert result["configured_commission_fee_usd"] == "0.0997"
    assert result["estimated_cash_debit_usd"] == "0"
    assert result["estimated_cash_credit_usd"] == "498.4003"
    assert result["conservative_risk_notional_usd"] == "500.0997"


@pytest.mark.parametrize(
    "build_changes,calculate_changes,fragment",
    [
        ({"proposal_changes": {"portfolio_version": "wrong"}}, {}, "portfolio"),
        ({"proposal_changes": {"strategy_version": "wrong"}}, {}, "strategy"),
        (
            {"proposal_changes": {"created_at": moment(-400)}},
            {},
            "predate",
        ),
        ({}, {"proposal_record_hash": "0" * 64}, "hash"),
        ({}, {"execution_stress_policy_record_hash": "0" * 64}, "hash"),
        ({}, {"assessed_at": moment(-70)}, "follow"),
    ],
)
def test_identity_policy_and_time_mismatches_fail_closed(
    tmp_path, build_changes, calculate_changes, fragment
):
    values = build(tmp_path, **build_changes)
    with pytest.raises(ValueError, match=fragment):
        calculate(values, **calculate_changes)


def test_stale_and_future_recording_times_are_rejected(tmp_path):
    values = build(tmp_path)
    with pytest.raises(ValueError, match="within five minutes"):
        calculate(values, recorded_at=moment(-600))
    with pytest.raises(ValueError, match="future"):
        calculate(values, recorded_at="2099-01-01T00:00:00+00:00")


def test_evidence_concurrent_retry_is_idempotent(tmp_path):
    values = build(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: calculate(values), range(2)))
    assert first == second
    assert len(values["evidence_ledger"].verify()) == 1


def test_evidence_chain_requires_forward_assessment_time(tmp_path):
    values = build(tmp_path)
    first = calculate(values)
    second = calculate(values, assessed_at=moment(-20), recorded_at=moment(-4))
    assert second["previous_hash"] == first["record_hash"]
    assert len(values["evidence_ledger"].verify()) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"scenario": "BASE"},
        {"stressed_execution_price_usd": "1"},
        {"configured_commission_fee_usd": "0"},
        {"execution_price_stress_applied": False},
        {"fees_included": False},
        {"all_applicable_fees_complete": True},
        {"risk_limits_enforced": True},
        {"paper_order_submission_allowed": True},
        {"live_trading_enabled": True},
        {"unexpected": True},
    ],
)
def test_rehashed_evidence_semantic_tampering_is_detected(tmp_path, changes):
    from core.broker import provider_paper_execution_stress_evidence as module

    values = build(tmp_path)
    calculate(values)
    rewrite(values["evidence_ledger"].path, module, **changes)
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        values["evidence_ledger"].verify()
