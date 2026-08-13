from __future__ import annotations

"""Deterministic, non-causal summaries of evidence-pinned simulated outcomes."""

from fractions import Fraction
import hashlib
from typing import Any, Mapping

from core.performance.effectiveness_cohort import (
    EVIDENCED_DIMENSIONS,
    EffectivenessCohortAssignmentLedger,
)
from core.performance.portfolio_valuation import (
    _canonical_json,
    _decimal_string,
    _fraction,
    _fraction_material,
)


EFFECTIVENESS_SUMMARY_VERSION = "descriptive-effectiveness-summary-v1"
MINIMUM_ALLOWED_SAMPLE_SIZE = 30
SUPPORTED_DIMENSIONS = (
    "market_regime",
    "sector",
    "company_size",
    "investment_horizon",
    "valuation_regime",
    "volatility_regime",
    "strategy",
    "signal",
    "model_version",
)
POLICY = {
    "availability_policy": "DECISION_TIME_AVAILABLE_LABELS_ONLY",
    "sample_policy": "MINIMUM_30_COMPLETE_INDEPENDENCE_NOT_ESTABLISHED",
    "statistical_policy": "DESCRIPTIVE_MEAN_ONLY_NO_SIGNIFICANCE_TEST",
    "causal_policy": "NO_CAUSAL_EFFECTIVENESS_CLAIM",
    "action_policy": "NO_RECOMMENDATION_LEARNING_PROMOTION_OR_TRADING",
}


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _model_labels(versions: Any) -> tuple[str, ...]:
    if not isinstance(versions, list) or not versions:
        return ()
    labels = []
    for value in versions:
        if not isinstance(value, Mapping):
            return ()
        component = str(value.get("component") or "").strip()
        version = str(value.get("version") or "").strip()
        if not component or not version:
            return ()
        labels.append(f"{component}@{version}")
    return tuple(sorted(set(labels)))


def _labels_for(assignment: Mapping[str, Any], dimension: str) -> tuple[str, ...]:
    if dimension in EVIDENCED_DIMENSIONS:
        value = assignment.get("labels", {}).get(dimension, {})
        return (str(value.get("label") or ""),)
    if dimension == "investment_horizon":
        return (str(assignment.get("investment_horizon") or ""),)
    if dimension == "strategy":
        return (str(assignment.get("strategy_version") or ""),)
    if dimension == "model_version":
        return _model_labels(assignment.get("model_versions"))
    return ()


class EffectivenessSummaryEngine:
    """Read-only aggregation; source ledgers remain the sole authority."""

    def __init__(self, cohort_ledger: EffectivenessCohortAssignmentLedger) -> None:
        self.cohort_ledger = cohort_ledger

    def summarize(
        self,
        *,
        dimension: str,
        label: str,
        minimum_sample_size: int = MINIMUM_ALLOWED_SAMPLE_SIZE,
    ) -> dict[str, Any]:
        resolved_dimension = _required(dimension, "dimension").lower()
        if resolved_dimension not in SUPPORTED_DIMENSIONS:
            raise ValueError(
                "dimension must be one of: " + ", ".join(SUPPORTED_DIMENSIONS)
            )
        resolved_label = _required(label, "label")
        if isinstance(minimum_sample_size, bool) or not isinstance(minimum_sample_size, int):
            raise ValueError("minimum_sample_size must be an integer")
        if minimum_sample_size < MINIMUM_ALLOWED_SAMPLE_SIZE:
            raise ValueError(
                f"minimum_sample_size cannot be below {MINIMUM_ALLOWED_SAMPLE_SIZE}"
            )

        assignments = self.cohort_ledger.verify()
        outcomes = {
            item["result_id"]: item
            for item in self.cohort_ledger.relative_return_ledger.verify()
        }
        included = []
        excluded_backfilled = 0
        for assignment in assignments:
            if resolved_label not in _labels_for(assignment, resolved_dimension):
                continue
            if assignment.get("effectiveness_claim_eligible") is not True:
                excluded_backfilled += 1
                continue
            outcome = outcomes.get(assignment.get("relative_return_result_id"))
            if outcome is None or outcome.get("record_hash") != assignment.get(
                "relative_return_record_hash"
            ):
                raise ValueError("Cohort assignment lost its pinned relative-return outcome")
            try:
                relative_return = _fraction(
                    outcome["exact_fractions"]["complete_benchmark_relative_return"],
                    "complete benchmark-relative return",
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("Pinned outcome has invalid relative-return economics") from error
            included.append((assignment, outcome, relative_return))

        included.sort(key=lambda value: value[0]["cohort_assignment_id"])
        sample_size = len(included)
        minimum_met = sample_size >= minimum_sample_size
        total = sum((value[2] for value in included), Fraction(0))
        mean = total / sample_size if sample_size else None
        source_assignments = [
            {
                "cohort_assignment_id": assignment["cohort_assignment_id"],
                "cohort_assignment_record_hash": assignment["record_hash"],
                "relative_return_result_id": outcome["result_id"],
                "relative_return_record_hash": outcome["record_hash"],
            }
            for assignment, outcome, _ in included
        ]
        identity = {
            "summary_version": EFFECTIVENESS_SUMMARY_VERSION,
            "dimension": resolved_dimension,
            "label": resolved_label,
            "minimum_sample_size": minimum_sample_size,
            "source_assignments": source_assignments,
        }
        summary_id = "ESUM-" + hashlib.sha256(
            _canonical_json(identity).encode("utf-8")
        ).hexdigest()[:32].upper()
        result = {
            **identity,
            "summary_id": summary_id,
            "status": (
                "DESCRIPTIVE_SAMPLE_AVAILABLE"
                if minimum_met
                else "INSUFFICIENT_EVIDENCE"
            ),
            "simulation_only": True,
            "sample_size": sample_size,
            "minimum_sample_met": minimum_met,
            "excluded_backfilled_count": excluded_backfilled,
            "complete_benchmark_relative_return_mean": (
                _decimal_string(mean) if mean is not None else None
            ),
            "exact_fraction": (
                _fraction_material(mean) if mean is not None else None
            ),
            "effectiveness_calculated": minimum_met,
            "descriptive_only": True,
            "independence_established": False,
            "statistical_significance_tested": False,
            "causal_effectiveness_claim": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "promotion_eligible": False,
            "order_submission_enabled": False,
            "live_trading_enabled": False,
            **POLICY,
        }
        return result
