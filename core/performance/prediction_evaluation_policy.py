from __future__ import annotations

"""Immutable human-preregistered hit-rate and prediction-cohort policy."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import (
    GENESIS_HASH,
    LedgerIntegrityError,
    canonical_timestamp,
    normalize_model_version,
)
from core.performance.portfolio_valuation import (
    _canonical_json,
    _decimal_string,
    _fraction_material,
    _record_hash,
    _write_all,
)


PREDICTION_EVALUATION_POLICY_SCHEMA_VERSION = "1.0"
PREDICTION_EVALUATION_POLICY_VERSION = "preregistered-fixed-horizon-cohorts-v1"
ALLOWED_SUCCESS_RULES = {
    "POSITIVE_ABSOLUTE_RETURN",
    "MEETS_OR_EXCEEDS_PREDICTED_RETURN",
    "POSITIVE_BENCHMARK_RELATIVE_RETURN",
}
MINIMUM_COHORT_OBSERVATIONS = 30
MINIMUM_TOTAL_OBSERVATIONS = 100
MINIMUM_LEAD_TIME = timedelta(minutes=5)
MAX_CLOCK_SKEW = timedelta(minutes=5)


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _models(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    resolved = [normalize_model_version(item) for item in values]
    if not resolved or any(
        not item["component"].strip() or not item["version"].strip()
        for item in resolved
    ):
        raise ValueError("model_versions require at least one named component and version")
    return resolved


def _fraction(value: Any, name: str) -> Fraction:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite():
        raise ValueError(f"{name} must be a finite decimal")
    return Fraction(resolved)


def _confidence_edges(values: Sequence[Any]) -> list[Fraction]:
    edges = [_fraction(item, "confidence bucket edge") for item in values]
    if len(edges) < 2 or len(edges) > 21:
        raise ValueError("confidence_bucket_edges require 2 to 21 edges")
    if edges[0] != 0 or edges[-1] != 100:
        raise ValueError("confidence_bucket_edges must start at 0 and end at 100")
    if any(left >= right for left, right in zip(edges, edges[1:])):
        raise ValueError("confidence_bucket_edges must strictly increase")
    return edges


def _return_splits(values: Sequence[Any]) -> list[Fraction]:
    splits = [_fraction(item, "expected-return split point") for item in values]
    if not splits or len(splits) > 20:
        raise ValueError("expected_return_split_points require 1 to 20 values")
    if any(item <= -1 for item in splits):
        raise ValueError("expected-return split points must exceed a total loss")
    if any(left >= right for left, right in zip(splits, splits[1:])):
        raise ValueError("expected_return_split_points must strictly increase")
    return splits


def _success_definition(rule: str) -> dict[str, Any]:
    if rule == "POSITIVE_ABSOLUTE_RETURN":
        return {
            "success_formula": "actual_total_return > 0",
            "benchmark_pairing_required": False,
        }
    if rule == "MEETS_OR_EXCEEDS_PREDICTED_RETURN":
        return {
            "success_formula": "actual_total_return >= predicted_expected_return",
            "benchmark_pairing_required": False,
        }
    return {
        "success_formula": "actual_total_return - matched_benchmark_total_return > 0",
        "benchmark_pairing_required": True,
    }


def _policy_id(
    portfolio_version: str,
    evaluation_not_before: str,
    success_rule: str,
    confidence_edges: Sequence[str],
    return_splits: Sequence[str],
    strategy_version: str,
) -> str:
    material = [
        portfolio_version,
        evaluation_not_before,
        success_rule,
        list(confidence_edges),
        list(return_splits),
        strategy_version,
        PREDICTION_EVALUATION_POLICY_VERSION,
    ]
    return "PEPOL-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class PredictionEvaluationPolicyLedger:
    """Append-only policy boundary; no hit rate or calibration is calculated."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Prediction-evaluation-policy ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank prediction-evaluation-policy line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at prediction-evaluation-policy line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Prediction-evaluation-policy line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def preregister(
        self,
        *,
        portfolio_version: str,
        success_rule: str,
        confidence_bucket_edges: Sequence[Any],
        expected_return_split_points: Sequence[Any],
        evaluation_not_before: str | datetime,
        decided_by: str,
        decision_reference: str,
        human_decision_confirmed: bool,
        strategy_version: str,
        model_versions: Sequence[Mapping[str, Any]],
        git_revision: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = _required(portfolio_version, "portfolio_version")
        rule = _required(success_rule, "success_rule").upper()
        if rule not in ALLOWED_SUCCESS_RULES:
            raise ValueError("success_rule is not supported")
        if human_decision_confirmed is not True:
            raise ValueError("An explicit human decision is required before preregistration")
        evaluator_start = _as_datetime(evaluation_not_before)
        recorded = _as_datetime(recorded_at or datetime.now(timezone.utc))
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        if evaluator_start < recorded + MINIMUM_LEAD_TIME:
            raise ValueError(
                "evaluation_not_before must be at least five minutes after preregistration"
            )
        confidence = [_decimal_string(item) for item in _confidence_edges(confidence_bucket_edges)]
        return_splits = [_decimal_string(item) for item in _return_splits(expected_return_split_points)]
        strategy = _required(strategy_version, "strategy_version")
        models = _models(model_versions)
        git = _required(git_revision, "git_revision")
        record = {
            "schema_version": PREDICTION_EVALUATION_POLICY_SCHEMA_VERSION,
            "policy_version": PREDICTION_EVALUATION_POLICY_VERSION,
            "policy_id": _policy_id(
                version, evaluator_start.isoformat(), rule, confidence, return_splits, strategy
            ),
            "status": "PREREGISTERED",
            "record_type": "HUMAN_APPROVED_PREDICTION_EVALUATION_POLICY",
            "portfolio_version": version,
            "evaluation_not_before": evaluator_start.isoformat(),
            "eligibility_time_basis": "decision_decided_at >= evaluation_not_before",
            "recorded_at": recorded.isoformat(),
            "decided_by": _required(decided_by, "decided_by"),
            "decision_reference": _required(decision_reference, "decision_reference"),
            "human_decision_confirmed": True,
            "success_rule": rule,
            **_success_definition(rule),
            "confidence_semantics": "RANKING_SCORE_0_TO_100_NOT_EVENT_PROBABILITY",
            "confidence_bucket_edges": confidence,
            "confidence_bucket_boundary": "LEFT_CLOSED_RIGHT_OPEN_EXCEPT_FINAL_RIGHT_CLOSED",
            "expected_return_split_points": return_splits,
            "expected_return_bucket_boundary": "[-1,first), [split_i,split_i+1), [last,+infinity)",
            "fixed_horizon_cohorts_only": True,
            "cross_horizon_pooling_allowed": False,
            "cross_model_version_pooling_allowed": False,
            "complete_eligible_cohort_required": True,
            "discretionary_outcome_exclusion_allowed": False,
            "complete_round_trip_cost_evidence_required": True,
            "entry_only_outcomes_eligible": False,
            "minimum_total_observations": MINIMUM_TOTAL_OBSERVATIONS,
            "minimum_observations_per_reported_cohort": MINIMUM_COHORT_OBSERVATIONS,
            "empty_or_small_cohorts_suppressed": True,
            "calibration_measures": [
                "MEAN_PREDICTION_ERROR",
                "MEAN_ABSOLUTE_PREDICTION_ERROR",
                "ROOT_MEAN_SQUARED_PREDICTION_ERROR",
                "COHORT_MEAN_PREDICTED_VS_ACTUAL_RETURN",
            ],
            "probability_calibration_claim_allowed": False,
            "immutable_before_observation": True,
            "policy_selection_precedes_every_eligible_decision": True,
            "outcome_knowledge_allowed_at_policy_selection": False,
            "retrospective_application_allowed": False,
            "hit_rate_calculated": False,
            "calibration_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
            "strategy_version": strategy,
            "model_versions": models,
            "git_revision": git,
        }
        return self._append(record, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        seen_windows = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(
                    f"Prediction-evaluation-policy record {index} has been modified."
                )
            try:
                version = _required(record.get("portfolio_version"), "portfolio_version")
                rule = _required(record.get("success_rule"), "success_rule").upper()
                if rule not in ALLOWED_SUCCESS_RULES:
                    raise ValueError("unsupported success rule")
                evaluator_start = _as_datetime(record.get("evaluation_not_before"))
                recorded = _as_datetime(record.get("recorded_at"))
                confidence = [_decimal_string(item) for item in _confidence_edges(record.get("confidence_bucket_edges") or [])]
                return_splits = [_decimal_string(item) for item in _return_splits(record.get("expected_return_split_points") or [])]
                strategy = _required(record.get("strategy_version"), "strategy_version")
                models = _models(record.get("model_versions") or [])
                git = _required(record.get("git_revision"), "git_revision")
                _required(record.get("decided_by"), "decided_by")
                _required(record.get("decision_reference"), "decision_reference")
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Prediction-evaluation-policy record {index} has invalid values."
                ) from error
            expected_id = _policy_id(
                version, evaluator_start.isoformat(), rule, confidence, return_splits, strategy
            )
            window_key = (version, evaluator_start.isoformat())
            boundary = (
                record.get("schema_version") == PREDICTION_EVALUATION_POLICY_SCHEMA_VERSION
                and record.get("policy_version") == PREDICTION_EVALUATION_POLICY_VERSION
                and record.get("policy_id") == expected_id
                and expected_id not in seen_ids
                and window_key not in seen_windows
                and record.get("status") == "PREREGISTERED"
                and record.get("record_type") == "HUMAN_APPROVED_PREDICTION_EVALUATION_POLICY"
                and record.get("human_decision_confirmed") is True
                and evaluator_start >= recorded + MINIMUM_LEAD_TIME
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("eligibility_time_basis") == "decision_decided_at >= evaluation_not_before"
                and all(record.get(key) == value for key, value in _success_definition(rule).items())
                and record.get("confidence_semantics") == "RANKING_SCORE_0_TO_100_NOT_EVENT_PROBABILITY"
                and record.get("confidence_bucket_edges") == confidence
                and record.get("confidence_bucket_boundary") == "LEFT_CLOSED_RIGHT_OPEN_EXCEPT_FINAL_RIGHT_CLOSED"
                and record.get("expected_return_split_points") == return_splits
                and record.get("expected_return_bucket_boundary") == "[-1,first), [split_i,split_i+1), [last,+infinity)"
                and record.get("fixed_horizon_cohorts_only") is True
                and record.get("cross_horizon_pooling_allowed") is False
                and record.get("cross_model_version_pooling_allowed") is False
                and record.get("complete_eligible_cohort_required") is True
                and record.get("discretionary_outcome_exclusion_allowed") is False
                and record.get("complete_round_trip_cost_evidence_required") is True
                and record.get("entry_only_outcomes_eligible") is False
                and record.get("minimum_total_observations") == MINIMUM_TOTAL_OBSERVATIONS
                and record.get("minimum_observations_per_reported_cohort") == MINIMUM_COHORT_OBSERVATIONS
                and record.get("empty_or_small_cohorts_suppressed") is True
                and record.get("calibration_measures") == [
                    "MEAN_PREDICTION_ERROR",
                    "MEAN_ABSOLUTE_PREDICTION_ERROR",
                    "ROOT_MEAN_SQUARED_PREDICTION_ERROR",
                    "COHORT_MEAN_PREDICTED_VS_ACTUAL_RETURN",
                ]
                and record.get("probability_calibration_claim_allowed") is False
                and record.get("immutable_before_observation") is True
                and record.get("policy_selection_precedes_every_eligible_decision") is True
                and record.get("outcome_knowledge_allowed_at_policy_selection") is False
                and record.get("retrospective_application_allowed") is False
                and record.get("hit_rate_calculated") is False
                and record.get("calibration_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("live_trading_enabled") is False
                and record.get("model_versions") == models
                and record.get("git_revision") == git
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Prediction-evaluation-policy record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            seen_windows.add(window_key)
            previous_hash = record["record_hash"]
        return records

    def _append(self, policy: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["policy_id"] == policy["policy_id"]), None
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in policy.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Prediction evaluation policy {policy['policy_id']} already exists."
                )
            if any(
                item["portfolio_version"] == policy["portfolio_version"]
                and item["evaluation_not_before"] == policy["evaluation_not_before"]
                for item in records
            ):
                raise LedgerIntegrityError(
                    "A different prediction policy is already preregistered for this evaluation window."
                )
            material = {
                **policy,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                _write_all(target, (_canonical_json(record) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def repair_incomplete_tail(self) -> Path | None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if not self.path.exists():
                return None
            raw = self.path.read_bytes()
            if not raw or raw.endswith(b"\n"):
                return None
            complete_end = raw.rfind(b"\n") + 1
            prefix, tail = raw[:complete_end], raw[complete_end:]
            try:
                json.loads(tail.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                backup = self.path.with_suffix(self.path.suffix + f".incomplete-tail-{uuid4().hex}")
                backup_descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    _write_all(backup_descriptor, tail)
                    os.fsync(backup_descriptor)
                finally:
                    os.close(backup_descriptor)
                target = os.open(self.path, os.O_WRONLY | os.O_TRUNC)
                try:
                    _write_all(target, prefix)
                    os.fsync(target)
                finally:
                    os.close(target)
                self.verify()
                return backup
            target = os.open(self.path, os.O_WRONLY | os.O_APPEND)
            try:
                _write_all(target, b"\n")
                os.fsync(target)
            finally:
                os.close(target)
            self.verify()
            return None
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
