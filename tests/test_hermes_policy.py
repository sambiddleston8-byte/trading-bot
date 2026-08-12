from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration import HermesPermissionPolicyLedger


BASE = {
    "effective_not_before": "2026-01-01T00:10:00+00:00",
    "max_scheduled_jobs_per_day": 4,
    "max_job_duration_seconds": 3600,
    "max_llm_calls_per_job": 2,
    "max_subagents_per_job": 2,
    "max_daily_ai_cost_usd": "10",
    "max_consecutive_failures": 3,
    "emergency_stop_identifier": "local-hermes-stop-v1",
    "decided_by": "Sam",
    "decision_reference": "human-hermes-policy-1",
    "human_decision_confirmed": True,
    "model_versions": [{"component": "orchestrator", "version": "1.0"}],
    "git_revision": "abc123",
    "recorded_at": "2026-01-01T00:00:00+00:00",
}


def register(ledger, **overrides):
    values = dict(BASE)
    values.update(overrides)
    return ledger.preregister(**values)


def rewrite(path, **changes):
    from core.orchestration import hermes_policy as module
    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_policy_is_bounded_and_inactive_by_default(tmp_path):
    ledger = HermesPermissionPolicyLedger(tmp_path / "hermes.jsonl")
    result = register(ledger)
    assert result["status"] == "PREREGISTERED_INACTIVE"
    assert result["default_runtime_state"] == "STOPPED"
    assert result["emergency_stop_required"] is True
    assert "WRITE_SANDBOX_EXPERIMENT_PROPOSAL" in result["allowed_actions"]
    assert "MODIFY_PRODUCTION_INVESTMENT_RULES" in result["denied_actions"]
    for field in (
        "scheduler_enabled", "network_access_enabled", "broker_access_enabled",
        "aws_access_enabled", "github_write_enabled", "production_rule_write_enabled",
        "self_activation_allowed", "self_permission_change_allowed",
        "automatic_experiment_promotion_allowed", "model_invocation_performed", "job_scheduled",
    ):
        assert result[field] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert ledger.verify() == [result]


def test_human_confirmation_and_future_lead_required(tmp_path):
    ledger = HermesPermissionPolicyLedger(tmp_path / "hermes.jsonl")
    with pytest.raises(ValueError, match="human"):
        register(ledger, human_decision_confirmed=False)
    with pytest.raises(ValueError, match="five minutes"):
        register(ledger, effective_not_before="2026-01-01T00:04:59+00:00")


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_scheduled_jobs_per_day", 0),
        ("max_scheduled_jobs_per_day", 101),
        ("max_job_duration_seconds", 59),
        ("max_llm_calls_per_job", 101),
        ("max_subagents_per_job", 21),
        ("max_consecutive_failures", 0),
        ("max_daily_ai_cost_usd", -1),
        ("max_daily_ai_cost_usd", 1001),
        ("max_daily_ai_cost_usd", "NaN"),
    ],
)
def test_invalid_resource_budgets_are_rejected(tmp_path, field, value):
    ledger = HermesPermissionPolicyLedger(tmp_path / "hermes.jsonl")
    with pytest.raises(ValueError):
        register(ledger, **{field: value})


def test_concurrent_retry_is_idempotent(tmp_path):
    ledger = HermesPermissionPolicyLedger(tmp_path / "hermes.jsonl")
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: register(ledger), range(2)))
    assert first == second
    assert len(ledger.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"allowed_actions": ["MODIFY_PRODUCTION_INVESTMENT_RULES"]},
        {"denied_actions": []},
        {"max_daily_ai_cost_usd": "999"},
        {"emergency_stop_required": False},
        {"default_runtime_state": "RUNNING"},
        {"scheduler_enabled": True},
        {"network_access_enabled": True},
        {"broker_access_enabled": True},
        {"aws_access_enabled": True},
        {"github_write_enabled": True},
        {"production_rule_write_enabled": True},
        {"self_activation_allowed": True},
        {"automatic_experiment_promotion_allowed": True},
        {"model_invocation_performed": True},
        {"job_scheduled": True},
    ],
)
def test_rehashed_permission_tampering_is_detected(tmp_path, changes):
    ledger = HermesPermissionPolicyLedger(tmp_path / "hermes.jsonl")
    register(ledger)
    rewrite(ledger.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    ledger = HermesPermissionPolicyLedger(tmp_path / "hermes.jsonl")
    register(ledger)
    with ledger.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert len(ledger.verify()) == 1
