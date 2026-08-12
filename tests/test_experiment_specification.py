from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.orchestration import SandboxExperimentSpecificationLedger


class Stub:
    def __init__(self, values):
        self.values = list(values)

    def verify(self):
        return self.values


LESSON = {
    "lesson_id": "LESSON-1",
    "record_hash": "hash-lesson",
    "status": "SANDBOX_PROPOSAL_UNAPPROVED",
}

BASE = {
    "lesson_id": "LESSON-1",
    "experiment_name": "Lower growth assumption experiment",
    "baseline_strategy_version": "strategy-v1",
    "candidate_strategy_version": "strategy-v1-exp-1",
    "candidate_change_description": "Reduce the long-term growth assumption by two percentage points.",
    "point_in_time_data_cutoff": "2021-12-31T23:59:59+00:00",
    "out_of_sample_not_before": "2022-02-01T00:00:00+00:00",
    "out_of_sample_not_after": "2022-06-30T00:00:00+00:00",
    "shadow_not_before": "2022-07-01T00:00:00+00:00",
    "primary_metric": "MEAN_ABSOLUTE_PREDICTION_ERROR",
    "minimum_primary_improvement": "0.02",
    "maximum_drawdown_degradation": "0.01",
    "maximum_turnover_increase": "0.05",
    "maximum_trials": 5,
    "random_seed": 42,
    "acceptance_rule": "Primary metric improves by at least 0.02 and both safety constraints pass.",
    "rejection_rule": "Reject if improvement is smaller or either safety constraint fails.",
    "registered_by": "Sam",
    "registered_at": "2022-01-01T00:00:00+00:00",
}


def ledgers(tmp_path):
    lessons = Stub([dict(LESSON)])
    ledger = SandboxExperimentSpecificationLedger(tmp_path / "experiments.jsonl", lessons)
    return lessons, ledger


def register(ledger, **overrides):
    values = dict(BASE)
    values.update(overrides)
    return ledger.preregister(**values)


def rewrite(path, **changes):
    from core.orchestration import experiment_specification as module
    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_preregisters_point_in_time_experiment_without_execution(tmp_path):
    _, ledger = ledgers(tmp_path)
    result = register(ledger)
    assert result["status"] == "PREREGISTERED_NOT_EXECUTED"
    assert result["lesson_record_hash"] == "hash-lesson"
    assert result["primary_metric"] == "MEAN_ABSOLUTE_PREDICTION_ERROR"
    assert result["maximum_trials"] == 5
    assert result["random_seed"] == 42
    assert result["point_in_time_only"] is True
    for field in (
        "random_split_allowed", "out_of_sample_reuse_allowed", "metric_substitution_allowed",
        "optional_stopping_allowed", "experiment_executed", "result_recorded",
        "shadow_test_started", "promotion_approved", "production_rule_changed",
        "code_changed", "deployment_performed", "order_submitted", "live_trading_enabled",
    ):
        assert result[field] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert ledger.verify() == [result]


def test_missing_or_changed_lesson_fails_closed(tmp_path):
    lessons, ledger = ledgers(tmp_path)
    with pytest.raises(ValueError, match="verified"):
        register(ledger, lesson_id="UNKNOWN")
    register(ledger)
    lessons.values[0]["record_hash"] = "changed"
    with pytest.raises(LedgerIntegrityError, match="lost its lesson"):
        ledger.verify()


@pytest.mark.parametrize(
    "field,value,fragment",
    [
        ("candidate_strategy_version", "strategy-v1", "differ"),
        ("point_in_time_data_cutoff", "2022-01-02T00:00:00+00:00", "postdate"),
        ("out_of_sample_not_before", "2021-12-01T00:00:00+00:00", "begin after"),
        ("out_of_sample_not_after", "2022-02-20T00:00:00+00:00", "30 days"),
        ("shadow_not_before", "2022-06-30T00:00:00+00:00", "after"),
        ("primary_metric", "WHICHEVER_WINS", "not supported"),
        ("minimum_primary_improvement", -1, "non-negative"),
        ("maximum_trials", 0, "between"),
        ("maximum_trials", 101, "between"),
        ("random_seed", 0, "between"),
    ],
)
def test_invalid_preregistration_fails_closed(tmp_path, field, value, fragment):
    _, ledger = ledgers(tmp_path)
    with pytest.raises(ValueError, match=fragment):
        register(ledger, **{field: value})


def test_identical_concurrent_retries_append_once(tmp_path):
    _, ledger = ledgers(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: register(ledger), range(2)))
    assert first == second
    assert len(ledger.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"primary_metric": "HIT_RATE"},
        {"minimum_primary_improvement": "0"},
        {"maximum_trials": 100},
        {"random_seed": 7},
        {"point_in_time_only": False},
        {"random_split_allowed": True},
        {"out_of_sample_reuse_allowed": True},
        {"metric_substitution_allowed": True},
        {"optional_stopping_allowed": True},
        {"experiment_executed": True},
        {"result_recorded": True},
        {"shadow_test_started": True},
        {"promotion_approved": True},
        {"production_rule_changed": True},
        {"code_changed": True},
        {"deployment_performed": True},
        {"order_submitted": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    _, ledger = ledgers(tmp_path)
    register(ledger)
    rewrite(ledger.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    _, ledger = ledgers(tmp_path)
    register(ledger)
    with ledger.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert len(ledger.verify()) == 1
