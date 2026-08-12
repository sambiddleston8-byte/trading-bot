from __future__ import annotations

"""Immutable human-approved Hermes permissions and budgets; no agent is run."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
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
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


HERMES_POLICY_SCHEMA_VERSION = "1.0"
HERMES_POLICY_VERSION = "fail-closed-hermes-governance-v1"
MINIMUM_LEAD_TIME = timedelta(minutes=5)
MAX_CLOCK_SKEW = timedelta(minutes=5)
ALLOWED_ACTIONS = [
    "READ_VERIFIED_LOCAL_EVIDENCE",
    "WRITE_SANDBOX_LESSON_PROPOSAL",
    "WRITE_SANDBOX_EXPERIMENT_PROPOSAL",
    "REQUEST_HUMAN_REVIEW",
]
DENIED_ACTIONS = [
    "MODIFY_PRODUCTION_INVESTMENT_RULES",
    "PROMOTE_EXPERIMENT",
    "MERGE_PULL_REQUEST",
    "DEPLOY_SOFTWARE",
    "SUBMIT_BROKER_ORDER",
    "ACCESS_BROKER_CREDENTIALS",
    "ACCESS_AWS",
    "WRITE_GITHUB",
    "CHANGE_OWN_PERMISSIONS_OR_BUDGET",
    "DELETE_OR_REWRITE_EVIDENCE",
    "ENABLE_LIVE_TRADING",
]
LIMITS = {
    "max_scheduled_jobs_per_day": (1, 100),
    "max_job_duration_seconds": (60, 86400),
    "max_llm_calls_per_job": (0, 100),
    "max_subagents_per_job": (0, 20),
    "max_consecutive_failures": (1, 10),
}


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _bounded_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        resolved = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if str(resolved) != str(value).strip() and not (
        isinstance(value, float) and value.is_integer()
    ):
        raise ValueError(f"{name} must be an integer")
    minimum, maximum = LIMITS[name]
    if resolved < minimum or resolved > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return resolved


def _cost(value: Any) -> str:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("max_daily_ai_cost_usd must be a finite decimal") from error
    if not resolved.is_finite() or resolved < 0 or resolved > 1000:
        raise ValueError("max_daily_ai_cost_usd must be between 0 and 1000")
    return "0" if resolved == 0 else format(resolved.normalize(), "f")


def _models(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    resolved = [normalize_model_version(item) for item in values]
    if not resolved or any(
        not item["component"].strip() or not item["version"].strip()
        for item in resolved
    ):
        raise ValueError("model_versions require at least one named component and version")
    return resolved


def _policy_id(
    effective: str,
    git_revision: str,
    budgets: Mapping[str, int],
    max_daily_ai_cost_usd: str,
    emergency_stop_identifier: str,
) -> str:
    material = [
        "HERMES", effective, git_revision, dict(budgets), max_daily_ai_cost_usd,
        emergency_stop_identifier, HERMES_POLICY_VERSION,
    ]
    return "HMPOL-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class HermesPermissionPolicyLedger:
    """Append-only governance records; registration does not activate Hermes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Hermes-policy ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank Hermes-policy line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(f"Invalid JSON at Hermes-policy line {line_number}.") from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(f"Hermes-policy line {line_number} is not an object.")
                records.append(record)
        return records

    def preregister(
        self,
        *,
        effective_not_before: str | datetime,
        max_scheduled_jobs_per_day: int,
        max_job_duration_seconds: int,
        max_llm_calls_per_job: int,
        max_subagents_per_job: int,
        max_daily_ai_cost_usd: Any,
        max_consecutive_failures: int,
        emergency_stop_identifier: str,
        decided_by: str,
        decision_reference: str,
        human_decision_confirmed: bool,
        model_versions: Sequence[Mapping[str, Any]],
        git_revision: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        if human_decision_confirmed is not True:
            raise ValueError("An explicit human decision is required before preregistration")
        effective = _as_datetime(effective_not_before)
        recorded = _as_datetime(recorded_at or datetime.now(timezone.utc))
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        if effective < recorded + MINIMUM_LEAD_TIME:
            raise ValueError("effective_not_before must be at least five minutes after preregistration")
        budgets = {
            name: _bounded_int(value, name)
            for name, value in {
                "max_scheduled_jobs_per_day": max_scheduled_jobs_per_day,
                "max_job_duration_seconds": max_job_duration_seconds,
                "max_llm_calls_per_job": max_llm_calls_per_job,
                "max_subagents_per_job": max_subagents_per_job,
                "max_consecutive_failures": max_consecutive_failures,
            }.items()
        }
        models = _models(model_versions)
        git = _required(git_revision, "git_revision")
        resolved_cost = _cost(max_daily_ai_cost_usd)
        stop_identifier = _required(emergency_stop_identifier, "emergency_stop_identifier")
        record = {
            "schema_version": HERMES_POLICY_SCHEMA_VERSION,
            "policy_version": HERMES_POLICY_VERSION,
            "policy_id": _policy_id(
                effective.isoformat(), git, budgets, resolved_cost, stop_identifier
            ),
            "status": "PREREGISTERED_INACTIVE",
            "agent_id": "HERMES",
            "record_type": "HUMAN_APPROVED_HERMES_PERMISSION_AND_BUDGET_POLICY",
            "effective_not_before": effective.isoformat(),
            "recorded_at": recorded.isoformat(),
            "decided_by": _required(decided_by, "decided_by"),
            "decision_reference": _required(decision_reference, "decision_reference"),
            "human_decision_confirmed": True,
            "allowed_actions": list(ALLOWED_ACTIONS),
            "denied_actions": list(DENIED_ACTIONS),
            **budgets,
            "max_daily_ai_cost_usd": resolved_cost,
            "emergency_stop_identifier": stop_identifier,
            "emergency_stop_required": True,
            "default_runtime_state": "STOPPED",
            "scheduler_enabled": False,
            "network_access_enabled": False,
            "broker_access_enabled": False,
            "aws_access_enabled": False,
            "github_write_enabled": False,
            "production_rule_write_enabled": False,
            "self_activation_allowed": False,
            "self_permission_change_allowed": False,
            "automatic_experiment_promotion_allowed": False,
            "model_invocation_performed": False,
            "job_scheduled": False,
            "model_versions": models,
            "git_revision": git,
        }
        return self._append(record, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Hermes-policy record {index} has been modified.")
            try:
                effective = _as_datetime(record.get("effective_not_before"))
                recorded = _as_datetime(record.get("recorded_at"))
                budgets = {
                    name: _bounded_int(record.get(name), name) for name in LIMITS
                }
                cost = _cost(record.get("max_daily_ai_cost_usd"))
                models = _models(record.get("model_versions") or [])
                git = _required(record.get("git_revision"), "git_revision")
                _required(record.get("decided_by"), "decided_by")
                _required(record.get("decision_reference"), "decision_reference")
                stop_identifier = _required(
                    record.get("emergency_stop_identifier"), "emergency_stop_identifier"
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Hermes-policy record {index} has invalid values.") from error
            expected_id = _policy_id(
                effective.isoformat(), git, budgets, cost, stop_identifier
            )
            boundary = (
                record.get("schema_version") == HERMES_POLICY_SCHEMA_VERSION
                and record.get("policy_version") == HERMES_POLICY_VERSION
                and record.get("policy_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "PREREGISTERED_INACTIVE"
                and record.get("agent_id") == "HERMES"
                and record.get("record_type") == "HUMAN_APPROVED_HERMES_PERMISSION_AND_BUDGET_POLICY"
                and record.get("human_decision_confirmed") is True
                and effective >= recorded + MINIMUM_LEAD_TIME
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("allowed_actions") == ALLOWED_ACTIONS
                and record.get("denied_actions") == DENIED_ACTIONS
                and all(record.get(key) == value for key, value in budgets.items())
                and record.get("max_daily_ai_cost_usd") == cost
                and record.get("emergency_stop_required") is True
                and record.get("default_runtime_state") == "STOPPED"
                and all(record.get(field) is False for field in (
                    "scheduler_enabled", "network_access_enabled", "broker_access_enabled",
                    "aws_access_enabled", "github_write_enabled", "production_rule_write_enabled",
                    "self_activation_allowed", "self_permission_change_allowed",
                    "automatic_experiment_promotion_allowed", "model_invocation_performed",
                    "job_scheduled",
                ))
                and record.get("model_versions") == models
                and record.get("git_revision") == git
            )
            if not boundary:
                raise LedgerIntegrityError(f"Hermes-policy record {index} violates its boundary.")
            seen_ids.add(expected_id)
            previous_hash = record["record_hash"]
        return records

    def _append(self, policy: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next((item for item in records if item["policy_id"] == policy["policy_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in policy.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(f"Hermes policy {policy['policy_id']} already exists.")
            material = {**policy, "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH}
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
