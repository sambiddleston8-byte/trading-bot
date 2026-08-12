from __future__ import annotations

"""Immutable, inert manifests for reproducible sandbox experiment runs."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.experiment_specification import SandboxExperimentSpecificationLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


RUN_MANIFEST_SCHEMA_VERSION = "1.0"
RUN_MANIFEST_POLICY_VERSION = "isolated-reproducible-sandbox-run-manifest-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
ALLOWED_ENVIRONMENTS = {"LOCAL_ISOLATED_NO_NETWORK", "CONTAINER_ISOLATED_NO_NETWORK"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_REVISION = re.compile(r"^[0-9a-f]{40,64}$")


def _required(value: Any, name: str, *, maximum: int = 300) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


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


MANIFEST_ID_FIELDS = (
    "git_revision", "dataset_manifest_sha256", "dependency_lock_sha256",
    "runner_sha256", "execution_environment", "maximum_runtime_seconds",
    "maximum_memory_mb", "maximum_cpu_cores", "planned_trial_count",
)


def _manifest_id(experiment_id: str, planned_at: str, values: Mapping[str, Any]) -> str:
    material = [
        experiment_id,
        planned_at,
        {field: values.get(field) for field in MANIFEST_ID_FIELDS},
        RUN_MANIFEST_POLICY_VERSION,
    ]
    return "RUN-" + hashlib.sha256(_canonical_json(material).encode()).hexdigest()[:32].upper()


class SandboxExperimentRunManifestLedger:
    """Append-only run plans. Recording one cannot execute the experiment."""

    def __init__(self, path: str | Path, experiment_ledger: SandboxExperimentSpecificationLedger) -> None:
        self.path = Path(path)
        self.experiment_ledger = experiment_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Run-manifest ledger has an incomplete final line; run explicit tail repair.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank run-manifest line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(f"Invalid JSON at run-manifest line {line_number}.") from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(f"Run-manifest line {line_number} is not an object.")
                records.append(record)
        return records

    def plan(
        self, *, experiment_id: str, git_revision: str,
        dataset_manifest_sha256: str, dependency_lock_sha256: str,
        runner_sha256: str, execution_environment: str,
        maximum_runtime_seconds: int, maximum_memory_mb: int,
        maximum_cpu_cores: int, planned_trial_count: int,
        planned_by: str, planned_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        experiments = self.experiment_ledger.verify()
        experiment = next((item for item in experiments if item.get("experiment_id") == experiment_id), None)
        if experiment is None:
            raise ValueError("A verified preregistered experiment is required")
        revision = str(git_revision or "").strip().lower()
        if not _GIT_REVISION.fullmatch(revision):
            raise ValueError("git_revision must be a full hexadecimal commit identifier")
        environment = _required(execution_environment, "execution_environment", maximum=80).upper()
        if environment not in ALLOWED_ENVIRONMENTS:
            raise ValueError("execution_environment must be isolated with network disabled")
        planned = _timestamp(planned_at or datetime.now(timezone.utc))
        if planned > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("planned_at cannot be in the future")
        if planned < _timestamp(experiment["registered_at"]):
            raise ValueError("planned_at cannot predate experiment preregistration")
        trials = _positive_int(planned_trial_count, "planned_trial_count", 100)
        if trials > int(experiment["maximum_trials"]):
            raise ValueError("planned_trial_count exceeds the preregistered trial budget")
        values = {
            "git_revision": revision,
            "dataset_manifest_sha256": _hash(dataset_manifest_sha256, "dataset_manifest_sha256"),
            "dependency_lock_sha256": _hash(dependency_lock_sha256, "dependency_lock_sha256"),
            "runner_sha256": _hash(runner_sha256, "runner_sha256"),
            "execution_environment": environment,
            "maximum_runtime_seconds": _positive_int(maximum_runtime_seconds, "maximum_runtime_seconds", 86_400),
            "maximum_memory_mb": _positive_int(maximum_memory_mb, "maximum_memory_mb", 65_536),
            "maximum_cpu_cores": _positive_int(maximum_cpu_cores, "maximum_cpu_cores", 64),
            "planned_trial_count": trials,
        }
        result = {
            "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
            "policy_version": RUN_MANIFEST_POLICY_VERSION,
            "run_manifest_id": _manifest_id(experiment_id, planned.isoformat(), values),
            "record_type": "CONTROLLED_LEARNING_SANDBOX_EXPERIMENT_RUN_MANIFEST",
            "status": "PLANNED_NOT_EXECUTED",
            "simulation_only": True,
            "planned_at": planned.isoformat(),
            "planned_by": _required(planned_by, "planned_by", maximum=100),
            "experiment_id": experiment["experiment_id"],
            "experiment_record_hash": experiment["record_hash"],
            **values,
            "network_allowed": False,
            "provider_api_allowed": False,
            "broker_connection_allowed": False,
            "experiment_executed": False,
            "result_recorded": False,
            "shadow_test_started": False,
            "promotion_approved": False,
            "production_rule_changed": False,
            "deployment_performed": False,
            "order_submitted": False,
            "live_trading_enabled": False,
        }
        return self._append(result, allow_existing=allow_existing)

    def _pinned_experiment(self, record: Mapping[str, Any]):
        return resolve_pinned_records(
            self.experiment_ledger.verify(), [record.get("experiment_id")],
            [record.get("experiment_record_hash")], id_field="experiment_id", label="experiment",
        )

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Run-manifest record {index} has been modified.")
            experiments, reasons = self._pinned_experiment(record)
            if reasons or len(experiments) != 1:
                raise LedgerIntegrityError(f"Run-manifest record {index} lost its experiment.")
            experiment = experiments[0]
            try:
                planned = _timestamp(record.get("planned_at"))
                revision = str(record.get("git_revision") or "").strip().lower()
                if not _GIT_REVISION.fullmatch(revision):
                    raise ValueError("invalid revision")
                values = {
                    "git_revision": revision,
                    "dataset_manifest_sha256": _hash(record.get("dataset_manifest_sha256"), "dataset"),
                    "dependency_lock_sha256": _hash(record.get("dependency_lock_sha256"), "dependencies"),
                    "runner_sha256": _hash(record.get("runner_sha256"), "runner"),
                    "execution_environment": _required(record.get("execution_environment"), "environment", maximum=80).upper(),
                    "maximum_runtime_seconds": _positive_int(record.get("maximum_runtime_seconds"), "runtime", 86_400),
                    "maximum_memory_mb": _positive_int(record.get("maximum_memory_mb"), "memory", 65_536),
                    "maximum_cpu_cores": _positive_int(record.get("maximum_cpu_cores"), "cpu", 64),
                    "planned_trial_count": _positive_int(record.get("planned_trial_count"), "trials", 100),
                }
                _required(record.get("planned_by"), "planned_by", maximum=100)
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Run-manifest record {index} has invalid values.") from error
            expected_id = _manifest_id(experiment["experiment_id"], planned.isoformat(), values)
            fixed_false = (
                "network_allowed", "provider_api_allowed", "broker_connection_allowed",
                "experiment_executed", "result_recorded", "shadow_test_started",
                "promotion_approved", "production_rule_changed", "deployment_performed",
                "order_submitted", "live_trading_enabled",
            )
            boundary = (
                record.get("schema_version") == RUN_MANIFEST_SCHEMA_VERSION
                and record.get("policy_version") == RUN_MANIFEST_POLICY_VERSION
                and record.get("run_manifest_id") == expected_id
                and expected_id not in seen_ids
                and record.get("record_type") == "CONTROLLED_LEARNING_SANDBOX_EXPERIMENT_RUN_MANIFEST"
                and record.get("status") == "PLANNED_NOT_EXECUTED"
                and record.get("simulation_only") is True
                and record.get("experiment_id") == experiment["experiment_id"]
                and record.get("experiment_record_hash") == experiment["record_hash"]
                and planned >= _timestamp(experiment["registered_at"])
                and planned <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and values["execution_environment"] in ALLOWED_ENVIRONMENTS
                and values["planned_trial_count"] <= int(experiment["maximum_trials"])
                and all(record.get(field) is False for field in fixed_false)
            )
            if not boundary:
                raise LedgerIntegrityError(f"Run-manifest record {index} violates its boundary.")
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
            existing = next((item for item in records if item["run_manifest_id"] == result["run_manifest_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash"}
                if allow_existing and {k: v for k, v in existing.items() if k not in ignored} == {k: v for k, v in result.items() if k not in ignored}:
                    return existing
                raise LedgerIntegrityError(f"Run manifest {result['run_manifest_id']} already exists.")
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
                json.loads(tail.decode())
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
