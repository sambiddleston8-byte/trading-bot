from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration import SandboxExperimentResultLedger


class Stub:
    def __init__(self, values): self.values = list(values)
    def verify(self): return self.values


EXPERIMENT = {
    "experiment_id": "EXP-1", "record_hash": "experiment-hash",
    "primary_metric": "MEAN_ABSOLUTE_PREDICTION_ERROR",
    "minimum_primary_improvement": "0.02", "maximum_drawdown_degradation": "0.01",
    "maximum_turnover_increase": "0.05", "out_of_sample_not_after": "2022-06-30T00:00:00+00:00",
}
MANIFEST = {
    "run_manifest_id": "RUN-1", "record_hash": "manifest-hash",
    "experiment_id": "EXP-1", "experiment_record_hash": "experiment-hash",
    "planned_at": "2022-01-02T00:00:00+00:00", "planned_trial_count": 5,
}
BASE = {
    "run_manifest_id": "RUN-1", "runner_output_sha256": "a" * 64,
    "trials_completed": 5, "baseline_primary_metric": "0.10",
    "candidate_primary_metric": "0.07", "baseline_maximum_drawdown": "0.10",
    "candidate_maximum_drawdown": "0.105", "baseline_turnover": "0.20",
    "candidate_turnover": "0.24", "completed_by": "runner-v1",
    "completed_at": "2022-07-01T00:00:00+00:00",
}


def ledger(tmp_path, metric=None):
    experiment = dict(EXPERIMENT)
    if metric: experiment["primary_metric"] = metric
    experiments = Stub([experiment])
    manifests = Stub([dict(MANIFEST)])
    manifests.experiment_ledger = experiments
    return SandboxExperimentResultLedger(tmp_path / "results.jsonl", manifests)


def record(target, **changes):
    values = dict(BASE); values.update(changes); return target.record(**values)


def rewrite(path, **changes):
    from core.orchestration import experiment_result as module
    value = json.loads(path.read_text()); value.update(changes)
    material = {k: v for k, v in value.items() if k != "record_hash"}
    value["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(value) + "\n")


def test_mechanically_accepts_lower_is_better_result_without_promotion(tmp_path):
    target = ledger(tmp_path); result = record(target)
    assert result["status"] == "ACCEPTANCE_CRITERIA_MET"
    assert result["primary_improvement"] == "0.03"
    assert result["all_preregistered_criteria_met"] is True
    assert result["previous_hash"] == GENESIS_HASH
    for field in ("shadow_test_started", "promotion_approved", "production_rule_changed", "deployment_performed", "order_submitted", "live_trading_enabled"):
        assert result[field] is False
    assert target.verify() == [result]


def test_higher_is_better_and_safety_rejection_are_mechanical(tmp_path):
    target = ledger(tmp_path, "COMPLETE_BENCHMARK_RELATIVE_RETURN")
    accepted = record(target, baseline_primary_metric="-0.01", candidate_primary_metric="0.02")
    assert accepted["primary_improvement"] == "0.03"
    target = ledger(tmp_path / "rejected")
    rejected = record(target, candidate_maximum_drawdown="0.12")
    assert rejected["status"] == "REJECTION_CRITERIA_MET"
    assert rejected["drawdown_constraint_passed"] is False


@pytest.mark.parametrize("field,value,fragment", [
    ("run_manifest_id", "unknown", "verified"),
    ("runner_output_sha256", "abc", "SHA-256"),
    ("trials_completed", 4, "planned count"),
    ("completed_at", "2021-01-01T00:00:00+00:00", "predate"),
    ("completed_at", "2022-06-01T00:00:00+00:00", "window ends"),
    ("baseline_primary_metric", "nan", "finite"),
    ("candidate_maximum_drawdown", -1, "non-negative"),
    ("candidate_turnover", -1, "non-negative"),
])
def test_invalid_result_fails_closed(tmp_path, field, value, fragment):
    with pytest.raises(ValueError, match=fragment): record(ledger(tmp_path), **{field: value})


def test_changed_parent_fails_closed(tmp_path):
    target = ledger(tmp_path); record(target)
    target.run_manifest_ledger.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="lost its run manifest"): target.verify()


def test_concurrent_retry_appends_once(tmp_path):
    target = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: record(target), range(2)))
    assert first == second and len(target.verify()) == 1


@pytest.mark.parametrize("changes", [
    {"candidate_primary_metric": "0.20"}, {"primary_improvement": "99"},
    {"status": "REJECTION_CRITERIA_MET"}, {"trials_completed": 4},
    {"primary_threshold_passed": False}, {"all_preregistered_criteria_met": False},
    {"random_split_used": True}, {"out_of_sample_reused": True},
    {"metric_substituted": True}, {"optional_stopping_used": True},
    {"promotion_approved": True}, {"production_rule_changed": True},
    {"deployment_performed": True}, {"order_submitted": True},
    {"live_trading_enabled": True},
])
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    target = ledger(tmp_path); record(target); rewrite(target.path, **changes)
    with pytest.raises(LedgerIntegrityError): target.verify()
