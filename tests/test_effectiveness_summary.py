from fractions import Fraction

import pytest

from core.performance import EffectivenessSummaryEngine


class Stub:
    def __init__(self, values):
        self.values = values

    def verify(self):
        return self.values


def outcome(index, numerator=1, denominator=100):
    return {
        "result_id": f"CRRET-{index}",
        "record_hash": f"outcome-hash-{index}",
        "exact_fractions": {
            "complete_benchmark_relative_return": {
                "numerator": str(numerator),
                "denominator": str(denominator),
            }
        },
    }


def assignment(index, *, eligible=True, signal="QUALITY"):
    labels = {
        name: {"label": label}
        for name, label in {
            "market_regime": "BULL",
            "sector": "TECHNOLOGY",
            "company_size": "LARGE_CAP",
            "valuation_regime": "FAIR_VALUE",
            "volatility_regime": "NORMAL_VOLATILITY",
            "signal": signal,
        }.items()
    }
    return {
        "cohort_assignment_id": f"ECOHORT-{index:03d}",
        "record_hash": f"assignment-hash-{index}",
        "relative_return_result_id": f"CRRET-{index}",
        "relative_return_record_hash": f"outcome-hash-{index}",
        "effectiveness_claim_eligible": eligible,
        "investment_horizon": "1_MONTH",
        "strategy_version": "strategy-v1",
        "model_versions": [
            {"component": "research", "version": "2.0"},
            {"component": "valuation", "version": "1.0"},
        ],
        "labels": labels,
    }


def engine(assignments, outcomes):
    relative = Stub(outcomes)
    cohorts = Stub(assignments)
    cohorts.relative_return_ledger = relative
    return EffectivenessSummaryEngine(cohorts)


def test_minimum_sample_produces_descriptive_mean_without_action_claims():
    assignments = [assignment(index) for index in range(30)]
    outcomes = [outcome(index, 1 if index < 15 else -1, 100) for index in range(30)]
    result = engine(assignments, outcomes).summarize(
        dimension="signal", label="QUALITY"
    )

    assert result["status"] == "DESCRIPTIVE_SAMPLE_AVAILABLE"
    assert result["sample_size"] == 30
    assert result["minimum_sample_met"] is True
    assert result["complete_benchmark_relative_return_mean"] == "0"
    assert result["exact_fraction"] == {"numerator": "0", "denominator": "1"}
    assert len(result["source_assignments"]) == 30
    assert result["effectiveness_calculated"] is True
    assert result["descriptive_only"] is True
    assert result["independence_established"] is False
    assert result["statistical_significance_tested"] is False
    assert result["causal_effectiveness_claim"] is False
    assert result["recommendation_provided"] is False
    assert result["learning_eligible"] is False
    assert result["promotion_eligible"] is False
    assert result["order_submission_enabled"] is False
    assert result["live_trading_enabled"] is False


def test_small_sample_remains_insufficient_evidence():
    result = engine([assignment(1)], [outcome(1)]).summarize(
        dimension="sector", label="TECHNOLOGY"
    )
    assert result["status"] == "INSUFFICIENT_EVIDENCE"
    assert result["sample_size"] == 1
    assert result["minimum_sample_met"] is False
    assert result["effectiveness_calculated"] is False
    assert result["complete_benchmark_relative_return_mean"] == "0.01"


def test_backfilled_assignments_are_excluded():
    result = engine(
        [assignment(1, eligible=False), assignment(2)],
        [outcome(1), outcome(2)],
    ).summarize(dimension="market_regime", label="BULL")
    assert result["sample_size"] == 1
    assert result["excluded_backfilled_count"] == 1
    assert result["source_assignments"][0]["cohort_assignment_id"] == "ECOHORT-002"


@pytest.mark.parametrize(
    "dimension,label",
    [
        ("market_regime", "BULL"),
        ("sector", "TECHNOLOGY"),
        ("company_size", "LARGE_CAP"),
        ("investment_horizon", "1_MONTH"),
        ("valuation_regime", "FAIR_VALUE"),
        ("volatility_regime", "NORMAL_VOLATILITY"),
        ("strategy", "strategy-v1"),
        ("signal", "QUALITY"),
        ("model_version", "valuation@1.0"),
    ],
)
def test_supports_each_master_roadmap_dimension(dimension, label):
    result = engine([assignment(1)], [outcome(1)]).summarize(
        dimension=dimension, label=label
    )
    assert result["sample_size"] == 1


def test_no_matching_evidence_has_no_invented_mean():
    result = engine([assignment(1)], [outcome(1)]).summarize(
        dimension="signal", label="MISSING"
    )
    assert result["sample_size"] == 0
    assert result["complete_benchmark_relative_return_mean"] is None
    assert result["exact_fraction"] is None


@pytest.mark.parametrize(
    "kwargs,fragment",
    [
        ({"dimension": "unknown", "label": "x"}, "dimension must"),
        ({"dimension": "signal", "label": ""}, "label is required"),
        (
            {"dimension": "signal", "label": "QUALITY", "minimum_sample_size": 29},
            "cannot be below 30",
        ),
        (
            {"dimension": "signal", "label": "QUALITY", "minimum_sample_size": True},
            "must be an integer",
        ),
    ],
)
def test_invalid_summary_request_fails_closed(kwargs, fragment):
    with pytest.raises(ValueError, match=fragment):
        engine([], []).summarize(**kwargs)


def test_lost_or_changed_outcome_pin_fails_closed():
    changed = outcome(1)
    changed["record_hash"] = "changed"
    with pytest.raises(ValueError, match="lost its pinned"):
        engine([assignment(1)], [changed]).summarize(
            dimension="signal", label="QUALITY"
        )


def test_mean_uses_exact_rational_arithmetic():
    assignments = [assignment(1), assignment(2), assignment(3)]
    outcomes = [outcome(1, 1, 3), outcome(2, 1, 3), outcome(3, 1, 2)]
    result = engine(assignments, outcomes).summarize(
        dimension="signal", label="QUALITY"
    )
    assert result["exact_fraction"] == {
        "numerator": str(Fraction(7, 18).numerator),
        "denominator": str(Fraction(7, 18).denominator),
    }
