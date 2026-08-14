from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.broker.provider_paper_kill_switch import ProviderPaperKillSwitchLedger
from core.broker.provider_paper_risk_policy import ProviderPaperRiskControlPolicyLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


POLICY_BASE = {
    "account_reference_sha256": "11" * 32,
    "portfolio_version": "port-v1",
    "strategy_version": "strat-v1",
    "max_order_notional_usd": "1000",
    "max_position_notional_usd": "5000",
    "max_gross_exposure_usd": "20000",
    "max_daily_loss_usd": "750",
    "max_account_snapshot_age_seconds": 120,
    "max_risk_snapshot_age_seconds": 60,
    "kill_switch_identifier": "switch-v1",
    "decided_by": "Sam",
    "decision_reference": "synthetic-test-policy-1",
    "human_decision_confirmed": True,
    "effective_not_before": "2026-01-01T00:10:00+00:00",
    "git_revision": "abc123",
    "recorded_at": "2026-01-01T00:00:00+00:00",
}
TRIGGER_BASE = {
    "kill_switch_identifier": "switch-v1",
    "trigger_source": "HUMAN",
    "reason": "Risk monitor threshold breached.",
    "triggered_by": "Sam",
    "triggered_at": "2026-01-01T01:00:00+00:00",
}


def preregister(policy_ledger, **overrides):
    values = dict(POLICY_BASE)
    values.update(overrides)
    return policy_ledger.preregister(**values)


def ledgers(tmp_path):
    policies = ProviderPaperRiskControlPolicyLedger(tmp_path / "risk_policy.jsonl")
    policy = preregister(policies)
    stops = ProviderPaperKillSwitchLedger(tmp_path / "kill_switch.jsonl", policies)
    return policies, policy, stops


def trigger(target, policy_id, **changes):
    values = dict(TRIGGER_BASE)
    values.update(changes)
    return target.trigger(policy_id=policy_id, **values)


def rewrite(path, **changes):
    from core.broker import provider_paper_kill_switch as module

    value = json.loads(path.read_text())
    value.update(changes)
    material = {key: val for key, val in value.items() if key != "record_hash"}
    value["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(value) + "\n")


def test_unknown_or_inactive_policy_is_stopped_by_default(tmp_path):
    _, policy, target = ledgers(tmp_path)
    assert target.runtime_state("unknown") == {
        "state": "STOPPED", "work_allowed": False, "reason": "UNKNOWN_POLICY",
    }
    assert target.runtime_state(policy["policy_id"]) == {
        "state": "STOPPED", "work_allowed": False, "reason": "POLICY_INACTIVE",
    }


def test_trigger_latches_all_work_off_and_pins_real_policy(tmp_path):
    _, policy, target = ledgers(tmp_path)
    result = trigger(target, policy["policy_id"])
    assert result["status"] == "STOPPED_LATCHED"
    assert result["trading_halted"] is True
    assert result["policy_record_hash"] == policy["record_hash"]
    assert result["account_reference_sha256"] == policy["account_reference_sha256"]
    assert result["previous_hash"] == GENESIS_HASH
    for field in (
        "paper_order_proposal_allowed", "paper_order_submission_allowed",
        "order_cancel_replace_allowed", "broker_access_allowed",
        "automatic_resume_allowed", "self_resume_allowed", "risk_limits_enforced",
        "order_route_exists", "external_head_anchor_present",
        "cryptographic_authentication_present", "live_trading_enabled",
    ):
        assert result[field] is False
    assert target.runtime_state(policy["policy_id"])["state"] == "STOPPED_LATCHED"
    assert target.verify() == [result]


def test_latch_follows_replacement_policy_for_same_account(tmp_path):
    policies, first, target = ledgers(tmp_path)
    second = preregister(
        policies,
        strategy_version="strat-v2",
        decision_reference="synthetic-test-policy-2",
        effective_not_before="2026-01-01T00:20:00+00:00",
    )
    trigger(target, first["policy_id"])
    state = target.runtime_state(second["policy_id"])
    assert state["state"] == "STOPPED_LATCHED"
    with pytest.raises(LedgerIntegrityError, match="already stopped"):
        trigger(
            target,
            second["policy_id"],
            triggered_at="2026-01-01T02:00:00+00:00",
        )


@pytest.mark.parametrize(
    "field,value,fragment",
    [
        ("policy_id", "unknown", "verified"),
        ("kill_switch_identifier", "wrong-switch", "match"),
        ("trigger_source", "AGENT", "not permitted"),
        ("reason", "", "required"),
        ("triggered_at", "2025-01-01T00:00:00+00:00", "predate"),
    ],
)
def test_invalid_trigger_fails_closed(tmp_path, field, value, fragment):
    _, policy, target = ledgers(tmp_path)
    policy_id = policy["policy_id"]
    changes = {field: value}
    if field == "policy_id":
        policy_id = value
        changes = {}
    with pytest.raises(ValueError, match=fragment):
        trigger(target, policy_id, **changes)


def test_different_second_trigger_is_rejected(tmp_path):
    _, policy, target = ledgers(tmp_path)
    trigger(target, policy["policy_id"])
    with pytest.raises(LedgerIntegrityError, match="already stopped"):
        trigger(
            target,
            policy["policy_id"],
            reason="A distinct trigger reason.",
            triggered_at="2026-01-01T02:00:00+00:00",
        )


def test_concurrent_retry_without_fixed_time_appends_once(tmp_path):
    _, policy, target = ledgers(tmp_path)
    values = {key: value for key, value in TRIGGER_BASE.items() if key != "triggered_at"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(
            pool.map(
                lambda _: target.trigger(policy_id=policy["policy_id"], **values),
                range(2),
            )
        )
    assert first == second
    assert len(target.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "RUNNING"},
        {"trading_halted": False},
        {"account_reference_sha256": "22" * 32},
        {"triggered_by": "Mallory"},
        {"paper_order_proposal_allowed": True},
        {"paper_order_submission_allowed": True},
        {"order_cancel_replace_allowed": True},
        {"broker_access_allowed": True},
        {"automatic_resume_allowed": True},
        {"self_resume_allowed": True},
        {"risk_limits_enforced": True},
        {"order_route_exists": True},
        {"external_head_anchor_present": True},
        {"cryptographic_authentication_present": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    _, policy, target = ledgers(tmp_path)
    trigger(target, policy["policy_id"])
    rewrite(target.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_rehashed_unexpected_field_is_rejected(tmp_path):
    _, policy, target = ledgers(tmp_path)
    trigger(target, policy["policy_id"])
    rewrite(target.path, unexpected_permission=True)
    with pytest.raises(LedgerIntegrityError):
        target.verify()
