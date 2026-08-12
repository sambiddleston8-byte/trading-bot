from __future__ import annotations

"""Immutable mechanical evaluation of completed sandbox experiment artifacts."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.experiment_run_manifest import SandboxExperimentRunManifestLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


RESULT_SCHEMA_VERSION = "1.0"
RESULT_POLICY_VERSION = "mechanical-preregistered-sandbox-result-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
LOWER_IS_BETTER = {
    "MEAN_ABSOLUTE_PREDICTION_ERROR",
    "ROOT_MEAN_SQUARED_PREDICTION_ERROR",
    "MAXIMUM_DRAWDOWN",
}
HIGHER_IS_BETTER = {
    "COMPLETE_BENCHMARK_RELATIVE_RETURN",
    "SHARPE_RATIO",
    "SORTINO_RATIO",
    "HIT_RATE",
}


def _required(value: Any, name: str, maximum: int = 300) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _decimal(value: Any, name: str, *, non_negative: bool = False) -> Decimal:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite() or (non_negative and resolved < 0):
        qualifier = "non-negative " if non_negative else ""
        raise ValueError(f"{name} must be a {qualifier}finite decimal")
    return resolved


def _canonical_decimal(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


def _hash(value: Any, name: str) -> str:
    resolved = str(value or "").strip().lower()
    if not _SHA256.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


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


def _result_id(run_manifest_id: str, completed_at: str, output_hash: str) -> str:
    return "RESULT-" + hashlib.sha256(
        _canonical_json([run_manifest_id, completed_at, output_hash, RESULT_POLICY_VERSION]).encode()
    ).hexdigest()[:32].upper()


class SandboxExperimentResultLedger:
    """Append-only result evidence; evaluation cannot promote or apply a change."""

    def __init__(self, path: str | Path, run_manifest_ledger: SandboxExperimentRunManifestLedger) -> None:
        self.path = Path(path)
        self.run_manifest_ledger = run_manifest_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Experiment-result ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank experiment-result line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(f"Invalid JSON at experiment-result line {line_number}.") from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(f"Experiment-result line {line_number} is not an object.")
                records.append(record)
        return records

    def record(
        self, *, run_manifest_id: str, runner_output_sha256: str,
        trials_completed: int, baseline_primary_metric: Any,
        candidate_primary_metric: Any, baseline_maximum_drawdown: Any,
        candidate_maximum_drawdown: Any, baseline_turnover: Any,
        candidate_turnover: Any, completed_by: str,
        completed_at: str | datetime | None = None, allow_existing: bool = True,
    ) -> dict[str, Any]:
        manifests = self.run_manifest_ledger.verify()
        manifest = next((item for item in manifests if item.get("run_manifest_id") == run_manifest_id), None)
        if manifest is None:
            raise ValueError("A verified run manifest is required")
        experiment = self._experiment_for(manifest)
        completed = _timestamp(completed_at or datetime.now(timezone.utc))
        if completed > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("completed_at cannot be in the future")
        if completed < _timestamp(manifest["planned_at"]):
            raise ValueError("completed_at cannot predate the run manifest")
        if completed < _timestamp(experiment["out_of_sample_not_after"]):
            raise ValueError("result cannot complete before the preregistered out-of-sample window ends")
        trials = _positive_int(trials_completed, "trials_completed", 100)
        if trials != int(manifest["planned_trial_count"]):
            raise ValueError("trials_completed must equal the planned count; optional stopping is forbidden")
        metric = experiment["primary_metric"]
        baseline = _decimal(baseline_primary_metric, "baseline_primary_metric")
        candidate = _decimal(candidate_primary_metric, "candidate_primary_metric")
        baseline_drawdown = _decimal(baseline_maximum_drawdown, "baseline_maximum_drawdown", non_negative=True)
        candidate_drawdown = _decimal(candidate_maximum_drawdown, "candidate_maximum_drawdown", non_negative=True)
        baseline_turnover_value = _decimal(baseline_turnover, "baseline_turnover", non_negative=True)
        candidate_turnover_value = _decimal(candidate_turnover, "candidate_turnover", non_negative=True)
        if metric in LOWER_IS_BETTER:
            improvement = baseline - candidate
        elif metric in HIGHER_IS_BETTER:
            improvement = candidate - baseline
        else:
            raise ValueError("The preregistered primary metric has no fixed direction")
        drawdown_degradation = candidate_drawdown - baseline_drawdown
        turnover_increase = candidate_turnover_value - baseline_turnover_value
        primary_passed = improvement >= Decimal(experiment["minimum_primary_improvement"])
        drawdown_passed = drawdown_degradation <= Decimal(experiment["maximum_drawdown_degradation"])
        turnover_passed = turnover_increase <= Decimal(experiment["maximum_turnover_increase"])
        accepted = primary_passed and drawdown_passed and turnover_passed
        output_hash = _hash(runner_output_sha256, "runner_output_sha256")
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "policy_version": RESULT_POLICY_VERSION,
            "result_id": _result_id(run_manifest_id, completed.isoformat(), output_hash),
            "record_type": "CONTROLLED_LEARNING_SANDBOX_EXPERIMENT_RESULT",
            "status": "ACCEPTANCE_CRITERIA_MET" if accepted else "REJECTION_CRITERIA_MET",
            "simulation_only": True,
            "completed_at": completed.isoformat(),
            "completed_by": _required(completed_by, "completed_by", 100),
            "run_manifest_id": manifest["run_manifest_id"],
            "run_manifest_record_hash": manifest["record_hash"],
            "experiment_id": experiment["experiment_id"],
            "experiment_record_hash": experiment["record_hash"],
            "runner_output_sha256": output_hash,
            "primary_metric": metric,
            "trials_completed": trials,
            "baseline_primary_metric": _canonical_decimal(baseline),
            "candidate_primary_metric": _canonical_decimal(candidate),
            "primary_improvement": _canonical_decimal(improvement),
            "minimum_primary_improvement": experiment["minimum_primary_improvement"],
            "baseline_maximum_drawdown": _canonical_decimal(baseline_drawdown),
            "candidate_maximum_drawdown": _canonical_decimal(candidate_drawdown),
            "drawdown_degradation": _canonical_decimal(drawdown_degradation),
            "maximum_drawdown_degradation": experiment["maximum_drawdown_degradation"],
            "baseline_turnover": _canonical_decimal(baseline_turnover_value),
            "candidate_turnover": _canonical_decimal(candidate_turnover_value),
            "turnover_increase": _canonical_decimal(turnover_increase),
            "maximum_turnover_increase": experiment["maximum_turnover_increase"],
            "primary_threshold_passed": primary_passed,
            "drawdown_constraint_passed": drawdown_passed,
            "turnover_constraint_passed": turnover_passed,
            "all_preregistered_criteria_met": accepted,
            "random_split_used": False,
            "out_of_sample_reused": False,
            "metric_substituted": False,
            "optional_stopping_used": False,
            "shadow_test_started": False,
            "promotion_approved": False,
            "production_rule_changed": False,
            "deployment_performed": False,
            "order_submitted": False,
            "live_trading_enabled": False,
        }
        return self._append(result, allow_existing=allow_existing)

    def _experiment_for(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        experiments, reasons = resolve_pinned_records(
            self.run_manifest_ledger.experiment_ledger.verify(),
            [manifest.get("experiment_id")], [manifest.get("experiment_record_hash")],
            id_field="experiment_id", label="experiment",
        )
        if reasons or len(experiments) != 1:
            raise LedgerIntegrityError("Run manifest lost its preregistered experiment.")
        return experiments[0]

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Experiment-result record {index} has been modified.")
            manifests, reasons = resolve_pinned_records(
                self.run_manifest_ledger.verify(), [record.get("run_manifest_id")],
                [record.get("run_manifest_record_hash")], id_field="run_manifest_id", label="run manifest",
            )
            if reasons or len(manifests) != 1:
                raise LedgerIntegrityError(f"Experiment-result record {index} lost its run manifest.")
            manifest = manifests[0]
            experiment = self._experiment_for(manifest)
            try:
                completed = _timestamp(record.get("completed_at"))
                output_hash = _hash(record.get("runner_output_sha256"), "runner output")
                baseline = _decimal(record.get("baseline_primary_metric"), "baseline")
                candidate = _decimal(record.get("candidate_primary_metric"), "candidate")
                baseline_drawdown = _decimal(record.get("baseline_maximum_drawdown"), "baseline drawdown", non_negative=True)
                candidate_drawdown = _decimal(record.get("candidate_maximum_drawdown"), "candidate drawdown", non_negative=True)
                baseline_turnover = _decimal(record.get("baseline_turnover"), "baseline turnover", non_negative=True)
                candidate_turnover = _decimal(record.get("candidate_turnover"), "candidate turnover", non_negative=True)
                trials = _positive_int(record.get("trials_completed"), "trials", 100)
                _required(record.get("completed_by"), "completed_by", 100)
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Experiment-result record {index} has invalid values.") from error
            metric = experiment["primary_metric"]
            improvement = baseline - candidate if metric in LOWER_IS_BETTER else candidate - baseline
            drawdown_degradation = candidate_drawdown - baseline_drawdown
            turnover_increase = candidate_turnover - baseline_turnover
            primary_passed = improvement >= Decimal(experiment["minimum_primary_improvement"])
            drawdown_passed = drawdown_degradation <= Decimal(experiment["maximum_drawdown_degradation"])
            turnover_passed = turnover_increase <= Decimal(experiment["maximum_turnover_increase"])
            accepted = primary_passed and drawdown_passed and turnover_passed
            expected_id = _result_id(manifest["run_manifest_id"], completed.isoformat(), output_hash)
            fixed_false = (
                "random_split_used", "out_of_sample_reused", "metric_substituted",
                "optional_stopping_used", "shadow_test_started", "promotion_approved",
                "production_rule_changed", "deployment_performed", "order_submitted",
                "live_trading_enabled",
            )
            boundary = (
                record.get("schema_version") == RESULT_SCHEMA_VERSION
                and record.get("policy_version") == RESULT_POLICY_VERSION
                and record.get("result_id") == expected_id and expected_id not in seen_ids
                and record.get("record_type") == "CONTROLLED_LEARNING_SANDBOX_EXPERIMENT_RESULT"
                and record.get("status") == ("ACCEPTANCE_CRITERIA_MET" if accepted else "REJECTION_CRITERIA_MET")
                and record.get("simulation_only") is True
                and record.get("run_manifest_id") == manifest["run_manifest_id"]
                and record.get("run_manifest_record_hash") == manifest["record_hash"]
                and record.get("experiment_id") == experiment["experiment_id"]
                and record.get("experiment_record_hash") == experiment["record_hash"]
                and completed >= _timestamp(manifest["planned_at"])
                and completed >= _timestamp(experiment["out_of_sample_not_after"])
                and completed <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and trials == int(manifest["planned_trial_count"])
                and record.get("primary_metric") == metric
                and record.get("baseline_primary_metric") == _canonical_decimal(baseline)
                and record.get("candidate_primary_metric") == _canonical_decimal(candidate)
                and record.get("primary_improvement") == _canonical_decimal(improvement)
                and record.get("minimum_primary_improvement") == experiment["minimum_primary_improvement"]
                and record.get("baseline_maximum_drawdown") == _canonical_decimal(baseline_drawdown)
                and record.get("candidate_maximum_drawdown") == _canonical_decimal(candidate_drawdown)
                and record.get("drawdown_degradation") == _canonical_decimal(drawdown_degradation)
                and record.get("maximum_drawdown_degradation") == experiment["maximum_drawdown_degradation"]
                and record.get("baseline_turnover") == _canonical_decimal(baseline_turnover)
                and record.get("candidate_turnover") == _canonical_decimal(candidate_turnover)
                and record.get("turnover_increase") == _canonical_decimal(turnover_increase)
                and record.get("maximum_turnover_increase") == experiment["maximum_turnover_increase"]
                and record.get("primary_threshold_passed") is primary_passed
                and record.get("drawdown_constraint_passed") is drawdown_passed
                and record.get("turnover_constraint_passed") is turnover_passed
                and record.get("all_preregistered_criteria_met") is accepted
                and all(record.get(field) is False for field in fixed_false)
            )
            if not boundary:
                raise LedgerIntegrityError(f"Experiment-result record {index} violates its boundary.")
            seen_ids.add(expected_id)
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next((item for item in records if item["result_id"] == result["result_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash"}
                if allow_existing and {k: v for k, v in existing.items() if k not in ignored} == {k: v for k, v in result.items() if k not in ignored}:
                    return existing
                raise LedgerIntegrityError(f"Experiment result {result['result_id']} already exists.")
            material = {**result, "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH}
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                _write_all(target, (_canonical_json(record) + "\n").encode())
                os.fsync(target)
            finally:
                os.close(target)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
