from datetime import datetime, timedelta, timezone

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration.replay_execution_policy_ledger import ReplayExecutionPolicyLedger


SCENARIOS = {
    "BASE": {
        "commission_bps_per_side": "1", "spread_bps_per_side": "2",
        "slippage_bps_per_side": "10", "latency_bps_per_side": "1",
        "market_impact_bps_per_side": "2", "maximum_volume_participation_rate": "0.05",
        "maximum_order_age_minutes": "30",
    },
    "PESSIMISTIC": {
        "commission_bps_per_side": "2", "spread_bps_per_side": "4",
        "slippage_bps_per_side": "20", "latency_bps_per_side": "2",
        "market_impact_bps_per_side": "4", "maximum_volume_participation_rate": "0.02",
        "maximum_order_age_minutes": "60",
    },
}


class VerifiedLedger:
    def __init__(self, records):
        self._records = records

    def verify(self):
        return self._records


def plan(access=None):
    return {
        "replay_plan_id": "REPLAY-1", "record_hash": "a" * 64,
        "evaluation_data_access_not_before": access or (
            datetime.now(timezone.utc) + timedelta(days=1)
        ).isoformat(),
        "cost_model": {
            "commission_bps_per_side": "1.0",
            "bid_ask_half_spread_bps_per_side": "2.00",
            "slippage_bps_per_side": "10", "latency_bps_per_side": "1",
            "market_impact_bps_per_side": "2", "pessimistic_cost_multiplier": "2.0",
        },
    }


def ledger(tmp_path, source_plan=None):
    return ReplayExecutionPolicyLedger(
        tmp_path / "execution-policy.jsonl", VerifiedLedger([source_plan or plan()])
    )


def preregister(target, **changes):
    values = {
        "replay_plan_id": "REPLAY-1", "scenarios": SCENARIOS, "defined_by": "Codex",
    }
    values.update(changes)
    return target.preregister(**values)


def test_preregisters_exact_plan_aligned_policy_without_execution(tmp_path):
    target = ledger(tmp_path)
    record = preregister(target)
    assert record["previous_hash"] == GENESIS_HASH
    assert record["replay_plan_record_hash"] == "a" * 64
    assert record["simulation_only"] is True
    for field in (
        "evaluation_dataset_opened", "evaluation_observations_interpreted", "costs_calculated",
        "replay_executed", "fills_generated", "position_mutated", "cash_mutated",
        "performance_calculated", "performance_claim_allowed", "model_trained",
        "learning_applied", "network_allowed", "paper_broker_submission_enabled",
        "broker_connection_allowed", "orders_submitted", "live_trading_enabled",
    ):
        assert record[field] is False
    assert target.verify() == [record]


def test_decimal_formatting_does_not_break_plan_alignment(tmp_path):
    record = preregister(ledger(tmp_path))
    assert record["scenarios"]["BASE"]["spread_bps_per_side"] == "2"


@pytest.mark.parametrize("scenario,field,value,fragment", [
    ("BASE", "spread_bps_per_side", "1", "must match"),
    ("PESSIMISTIC", "spread_bps_per_side", "5", "plan multiplier"),
])
def test_costs_must_match_preregistered_plan(tmp_path, scenario, field, value, fragment):
    scenarios = {name: dict(item) for name, item in SCENARIOS.items()}
    scenarios[scenario][field] = value
    with pytest.raises(ValueError, match=fragment):
        preregister(ledger(tmp_path), scenarios=scenarios)


def test_policy_cannot_be_defined_after_sealed_data_access(tmp_path):
    access = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with pytest.raises(ValueError, match="before sealed"):
        preregister(ledger(tmp_path, plan(access)))


def test_policy_cannot_be_defined_at_exact_data_access_boundary(tmp_path):
    boundary = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="before sealed"):
        preregister(
            ledger(tmp_path, plan(boundary.isoformat())),
            defined_at=boundary.isoformat(),
        )


def test_unknown_replay_plan_is_rejected(tmp_path):
    target = ReplayExecutionPolicyLedger(
        tmp_path / "policy.jsonl", VerifiedLedger([])
    )
    with pytest.raises(ValueError, match="verified replay plan"):
        preregister(target)


def test_policy_cannot_be_backdated(tmp_path):
    with pytest.raises(ValueError, match="actual append time"):
        preregister(
            ledger(tmp_path),
            defined_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )


def test_identical_retry_is_idempotent_but_conflict_fails(tmp_path):
    target = ledger(tmp_path)
    first = preregister(target)
    assert preregister(target) == first
    changed = {name: dict(item) for name, item in SCENARIOS.items()}
    changed["BASE"]["maximum_order_age_minutes"] = "31"
    with pytest.raises(LedgerIntegrityError, match="single execution policy"):
        preregister(target, scenarios=changed)


def test_verify_rechecks_plan_hash_and_data_access_boundary(tmp_path):
    source_plan = plan()
    plans = VerifiedLedger([source_plan])
    target = ReplayExecutionPolicyLedger(tmp_path / "policy.jsonl", plans)
    preregister(target)
    source_plan["record_hash"] = "b" * 64
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_tampering_is_detected(tmp_path):
    target = ledger(tmp_path)
    preregister(target)
    text = target.path.read_text()
    target.path.write_text(text.replace('"orders_submitted":false', '"orders_submitted":true'))
    with pytest.raises(LedgerIntegrityError, match="modified"):
        target.verify()
