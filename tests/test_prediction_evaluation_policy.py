from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.performance import PredictionEvaluationPolicyLedger


BASE = {
    "portfolio_version": "PORT-001",
    "success_rule": "POSITIVE_ABSOLUTE_RETURN",
    "confidence_bucket_edges": [0, 20, 40, 60, 80, 100],
    "expected_return_split_points": [0, 0.1, 0.2, 0.3, 0.5],
    "evaluation_not_before": "2026-08-12T20:10:00+00:00",
    "decided_by": "Sam",
    "decision_reference": "human-choice-1",
    "human_decision_confirmed": True,
    "strategy_version": "strategy-v1",
    "model_versions": [{"component": "research", "version": "1.0"}],
    "git_revision": "abc123",
    "recorded_at": "2026-08-12T20:00:00+00:00",
}


def register(ledger, **overrides):
    values = dict(BASE)
    values.update(overrides)
    return ledger.preregister(**values)


def rewrite_with_valid_hash(path, **changes):
    from core.performance import prediction_evaluation_policy as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_preregisters_fixed_future_cohort_policy(tmp_path):
    ledger = PredictionEvaluationPolicyLedger(tmp_path / "policy.jsonl")
    result = register(ledger)
    assert result["status"] == "PREREGISTERED"
    assert result["success_formula"] == "actual_total_return > 0"
    assert result["confidence_semantics"] == "RANKING_SCORE_0_TO_100_NOT_EVENT_PROBABILITY"
    assert result["confidence_bucket_edges"] == ["0", "20", "40", "60", "80", "100"]
    assert result["expected_return_split_points"] == ["0", "0.1", "0.2", "0.3", "0.5"]
    assert result["fixed_horizon_cohorts_only"] is True
    assert result["cross_horizon_pooling_allowed"] is False
    assert result["cross_model_version_pooling_allowed"] is False
    assert result["complete_eligible_cohort_required"] is True
    assert result["discretionary_outcome_exclusion_allowed"] is False
    assert result["complete_round_trip_cost_evidence_required"] is True
    assert result["entry_only_outcomes_eligible"] is False
    assert result["minimum_total_observations"] == 100
    assert result["minimum_observations_per_reported_cohort"] == 30
    assert result["probability_calibration_claim_allowed"] is False
    assert result["policy_selection_precedes_every_eligible_decision"] is True
    assert result["outcome_knowledge_allowed_at_policy_selection"] is False
    assert result["hit_rate_calculated"] is False
    assert result["calibration_calculated"] is False
    assert result["learning_eligible"] is False
    assert result["live_trading_enabled"] is False
    assert result["previous_hash"] == GENESIS_HASH
    assert ledger.verify() == [result]


@pytest.mark.parametrize(
    "rule,formula,benchmark_required",
    [
        ("POSITIVE_ABSOLUTE_RETURN", "actual_total_return > 0", False),
        (
            "MEETS_OR_EXCEEDS_PREDICTED_RETURN",
            "actual_total_return >= predicted_expected_return",
            False,
        ),
        (
            "POSITIVE_BENCHMARK_RELATIVE_RETURN",
            "actual_total_return - matched_benchmark_total_return > 0",
            True,
        ),
    ],
)
def test_supported_success_rules_are_explicit(tmp_path, rule, formula, benchmark_required):
    ledger = PredictionEvaluationPolicyLedger(tmp_path / f"{rule}.jsonl")
    result = register(ledger, success_rule=rule)
    assert result["success_formula"] == formula
    assert result["benchmark_pairing_required"] is benchmark_required


def test_human_confirmation_and_future_lead_time_are_required(tmp_path):
    ledger = PredictionEvaluationPolicyLedger(tmp_path / "policy.jsonl")
    with pytest.raises(ValueError, match="human"):
        register(ledger, human_decision_confirmed=False)
    with pytest.raises(ValueError, match="five minutes"):
        register(ledger, evaluation_not_before="2026-08-12T20:04:59+00:00")


@pytest.mark.parametrize(
    "edges",
    [
        [],
        [0],
        [1, 100],
        [0, 99],
        [0, 50, 50, 100],
        [0, 80, 20, 100],
        [0, float("nan"), 100],
    ],
)
def test_invalid_confidence_buckets_are_rejected(tmp_path, edges):
    ledger = PredictionEvaluationPolicyLedger(tmp_path / "policy.jsonl")
    with pytest.raises(ValueError):
        register(ledger, confidence_bucket_edges=edges)


@pytest.mark.parametrize(
    "splits",
    [[], [-1], [-2, 0], [0.2, 0.1], [0.1, 0.1], [float("inf")]],
)
def test_invalid_expected_return_splits_are_rejected(tmp_path, splits):
    ledger = PredictionEvaluationPolicyLedger(tmp_path / "policy.jsonl")
    with pytest.raises(ValueError):
        register(ledger, expected_return_split_points=splits)


def test_unsupported_success_rule_is_rejected(tmp_path):
    ledger = PredictionEvaluationPolicyLedger(tmp_path / "policy.jsonl")
    with pytest.raises(ValueError, match="not supported"):
        register(ledger, success_rule="WHATEVER_LOOKS_BEST_LATER")


def test_same_future_window_cannot_be_redefined(tmp_path):
    ledger = PredictionEvaluationPolicyLedger(tmp_path / "policy.jsonl")
    register(ledger)
    with pytest.raises(LedgerIntegrityError, match="already preregistered"):
        register(ledger, success_rule="MEETS_OR_EXCEEDS_PREDICTED_RETURN")


def test_identical_retry_is_idempotent_and_concurrent(tmp_path):
    ledger = PredictionEvaluationPolicyLedger(tmp_path / "policy.jsonl")
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: register(ledger), range(2)))
    assert first == second
    assert len(ledger.verify()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"success_rule": "MEETS_OR_EXCEEDS_PREDICTED_RETURN"},
        {"confidence_bucket_edges": ["0", "100"]},
        {"minimum_total_observations": 1},
        {"cross_horizon_pooling_allowed": True},
        {"cross_model_version_pooling_allowed": True},
        {"complete_eligible_cohort_required": False},
        {"discretionary_outcome_exclusion_allowed": True},
        {"complete_round_trip_cost_evidence_required": False},
        {"entry_only_outcomes_eligible": True},
        {"probability_calibration_claim_allowed": True},
        {"retrospective_application_allowed": True},
        {"policy_selection_precedes_every_eligible_decision": False},
        {"outcome_knowledge_allowed_at_policy_selection": True},
        {"hit_rate_calculated": True},
        {"calibration_calculated": True},
        {"learning_eligible": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_semantic_tampering_is_detected(tmp_path, changes):
    ledger = PredictionEvaluationPolicyLedger(tmp_path / "policy.jsonl")
    register(ledger)
    rewrite_with_valid_hash(ledger.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        ledger.verify()


def test_incomplete_tail_requires_explicit_repair(tmp_path):
    ledger = PredictionEvaluationPolicyLedger(tmp_path / "policy.jsonl")
    register(ledger)
    with ledger.path.open("ab") as target:
        target.write(b'{"partial"')
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        ledger.verify()
    backup = ledger.repair_incomplete_tail()
    assert backup is not None
    assert backup.read_bytes() == b'{"partial"'
    assert len(ledger.verify()) == 1
