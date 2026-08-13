from __future__ import annotations

"""Human-controlled Hermes activation and fail-closed job admission.

This module defines the boundary only. Importing it, approving a future window,
or checking a job does not schedule work or invoke a model.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.hermes_emergency_stop import HermesEmergencyStopLedger
from core.orchestration.hermes_policy import HermesPermissionPolicyLedger
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "human-controlled-hermes-activation-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
MINIMUM_LEAD_TIME = timedelta(minutes=5)
MAXIMUM_WINDOW = timedelta(days=31)
READ_ROOT_MARKER = "SAM_PAT_VERIFIED_LOCAL_EVIDENCE_ROOT_V1\n"
WRITE_ROOT_MARKER = "SAM_PAT_HERMES_SANDBOX_WRITE_ROOT_V1\n"
JOB_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
GIT_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
PERMITTED_JOB_ACTIONS = {
    "WRITE_SANDBOX_LESSON_PROPOSAL",
    "WRITE_SANDBOX_EXPERIMENT_PROPOSAL",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _required(value: Any, name: str, maximum: int = 500) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        resolved = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if str(resolved) != str(value).strip():
        raise ValueError(f"{name} must be an integer")
    if not minimum <= resolved <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return resolved


def _money(value: Any, name: str, maximum: Decimal) -> str:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite() or resolved < 0 or resolved > maximum:
        raise ValueError(f"{name} exceeds its approved bound")
    return "0" if resolved == 0 else format(resolved.normalize(), "f")


def _unique_strings(
    values: Sequence[Any], name: str, *, maximum_items: int, maximum_length: int = 300
) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be a sequence")
    resolved = [_required(value, name, maximum_length) for value in values]
    if not resolved or len(resolved) > maximum_items or len(set(resolved)) != len(resolved):
        raise ValueError(f"{name} must contain unique bounded values")
    return sorted(resolved)


def _root_values(values: Sequence[Any], name: str) -> list[str]:
    resolved = _unique_strings(values, name, maximum_items=20)
    if resolved == ["NONE"]:
        return resolved
    if any(
        not Path(value).is_absolute()
        or os.path.normpath(value) != value
        or any(ord(character) < 32 for character in value)
        for value in resolved
    ):
        raise ValueError(f"{name} must contain canonical absolute paths")
    return resolved


def _existing_roots(
    values: Sequence[Any], name: str, *, marker_name: str, marker_value: str
) -> list[str]:
    resolved = _root_values(values, name)
    if resolved == ["NONE"]:
        return resolved
    paths = [Path(value) for value in resolved]
    if any(
        path.is_symlink() or not path.is_dir()
        or path.resolve() != path
        for path in paths
    ):
        raise ValueError(f"{name} must contain existing canonical non-symlink directories")
    for path in paths:
        marker = path / marker_name
        if (
            marker.is_symlink() or not marker.is_file()
            or marker.read_text(encoding="utf-8") != marker_value
        ):
            raise ValueError(f"{name} is missing its exact safety marker")
    return resolved


def _roots_overlap(reads: Sequence[str], writes: Sequence[str]) -> bool:
    if reads == ["NONE"] or writes == ["NONE"]:
        return False
    for read in map(Path, reads):
        for write in map(Path, writes):
            if read == write or read in write.parents or write in read.parents:
                return True
    return False


def _activation_id(material: Mapping[str, Any]) -> str:
    return "HMACT-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class HermesActivationLedger:
    """Append-only, human-approved activation windows; no scheduling capability."""

    def __init__(
        self,
        path: str | Path,
        policy_ledger: HermesPermissionPolicyLedger,
        stop_ledger: HermesEmergencyStopLedger,
    ) -> None:
        self.path = Path(path)
        self.policy_ledger = policy_ledger
        self.stop_ledger = stop_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Hermes-activation ledger has an incomplete final line.")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank Hermes-activation line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid Hermes-activation JSON at line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Hermes-activation line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def approve(
        self,
        *,
        policy_id: str,
        active_from: str | datetime,
        active_until: str | datetime,
        allowed_jobs: Sequence[str],
        job_action: str,
        max_concurrent_jobs: int,
        max_job_duration_seconds: int,
        max_llm_calls_per_job: int,
        max_ai_cost_per_job_usd: Any,
        max_simulated_portfolio_exposure_usd: Any,
        stale_heartbeat_seconds: int,
        permitted_read_roots: Sequence[str],
        permitted_write_roots: Sequence[str],
        permitted_endpoints: Sequence[str],
        permitted_credential_names: Sequence[str],
        git_revision: str,
        approved_by: str,
        approval_reference: str,
        human_activation_confirmed: bool,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        if human_activation_confirmed is not True:
            raise ValueError("Explicit human activation approval is required")
        policies = self.policy_ledger.verify()
        policy = next((item for item in policies if item["policy_id"] == policy_id), None)
        if policy is None:
            raise ValueError("A verified Hermes policy is required")
        if any(item["policy_id"] == policy_id for item in self.stop_ledger.verify()):
            raise ValueError("A stopped Hermes policy cannot be activated")
        recorded = _timestamp(recorded_at or _now())
        start = _timestamp(active_from)
        end = _timestamp(active_until)
        now = _now()
        if not now - MAX_CLOCK_SKEW <= recorded <= now + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at must match the actual approval time")
        if start < recorded + MINIMUM_LEAD_TIME:
            raise ValueError("active_from must be at least five minutes after approval")
        if start < _timestamp(policy["effective_not_before"]):
            raise ValueError("activation cannot predate its policy")
        if end <= start or end - start > MAXIMUM_WINDOW:
            raise ValueError("activation window must be positive and no longer than 31 days")
        jobs = _unique_strings(allowed_jobs, "allowed_jobs", maximum_items=20, maximum_length=80)
        if any(not JOB_NAME.fullmatch(job) for job in jobs):
            raise ValueError("allowed_jobs contain an invalid job name")
        action = _required(job_action, "job_action", 80).upper()
        if action not in PERMITTED_JOB_ACTIONS or action not in policy["allowed_actions"]:
            raise ValueError("job_action is not permitted by the Hermes policy")
        concurrency = _integer(max_concurrent_jobs, "max_concurrent_jobs", 1, 10)
        if concurrency > policy["max_scheduled_jobs_per_day"]:
            raise ValueError("max_concurrent_jobs exceeds the daily policy budget")
        duration = _integer(
            max_job_duration_seconds, "max_job_duration_seconds", 1,
            policy["max_job_duration_seconds"],
        )
        calls = _integer(
            max_llm_calls_per_job, "max_llm_calls_per_job", 0,
            policy["max_llm_calls_per_job"],
        )
        daily_cost = Decimal(policy["max_daily_ai_cost_usd"])
        per_job_cost = _money(max_ai_cost_per_job_usd, "max_ai_cost_per_job_usd", daily_cost)
        exposure = _money(
            max_simulated_portfolio_exposure_usd,
            "max_simulated_portfolio_exposure_usd", Decimal("1000000000"),
        )
        heartbeat = _integer(
            stale_heartbeat_seconds, "stale_heartbeat_seconds", 5, duration,
        )
        reads = _existing_roots(
            permitted_read_roots, "permitted_read_roots",
            marker_name=".verified-local-evidence-root", marker_value=READ_ROOT_MARKER,
        )
        writes = _existing_roots(
            permitted_write_roots, "permitted_write_roots",
            marker_name=".hermes-sandbox-write-root", marker_value=WRITE_ROOT_MARKER,
        )
        if _roots_overlap(reads, writes):
            raise ValueError("permitted read and write roots cannot overlap")
        endpoints = _unique_strings(permitted_endpoints, "permitted_endpoints", maximum_items=20)
        credentials = _unique_strings(
            permitted_credential_names, "permitted_credential_names", maximum_items=20,
        )
        if endpoints != ["NONE"] or credentials != ["NONE"]:
            raise ValueError("initial Hermes activation is local-only with no endpoints or credentials")
        git = _required(git_revision, "git_revision", 64).lower()
        if not GIT_OBJECT_ID.fullmatch(git):
            raise ValueError("git_revision must be a full 40- or 64-character Git object ID")
        boundary = {
            "policy_id": policy["policy_id"],
            "policy_record_hash": policy["record_hash"],
            "active_from": start.isoformat(),
            "active_until": end.isoformat(),
            "allowed_jobs": jobs,
            "job_action": action,
            "max_concurrent_jobs": concurrency,
            "max_job_duration_seconds": duration,
            "max_llm_calls_per_job": calls,
            "max_ai_cost_per_job_usd": per_job_cost,
            "max_simulated_portfolio_exposure_usd": exposure,
            "stale_heartbeat_seconds": heartbeat,
            "permitted_read_roots": reads,
            "permitted_write_roots": writes,
            "permitted_endpoints": endpoints,
            "permitted_credential_names": credentials,
            "git_revision": git,
        }
        record = {
            "schema_version": SCHEMA_VERSION,
            "activation_policy_version": POLICY_VERSION,
            "activation_id": _activation_id(boundary),
            "record_type": "HUMAN_APPROVED_HERMES_ACTIVATION_WINDOW",
            "status": "APPROVED_FUTURE_BOUNDED_WINDOW",
            "agent_id": "HERMES",
            "recorded_at": recorded.isoformat(),
            "approved_by": _required(approved_by, "approved_by", 100),
            "approval_reference": _required(approval_reference, "approval_reference"),
            "human_activation_confirmed": True,
            **boundary,
            "scheduler_created": False,
            "job_started": False,
            "network_access_allowed": False,
            "broker_access_allowed": False,
            "aws_access_allowed": False,
            "github_write_allowed": False,
            "production_rule_write_allowed": False,
            "promotion_allowed": False,
            "order_submission_allowed": False,
            "live_trading_enabled": False,
            "self_extension_allowed": False,
            "self_budget_change_allowed": False,
        }
        return self._append(record, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous = GENESIS_HASH
        seen: set[str] = set()
        policies = {item["policy_id"]: item for item in self.policy_ledger.verify()}
        self.stop_ledger.verify()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Hermes-activation record {index} was modified.")
            policy = policies.get(record.get("policy_id"))
            try:
                start = _timestamp(record.get("active_from"))
                end = _timestamp(record.get("active_until"))
                recorded = _timestamp(record.get("recorded_at"))
                jobs = _unique_strings(record.get("allowed_jobs") or [], "allowed_jobs", maximum_items=20, maximum_length=80)
                reads = _root_values(record.get("permitted_read_roots") or [], "permitted_read_roots")
                writes = _root_values(record.get("permitted_write_roots") or [], "permitted_write_roots")
                endpoints = _unique_strings(record.get("permitted_endpoints") or [], "permitted_endpoints", maximum_items=20)
                credentials = _unique_strings(record.get("permitted_credential_names") or [], "permitted_credential_names", maximum_items=20)
                per_job_cost = _money(
                    record.get("max_ai_cost_per_job_usd"),
                    "max_ai_cost_per_job_usd", Decimal(policy["max_daily_ai_cost_usd"]),
                ) if policy is not None else ""
                exposure = _money(
                    record.get("max_simulated_portfolio_exposure_usd"),
                    "max_simulated_portfolio_exposure_usd", Decimal("1000000000"),
                )
                concurrency = _integer(
                    record.get("max_concurrent_jobs"), "max_concurrent_jobs", 1,
                    min(10, policy["max_scheduled_jobs_per_day"]),
                ) if policy is not None else -1
                duration = _integer(
                    record.get("max_job_duration_seconds"),
                    "max_job_duration_seconds", 1,
                    policy["max_job_duration_seconds"],
                ) if policy is not None else -1
                calls = _integer(
                    record.get("max_llm_calls_per_job"), "max_llm_calls_per_job", 0,
                    policy["max_llm_calls_per_job"],
                ) if policy is not None else -1
                heartbeat = _integer(
                    record.get("stale_heartbeat_seconds"),
                    "stale_heartbeat_seconds", 5, duration,
                ) if duration > 0 else -1
                _required(record.get("approved_by"), "approved_by", 100)
                _required(record.get("approval_reference"), "approval_reference")
            except (InvalidOperation, TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Hermes-activation record {index} has invalid values.") from error
            boundary = {
                key: record.get(key) for key in (
                    "policy_id", "policy_record_hash", "active_from", "active_until",
                    "allowed_jobs", "job_action", "max_concurrent_jobs",
                    "max_job_duration_seconds", "max_llm_calls_per_job",
                    "max_ai_cost_per_job_usd", "max_simulated_portfolio_exposure_usd",
                    "stale_heartbeat_seconds", "permitted_read_roots",
                    "permitted_write_roots", "permitted_endpoints",
                    "permitted_credential_names", "git_revision",
                )
            }
            fixed_false = (
                "scheduler_created", "job_started", "network_access_allowed",
                "broker_access_allowed", "aws_access_allowed", "github_write_allowed",
                "production_rule_write_allowed", "promotion_allowed",
                "order_submission_allowed", "live_trading_enabled",
                "self_extension_allowed", "self_budget_change_allowed",
            )
            valid = (
                policy is not None
                and record.get("schema_version") == SCHEMA_VERSION
                and record.get("activation_policy_version") == POLICY_VERSION
                and record.get("activation_id") == _activation_id(boundary)
                and record.get("activation_id") not in seen
                and record.get("policy_record_hash") == policy["record_hash"]
                and record.get("record_type") == "HUMAN_APPROVED_HERMES_ACTIVATION_WINDOW"
                and record.get("status") == "APPROVED_FUTURE_BOUNDED_WINDOW"
                and record.get("agent_id") == "HERMES"
                and record.get("human_activation_confirmed") is True
                and recorded <= _now() + MAX_CLOCK_SKEW
                and start >= recorded + MINIMUM_LEAD_TIME
                and start >= _timestamp(policy["effective_not_before"])
                and start < end <= start + MAXIMUM_WINDOW
                and jobs == record.get("allowed_jobs")
                and all(JOB_NAME.fullmatch(job) for job in jobs)
                and record.get("job_action") in PERMITTED_JOB_ACTIONS
                and record.get("job_action") in policy["allowed_actions"]
                and record.get("max_concurrent_jobs") == concurrency
                and record.get("max_job_duration_seconds") == duration
                and record.get("max_llm_calls_per_job") == calls
                and record.get("max_ai_cost_per_job_usd") == per_job_cost
                and record.get("max_simulated_portfolio_exposure_usd") == exposure
                and record.get("stale_heartbeat_seconds") == heartbeat
                and reads == record.get("permitted_read_roots")
                and writes == record.get("permitted_write_roots")
                and not _roots_overlap(reads, writes)
                and endpoints == ["NONE"] and credentials == ["NONE"]
                and GIT_OBJECT_ID.fullmatch(str(record.get("git_revision") or ""))
                and all(record.get(field) is False for field in fixed_false)
            )
            if not valid:
                raise LedgerIntegrityError(f"Hermes-activation record {index} violates its boundary.")
            seen.add(record["activation_id"])
            previous = record["record_hash"]
        return records

    def admission(
        self, *, activation_id: str, job_name: str, git_revision: str,
        requested_duration_seconds: int, requested_llm_calls: int,
        requested_ai_cost_usd: Any, requested_simulated_exposure_usd: Any,
        now: str | datetime | None = None,
    ) -> dict[str, Any]:
        current = _timestamp(now or _now())
        actual = _now()
        if not actual - MAX_CLOCK_SKEW <= current <= actual + MAX_CLOCK_SKEW:
            return self._denied("ADMISSION_TIME_NOT_CURRENT")
        activation = next(
            (item for item in self.verify() if item["activation_id"] == activation_id), None
        )
        if activation is None:
            return self._denied("UNKNOWN_ACTIVATION")
        if any(
            item["policy_id"] == activation["policy_id"]
            for item in self.stop_ledger.verify()
        ):
            return self._denied("EMERGENCY_STOP_LATCHED")
        try:
            _existing_roots(
                activation["permitted_read_roots"], "permitted_read_roots",
                marker_name=".verified-local-evidence-root",
                marker_value=READ_ROOT_MARKER,
            )
            _existing_roots(
                activation["permitted_write_roots"], "permitted_write_roots",
                marker_name=".hermes-sandbox-write-root",
                marker_value=WRITE_ROOT_MARKER,
            )
        except (OSError, ValueError):
            return self._denied("PERMITTED_ROOT_UNAVAILABLE")
        if not _timestamp(activation["active_from"]) <= current < _timestamp(activation["active_until"]):
            return self._denied("OUTSIDE_APPROVED_WINDOW")
        try:
            duration = _integer(
                requested_duration_seconds, "requested_duration_seconds", 1,
                activation["max_job_duration_seconds"],
            )
            calls = _integer(
                requested_llm_calls, "requested_llm_calls", 0,
                activation["max_llm_calls_per_job"],
            )
            cost = _money(
                requested_ai_cost_usd, "requested_ai_cost_usd",
                Decimal(activation["max_ai_cost_per_job_usd"]),
            )
            exposure = _money(
                requested_simulated_exposure_usd, "requested_simulated_exposure_usd",
                Decimal(activation["max_simulated_portfolio_exposure_usd"]),
            )
        except (TypeError, ValueError):
            return self._denied("INVALID_OR_EXCESSIVE_RESOURCE_REQUEST")
        checks = (
            (job_name in activation["allowed_jobs"], "JOB_NOT_ALLOWED"),
            (str(git_revision).lower() == activation["git_revision"], "GIT_REVISION_MISMATCH"),
            (
                current + timedelta(seconds=duration) <= _timestamp(activation["active_until"]),
                "JOB_WOULD_OUTLIVE_APPROVED_WINDOW",
            ),
        )
        for passed, reason in checks:
            if not passed:
                return self._denied(reason)
        return {
            "state": "ADMISSION_CHECK_PASSED_NOT_SCHEDULED",
            "work_allowed": True,
            "activation_id": activation["activation_id"],
            "policy_id": activation["policy_id"],
            "job_name": job_name,
            "deadline": (
                current + timedelta(seconds=duration)
            ).isoformat(),
            "approved_llm_calls": calls,
            "approved_ai_cost_usd": cost,
            "approved_simulated_exposure_usd": exposure,
            "stale_heartbeat_seconds": activation["stale_heartbeat_seconds"],
            "permitted_read_roots": activation["permitted_read_roots"],
            "permitted_write_roots": activation["permitted_write_roots"],
            "permitted_endpoints": ["NONE"],
            "permitted_credential_names": ["NONE"],
            "network_access_allowed": False,
            "broker_access_allowed": False,
            "aws_access_allowed": False,
            "github_write_allowed": False,
            "promotion_allowed": False,
            "order_submission_allowed": False,
            "live_trading_enabled": False,
            "job_started": False,
            "scheduler_created": False,
        }

    @staticmethod
    def _denied(reason: str) -> dict[str, Any]:
        return {
            "state": "STOPPED",
            "work_allowed": False,
            "reason": reason,
            "job_started": False,
            "scheduler_created": False,
            "network_access_allowed": False,
            "broker_access_allowed": False,
            "aws_access_allowed": False,
            "github_write_allowed": False,
            "promotion_allowed": False,
            "order_submission_allowed": False,
            "live_trading_enabled": False,
        }

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path.with_suffix(self.path.suffix + ".lock"),
            os.O_CREAT | os.O_RDWR, 0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["activation_id"] == result["activation_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                if allow_existing and (
                    {key: value for key, value in existing.items() if key not in ignored}
                    == {key: value for key, value in result.items() if key not in ignored}
                ):
                    return existing
                raise LedgerIntegrityError("Hermes activation already exists.")
            proposed_start = _timestamp(result["active_from"])
            proposed_end = _timestamp(result["active_until"])
            if any(
                item["policy_id"] == result["policy_id"]
                and proposed_start < _timestamp(item["active_until"])
                and _timestamp(item["active_from"]) < proposed_end
                for item in records
            ):
                raise LedgerIntegrityError(
                    "Hermes policy already has an overlapping activation window."
                )
            material = {
                **result,
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
