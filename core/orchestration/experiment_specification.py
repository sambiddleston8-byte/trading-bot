from __future__ import annotations

"""Immutable preregistered sandbox experiments; no experiment is executed."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.lesson_proposal import SandboxLessonProposalLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


EXPERIMENT_SCHEMA_VERSION = "1.0"
EXPERIMENT_POLICY_VERSION = "preregistered-sandbox-experiment-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
MINIMUM_OUT_OF_SAMPLE_SPAN = timedelta(days=30)
ALLOWED_PRIMARY_METRICS = {
    "MEAN_ABSOLUTE_PREDICTION_ERROR",
    "ROOT_MEAN_SQUARED_PREDICTION_ERROR",
    "COMPLETE_BENCHMARK_RELATIVE_RETURN",
    "MAXIMUM_DRAWDOWN",
    "SHARPE_RATIO",
    "SORTINO_RATIO",
    "HIT_RATE",
}


def _required(value: Any, name: str, *, maximum: int = 3000) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _decimal(value: Any, name: str) -> str:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite() or resolved < 0:
        raise ValueError(f"{name} must be a non-negative finite decimal")
    return "0" if resolved == 0 else format(resolved.normalize(), "f")


def _positive_int(value: Any, name: str, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        resolved = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if resolved < 1 or resolved > maximum or str(resolved) != str(value).strip():
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return resolved


SPECIFICATION_ID_FIELDS = (
    "experiment_name",
    "baseline_strategy_version",
    "candidate_strategy_version",
    "candidate_change_description",
    "point_in_time_data_cutoff",
    "out_of_sample_not_before",
    "out_of_sample_not_after",
    "shadow_not_before",
    "primary_metric",
    "minimum_primary_improvement",
    "maximum_drawdown_degradation",
    "maximum_turnover_increase",
    "maximum_trials",
    "random_seed",
    "acceptance_rule",
    "rejection_rule",
)


def _experiment_id(
    lesson_id: str, registered_at: str, specification: Mapping[str, Any]
) -> str:
    material = [
        lesson_id,
        registered_at,
        {field: specification.get(field) for field in SPECIFICATION_ID_FIELDS},
        EXPERIMENT_POLICY_VERSION,
    ]
    return "EXP-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class SandboxExperimentSpecificationLedger:
    """Append-only preregistration; neither backtest nor model is run."""

    def __init__(self, path: str | Path, lesson_ledger: SandboxLessonProposalLedger) -> None:
        self.path = Path(path)
        self.lesson_ledger = lesson_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Experiment-specification ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank experiment-specification line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at experiment-specification line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Experiment-specification line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def preregister(
        self,
        *,
        lesson_id: str,
        experiment_name: str,
        baseline_strategy_version: str,
        candidate_strategy_version: str,
        candidate_change_description: str,
        point_in_time_data_cutoff: str | datetime,
        out_of_sample_not_before: str | datetime,
        out_of_sample_not_after: str | datetime,
        shadow_not_before: str | datetime,
        primary_metric: str,
        minimum_primary_improvement: Any,
        maximum_drawdown_degradation: Any,
        maximum_turnover_increase: Any,
        maximum_trials: int,
        random_seed: int,
        acceptance_rule: str,
        rejection_rule: str,
        registered_by: str,
        registered_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        lessons = self.lesson_ledger.verify()
        lesson = next((item for item in lessons if item.get("lesson_id") == lesson_id), None)
        if lesson is None:
            raise ValueError("A verified evidence-backed lesson is required")
        baseline = _required(baseline_strategy_version, "baseline_strategy_version", maximum=100)
        candidate = _required(candidate_strategy_version, "candidate_strategy_version", maximum=100)
        if baseline == candidate:
            raise ValueError("candidate_strategy_version must differ from baseline")
        cutoff = _as_datetime(point_in_time_data_cutoff)
        test_start = _as_datetime(out_of_sample_not_before)
        test_end = _as_datetime(out_of_sample_not_after)
        shadow_start = _as_datetime(shadow_not_before)
        registered = _as_datetime(registered_at or datetime.now(timezone.utc))
        if registered > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("registered_at cannot be in the future")
        if cutoff > registered:
            raise ValueError("point_in_time_data_cutoff cannot postdate preregistration")
        if test_start <= registered or test_start <= cutoff:
            raise ValueError("out-of-sample evaluation must begin after preregistration and data cutoff")
        if test_end < test_start + MINIMUM_OUT_OF_SAMPLE_SPAN:
            raise ValueError("out-of-sample window must span at least 30 days")
        if shadow_start <= test_end:
            raise ValueError("shadow_not_before must be after the out-of-sample window")
        metric = _required(primary_metric, "primary_metric", maximum=80).upper()
        if metric not in ALLOWED_PRIMARY_METRICS:
            raise ValueError("primary_metric is not supported")
        trials = _positive_int(maximum_trials, "maximum_trials", 100)
        seed = _positive_int(random_seed, "random_seed", 2_147_483_647)
        specification = {
            "experiment_name": _required(experiment_name, "experiment_name", maximum=200),
            "baseline_strategy_version": baseline,
            "candidate_strategy_version": candidate,
            "candidate_change_description": _required(
                candidate_change_description, "candidate_change_description"
            ),
            "point_in_time_data_cutoff": cutoff.isoformat(),
            "out_of_sample_not_before": test_start.isoformat(),
            "out_of_sample_not_after": test_end.isoformat(),
            "shadow_not_before": shadow_start.isoformat(),
            "primary_metric": metric,
            "minimum_primary_improvement": _decimal(
                minimum_primary_improvement, "minimum_primary_improvement"
            ),
            "maximum_drawdown_degradation": _decimal(
                maximum_drawdown_degradation, "maximum_drawdown_degradation"
            ),
            "maximum_turnover_increase": _decimal(
                maximum_turnover_increase, "maximum_turnover_increase"
            ),
            "maximum_trials": trials,
            "random_seed": seed,
            "acceptance_rule": _required(acceptance_rule, "acceptance_rule"),
            "rejection_rule": _required(rejection_rule, "rejection_rule"),
        }
        result = {
            "schema_version": EXPERIMENT_SCHEMA_VERSION,
            "policy_version": EXPERIMENT_POLICY_VERSION,
            "experiment_id": _experiment_id(
                lesson_id, registered.isoformat(), specification
            ),
            "status": "PREREGISTERED_NOT_EXECUTED",
            "record_type": "CONTROLLED_LEARNING_SANDBOX_EXPERIMENT_SPECIFICATION",
            "simulation_only": True,
            "registered_at": registered.isoformat(),
            "registered_by": _required(registered_by, "registered_by", maximum=100),
            "lesson_id": lesson["lesson_id"],
            "lesson_record_hash": lesson["record_hash"],
            **specification,
            "point_in_time_only": True,
            "random_split_allowed": False,
            "out_of_sample_reuse_allowed": False,
            "metric_substitution_allowed": False,
            "optional_stopping_allowed": False,
            "experiment_executed": False,
            "result_recorded": False,
            "shadow_test_started": False,
            "promotion_approved": False,
            "production_rule_changed": False,
            "code_changed": False,
            "deployment_performed": False,
            "order_submitted": False,
            "live_trading_enabled": False,
        }
        return self._append(result, allow_existing=allow_existing)

    def _pinned_lesson(self, record: Mapping[str, Any]):
        return resolve_pinned_records(
            self.lesson_ledger.verify(),
            [record.get("lesson_id")],
            [record.get("lesson_record_hash")],
            id_field="lesson_id",
            label="lesson",
        )

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Experiment-specification record {index} has been modified.")
            lessons, reasons = self._pinned_lesson(record)
            if reasons or len(lessons) != 1:
                raise LedgerIntegrityError(f"Experiment-specification record {index} lost its lesson.")
            lesson = lessons[0]
            try:
                registered = _as_datetime(record.get("registered_at"))
                cutoff = _as_datetime(record.get("point_in_time_data_cutoff"))
                test_start = _as_datetime(record.get("out_of_sample_not_before"))
                test_end = _as_datetime(record.get("out_of_sample_not_after"))
                shadow_start = _as_datetime(record.get("shadow_not_before"))
                baseline = _required(record.get("baseline_strategy_version"), "baseline", maximum=100)
                candidate = _required(record.get("candidate_strategy_version"), "candidate", maximum=100)
                metric = _required(record.get("primary_metric"), "primary_metric", maximum=80).upper()
                if metric not in ALLOWED_PRIMARY_METRICS or baseline == candidate:
                    raise ValueError("invalid metric or strategy versions")
                minimum = _decimal(record.get("minimum_primary_improvement"), "minimum_primary_improvement")
                drawdown = _decimal(record.get("maximum_drawdown_degradation"), "maximum_drawdown_degradation")
                turnover = _decimal(record.get("maximum_turnover_increase"), "maximum_turnover_increase")
                trials = _positive_int(record.get("maximum_trials"), "maximum_trials", 100)
                seed = _positive_int(record.get("random_seed"), "random_seed", 2_147_483_647)
                _required(record.get("registered_by"), "registered_by", maximum=100)
                _required(record.get("experiment_name"), "experiment_name", maximum=200)
                _required(record.get("candidate_change_description"), "candidate_change_description")
                _required(record.get("acceptance_rule"), "acceptance_rule")
                _required(record.get("rejection_rule"), "rejection_rule")
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Experiment-specification record {index} has invalid values.") from error
            expected_id = _experiment_id(
                lesson["lesson_id"], registered.isoformat(), record
            )
            fixed_false = (
                "random_split_allowed", "out_of_sample_reuse_allowed",
                "metric_substitution_allowed", "optional_stopping_allowed",
                "experiment_executed", "result_recorded", "shadow_test_started",
                "promotion_approved", "production_rule_changed", "code_changed",
                "deployment_performed", "order_submitted", "live_trading_enabled",
            )
            boundary = (
                record.get("schema_version") == EXPERIMENT_SCHEMA_VERSION
                and record.get("policy_version") == EXPERIMENT_POLICY_VERSION
                and record.get("experiment_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "PREREGISTERED_NOT_EXECUTED"
                and record.get("record_type") == "CONTROLLED_LEARNING_SANDBOX_EXPERIMENT_SPECIFICATION"
                and record.get("simulation_only") is True
                and record.get("lesson_id") == lesson["lesson_id"]
                and record.get("lesson_record_hash") == lesson["record_hash"]
                and cutoff <= registered < test_start
                and cutoff < test_start
                and test_end >= test_start + MINIMUM_OUT_OF_SAMPLE_SPAN
                and shadow_start > test_end
                and registered <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("minimum_primary_improvement") == minimum
                and record.get("maximum_drawdown_degradation") == drawdown
                and record.get("maximum_turnover_increase") == turnover
                and record.get("maximum_trials") == trials
                and record.get("random_seed") == seed
                and record.get("point_in_time_only") is True
                and all(record.get(field) is False for field in fixed_false)
            )
            if not boundary:
                raise LedgerIntegrityError(f"Experiment-specification record {index} violates its boundary.")
            seen_ids.add(expected_id)
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next((item for item in records if item["experiment_id"] == result["experiment_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(f"Experiment {result['experiment_id']} already exists.")
            material = {**result, "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH}
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
