from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration import SandboxExperimentRunManifestLedger


class Stub:
    def __init__(self, values): self.values = list(values)
    def verify(self): return self.values


EXPERIMENT = {
    "experiment_id": "EXP-1", "record_hash": "hash-experiment",
    "status": "PREREGISTERED_NOT_EXECUTED", "maximum_trials": 5,
    "registered_at": "2022-01-01T00:00:00+00:00",
}
BASE = {
    "experiment_id": "EXP-1", "git_revision": "a" * 40,
    "dataset_manifest_sha256": "b" * 64, "dependency_lock_sha256": "c" * 64,
    "runner_sha256": "d" * 64, "execution_environment": "LOCAL_ISOLATED_NO_NETWORK",
    "maximum_runtime_seconds": 3600, "maximum_memory_mb": 4096,
    "maximum_cpu_cores": 2, "planned_trial_count": 5,
    "planned_by": "Codex", "planned_at": "2022-01-02T00:00:00+00:00",
}


def ledger(tmp_path):
    return SandboxExperimentRunManifestLedger(tmp_path / "runs.jsonl", Stub([dict(EXPERIMENT)]))


def plan(target, **changes):
    values = dict(BASE); values.update(changes); return target.plan(**values)


def rewrite(path, **changes):
    from core.orchestration import experiment_run_manifest as module
    record = json.loads(path.read_text()); record.update(changes)
    material = {k: v for k, v in record.items() if k != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_plans_reproducible_inert_run(tmp_path):
    target = ledger(tmp_path); result = plan(target)
    assert result["status"] == "PLANNED_NOT_EXECUTED"
    assert result["experiment_record_hash"] == "hash-experiment"
    assert result["previous_hash"] == GENESIS_HASH
    for field in (
        "network_allowed", "provider_api_allowed", "broker_connection_allowed",
        "experiment_executed", "result_recorded", "shadow_test_started",
        "promotion_approved", "production_rule_changed", "deployment_performed",
        "order_submitted", "live_trading_enabled",
    ): assert result[field] is False
    assert target.verify() == [result]


@pytest.mark.parametrize("field,value,fragment", [
    ("experiment_id", "unknown", "verified"),
    ("git_revision", "abc", "full hexadecimal"),
    ("dataset_manifest_sha256", "abc", "SHA-256"),
    ("execution_environment", "AWS_WITH_NETWORK", "isolated"),
    ("planned_at", "2021-12-31T23:59:59+00:00", "predate"),
    ("maximum_runtime_seconds", 0, "between"),
    ("maximum_memory_mb", 65537, "between"),
    ("maximum_cpu_cores", 65, "between"),
    ("planned_trial_count", 6, "exceeds"),
])
def test_invalid_plan_fails_closed(tmp_path, field, value, fragment):
    with pytest.raises(ValueError, match=fragment): plan(ledger(tmp_path), **{field: value})


def test_changed_or_missing_experiment_fails_closed(tmp_path):
    target = ledger(tmp_path); plan(target)
    target.experiment_ledger.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="lost its experiment"): target.verify()


def test_concurrent_retry_appends_once(tmp_path):
    target = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: plan(target), range(2)))
    assert first == second and len(target.verify()) == 1


@pytest.mark.parametrize("changes", [
    {"git_revision": "e" * 40}, {"planned_trial_count": 4},
    {"execution_environment": "CONTAINER_ISOLATED_NO_NETWORK"},
    {"network_allowed": True}, {"provider_api_allowed": True},
    {"broker_connection_allowed": True}, {"experiment_executed": True},
    {"result_recorded": True}, {"promotion_approved": True},
    {"deployment_performed": True}, {"order_submitted": True},
    {"live_trading_enabled": True},
])
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    target = ledger(tmp_path); plan(target); rewrite(target.path, **changes)
    with pytest.raises(LedgerIntegrityError): target.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    target = ledger(tmp_path); plan(target)
    with target.path.open("ab") as output: output.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"): target.verify()
    backup = target.repair_incomplete_tail()
    assert backup and backup.read_bytes() == b'{"partial"'
    assert len(target.verify()) == 1
