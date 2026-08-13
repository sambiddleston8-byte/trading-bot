from __future__ import annotations

"""Append-only Hermes job reservations and lifecycle accounting; runs no work."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.hermes_activation import HermesActivationLedger
from core.orchestration.hermes_emergency_stop import HermesEmergencyStopLedger
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "hermes-worker-lifecycle-accounting-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
ACTIVE_STATES = {"RESERVED_NOT_STARTED", "STARTED", "HEARTBEAT"}
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "QUARANTINED"}
ALL_STATES = ACTIVE_STATES | TERMINAL_STATES
SHA256 = __import__("re").compile(r"^[0-9a-f]{64}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: Any) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _required(value: Any, name: str, maximum: int = 500) -> str:
    resolved = str(value or "").strip()
    if not resolved or len(resolved) > maximum:
        raise ValueError(f"{name} is required and must not exceed {maximum} characters")
    return resolved


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        resolved = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if str(resolved) != str(value).strip() or not minimum <= resolved <= maximum:
        raise ValueError(f"{name} is outside its permitted range")
    return resolved


def _money(value: Any, name: str, maximum: Decimal) -> str:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite() or resolved < 0 or resolved > maximum:
        raise ValueError(f"{name} is outside its permitted range")
    return "0" if resolved == 0 else format(resolved.normalize(), "f")


def _run_id(activation_id: str, request_id: str) -> str:
    return "HMRUN-" + hashlib.sha256(
        _canonical_json([activation_id, request_id, POLICY_VERSION]).encode()
    ).hexdigest()[:32].upper()


class HermesWorkerLifecycleLedger:
    """Reserves and meters bounded work but never schedules or executes it."""

    def __init__(
        self,
        path: str | Path,
        activation_ledger: HermesActivationLedger,
        stop_ledger: HermesEmergencyStopLedger,
    ) -> None:
        self.path = Path(path)
        self.activation_ledger = activation_ledger
        self.stop_ledger = stop_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Hermes lifecycle ledger has an incomplete final line.")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank Hermes lifecycle line {line_number}.")
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid Hermes lifecycle JSON at line {line_number}."
                    ) from error
                if not isinstance(value, dict):
                    raise LedgerIntegrityError(
                        f"Hermes lifecycle line {line_number} is not an object."
                    )
                records.append(value)
        return records

    def verify(self) -> list[dict[str, Any]]:
        activations = {
            item["activation_id"]: item for item in self.activation_ledger.verify()
        }
        previous = GENESIS_HASH
        latest: dict[str, dict[str, Any]] = {}
        seen_requests: set[tuple[str, str]] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Hermes lifecycle record {index} was modified.")
            activation = activations.get(record.get("activation_id"))
            try:
                event_at = _timestamp(record.get("event_at"))
                run_id = _required(record.get("run_id"), "run_id", 80)
                state = _required(record.get("state"), "state", 40)
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Hermes lifecycle record {index} has invalid values."
                ) from error
            prior = latest.get(run_id)
            common = (
                activation is not None
                and record.get("schema_version") == SCHEMA_VERSION
                and record.get("lifecycle_policy_version") == POLICY_VERSION
                and record.get("record_type") == "HERMES_WORKER_LIFECYCLE_EVENT"
                and record.get("activation_record_hash") == activation["record_hash"]
                and state in ALL_STATES
                and event_at <= _now() + MAX_CLOCK_SKEW
                and record.get("scheduler_created") is False
                and record.get("work_executed_by_ledger") is False
                and all(record.get(field) is False for field in (
                    "network_access_allowed", "broker_access_allowed", "aws_access_allowed",
                    "github_write_allowed", "promotion_allowed", "order_submission_allowed",
                    "live_trading_enabled",
                ))
            )
            valid = False
            if state == "RESERVED_NOT_STARTED":
                request_id = _required(record.get("request_id"), "request_id", 200)
                key = (activation["activation_id"], request_id) if activation else ("", "")
                try:
                    duration = _integer(
                        record.get("reserved_duration_seconds"), "duration", 1,
                        activation["max_job_duration_seconds"] if activation else 0,
                    )
                    calls = _integer(
                        record.get("reserved_llm_calls"), "calls", 0,
                        activation["max_llm_calls_per_job"] if activation else 0,
                    )
                    cost = _money(
                        record.get("reserved_ai_cost_usd"), "cost",
                        Decimal(activation["max_ai_cost_per_job_usd"]) if activation else Decimal(0),
                    )
                    exposure = _money(
                        record.get("reserved_simulated_exposure_usd"), "exposure",
                        Decimal(activation["max_simulated_portfolio_exposure_usd"]) if activation else Decimal(0),
                    )
                    deadline = _timestamp(record.get("deadline"))
                except (TypeError, ValueError) as error:
                    raise LedgerIntegrityError(
                        f"Hermes reservation {index} has invalid resources."
                    ) from error
                valid = (
                    prior is None and key not in seen_requests
                    and run_id == _run_id(activation["activation_id"], request_id)
                    and record.get("job_name") in activation["allowed_jobs"]
                    and record.get("git_revision") == activation["git_revision"]
                    and _timestamp(activation["active_from"]) <= event_at < _timestamp(activation["active_until"])
                    and deadline == event_at + timedelta(seconds=duration)
                    and deadline <= _timestamp(activation["active_until"])
                    and record.get("reserved_duration_seconds") == duration
                    and record.get("reserved_llm_calls") == calls
                    and record.get("reserved_ai_cost_usd") == cost
                    and record.get("reserved_simulated_exposure_usd") == exposure
                    and record.get("job_started") is False
                    and record.get("previous_run_event_hash") == GENESIS_HASH
                )
                seen_requests.add(key)
            elif prior is not None:
                allowed = {
                    "RESERVED_NOT_STARTED": {"STARTED", "QUARANTINED"},
                    "STARTED": {"HEARTBEAT", "SUCCEEDED", "FAILED", "QUARANTINED"},
                    "HEARTBEAT": {"HEARTBEAT", "SUCCEEDED", "FAILED", "QUARANTINED"},
                }.get(prior["state"], set())
                valid = (
                    state in allowed
                    and record.get("request_id") == prior["request_id"]
                    and record.get("job_name") == prior["job_name"]
                    and record.get("git_revision") == prior["git_revision"]
                    and record.get("previous_run_event_hash") == prior["record_hash"]
                    and event_at >= _timestamp(prior["event_at"])
                    and record.get("job_started") is (state != "QUARANTINED" or prior["state"] != "RESERVED_NOT_STARTED")
                )
                if state in TERMINAL_STATES:
                    try:
                        actual_calls = _integer(
                            record.get("actual_llm_calls"), "actual calls", 0,
                            self._reservation(records, run_id)["reserved_llm_calls"],
                        )
                        actual_cost = _money(
                            record.get("actual_ai_cost_usd"), "actual cost",
                            Decimal(self._reservation(records, run_id)["reserved_ai_cost_usd"]),
                        )
                    except (TypeError, ValueError) as error:
                        raise LedgerIntegrityError(
                            f"Hermes terminal record {index} has invalid usage."
                        ) from error
                    success_material_valid = (
                        state == "SUCCEEDED"
                        and SHA256.fullmatch(str(record.get("output_sha256") or ""))
                        and record.get("failure_category") == "NONE"
                    )
                    failure_material_valid = (
                        state in {"FAILED", "QUARANTINED"}
                        and record.get("output_sha256") == "NONE"
                        and bool(str(record.get("failure_category") or "").strip())
                        and record.get("failure_category") != "NONE"
                    )
                    valid = (
                        valid
                        and record.get("actual_llm_calls") == actual_calls
                        and record.get("actual_ai_cost_usd") == actual_cost
                        and (success_material_valid or failure_material_valid)
                    )
            if not (common and valid):
                raise LedgerIntegrityError(f"Hermes lifecycle record {index} violates its boundary.")
            latest[run_id] = record
            previous = record["record_hash"]
        return records

    def reserve(
        self, *, activation_id: str, request_id: str, job_name: str,
        git_revision: str, requested_duration_seconds: int,
        requested_llm_calls: int, requested_ai_cost_usd: Any,
        requested_simulated_exposure_usd: Any,
        requested_at: str | datetime | None = None,
    ) -> dict[str, Any]:
        event_at = _timestamp(requested_at or _now())
        admission = self.activation_ledger.admission(
            activation_id=activation_id, job_name=job_name, git_revision=git_revision,
            requested_duration_seconds=requested_duration_seconds,
            requested_llm_calls=requested_llm_calls,
            requested_ai_cost_usd=requested_ai_cost_usd,
            requested_simulated_exposure_usd=requested_simulated_exposure_usd,
            now=event_at,
        )
        if admission.get("work_allowed") is not True:
            raise RuntimeError("Hermes job admission denied: " + str(admission.get("reason")))
        request = _required(request_id, "request_id", 200)
        run_id = _run_id(activation_id, request)
        return self._locked_reserve(run_id, request, admission, event_at)

    def start(self, run_id: str, *, started_at: str | datetime | None = None) -> dict[str, Any]:
        return self._transition(run_id, "STARTED", started_at or _now())

    def heartbeat(self, run_id: str, *, heartbeat_at: str | datetime | None = None) -> dict[str, Any]:
        return self._transition(run_id, "HEARTBEAT", heartbeat_at or _now())

    def finish(
        self, run_id: str, *, succeeded: bool, actual_llm_calls: int,
        actual_ai_cost_usd: Any, output_sha256: str | None = None,
        failure_category: str | None = None,
        finished_at: str | datetime | None = None,
    ) -> dict[str, Any]:
        state = "SUCCEEDED" if succeeded else "FAILED"
        extra = {
            "actual_llm_calls": _integer(
                actual_llm_calls, "actual_llm_calls", 0,
                self._reservation(self.verify(), run_id)["reserved_llm_calls"],
            ),
            "actual_ai_cost_usd": _money(
                actual_ai_cost_usd, "actual_ai_cost_usd",
                Decimal(self._reservation(self.verify(), run_id)["reserved_ai_cost_usd"]),
            ),
            "output_sha256": _required(output_sha256, "output_sha256", 64) if succeeded else "NONE",
            "failure_category": "NONE" if succeeded else _required(failure_category, "failure_category", 100),
        }
        if succeeded and not SHA256.fullmatch(extra["output_sha256"]):
            raise ValueError("output_sha256 must be a lowercase SHA-256 digest")
        return self._transition(run_id, state, finished_at or _now(), extra=extra)

    def enforce(self, *, checked_at: str | datetime | None = None) -> dict[str, Any]:
        current = _timestamp(checked_at or _now())
        actual = _now()
        if not actual - MAX_CLOCK_SKEW <= current <= actual + MAX_CLOCK_SKEW:
            raise ValueError("enforcement time must match actual time")
        with self._lock():
            records = self.verify()
            latest = self._latest(records)
            stopped = self.stop_ledger.verify()
            if stopped:
                return {
                    "state": "STOPPED_LATCHED", "emergency_stop_latched": True,
                    "reason": "EMERGENCY_STOP_ALREADY_LATCHED",
                    "stop_id": stopped[-1]["stop_id"],
                }
            violation = self._violation(latest, records, current)
            if violation is None:
                return {"state": "WITHIN_LIMITS", "emergency_stop_latched": False}
            run, reason = violation
            activation = self._activation(run["activation_id"])
            policy = self._policy(activation["policy_id"])
            # Latch the independent stop first: a later lifecycle-log failure must
            # never leave a violating worker authorised to continue.
            stop = self.stop_ledger.trigger(
                policy_id=policy["policy_id"],
                emergency_stop_identifier=policy["emergency_stop_identifier"],
                trigger_source="SYSTEM_FAILURE_GUARD", reason=reason,
                triggered_by="HermesWorkerLifecycleLedger", triggered_at=current,
            )
            if run["state"] in ACTIVE_STATES:
                self._append_locked(records, self._event(run, "QUARANTINED", current, {
                    "actual_llm_calls": 0, "actual_ai_cost_usd": "0",
                    "output_sha256": "NONE", "failure_category": reason,
                }))
            return {
                "state": "STOPPED_LATCHED", "emergency_stop_latched": True,
                "reason": reason, "stop_id": stop["stop_id"],
            }

    def _locked_reserve(
        self, run_id: str, request_id: str, admission: Mapping[str, Any], event_at: datetime
    ) -> dict[str, Any]:
        with self._lock():
            records = self.verify()
            existing = next((item for item in records if item["run_id"] == run_id), None)
            if existing:
                reservation = self._reservation(records, run_id)
                same = (
                    reservation["request_id"] == request_id
                    and reservation["job_name"] == admission["job_name"]
                    and reservation["git_revision"] == self._activation(admission["activation_id"])["git_revision"]
                    and reservation["deadline"] == admission["deadline"]
                    and reservation["reserved_llm_calls"] == admission["approved_llm_calls"]
                    and reservation["reserved_ai_cost_usd"] == admission["approved_ai_cost_usd"]
                    and reservation["reserved_simulated_exposure_usd"] == admission["approved_simulated_exposure_usd"]
                )
                if same:
                    return reservation
                raise LedgerIntegrityError("Hermes request ID was reused with different work.")
            activation = self._activation(admission["activation_id"])
            policy = self._policy(activation["policy_id"])
            if any(item["policy_id"] == policy["policy_id"] for item in self.stop_ledger.verify()):
                raise RuntimeError("Hermes emergency stop is latched")
            latest = self._latest(records)
            if self._violation(latest, records, event_at):
                raise RuntimeError("Hermes lifecycle requires emergency-stop enforcement")
            today = event_at.date()
            reservations = [
                item for item in records
                if item["state"] == "RESERVED_NOT_STARTED"
                and _timestamp(item["event_at"]).date() == today
                and self._activation(item["activation_id"])["policy_id"] == policy["policy_id"]
            ]
            if len(reservations) >= policy["max_scheduled_jobs_per_day"]:
                raise RuntimeError("Hermes daily job limit reached")
            reserved_cost = sum(Decimal(item["reserved_ai_cost_usd"]) for item in reservations)
            requested_cost = Decimal(admission["approved_ai_cost_usd"])
            if reserved_cost + requested_cost > Decimal(policy["max_daily_ai_cost_usd"]):
                raise RuntimeError("Hermes daily AI cost limit reached")
            active = [item for item in latest.values() if item["state"] in ACTIVE_STATES]
            if len(active) >= activation["max_concurrent_jobs"]:
                raise RuntimeError("Hermes concurrency limit reached")
            result = {
                "schema_version": SCHEMA_VERSION,
                "lifecycle_policy_version": POLICY_VERSION,
                "record_type": "HERMES_WORKER_LIFECYCLE_EVENT",
                "state": "RESERVED_NOT_STARTED", "run_id": run_id,
                "request_id": request_id, "activation_id": activation["activation_id"],
                "activation_record_hash": activation["record_hash"],
                "job_name": admission["job_name"], "git_revision": activation["git_revision"],
                "event_at": event_at.isoformat(), "deadline": admission["deadline"],
                "reserved_duration_seconds": int(
                    (_timestamp(admission["deadline"]) - event_at).total_seconds()
                ),
                "reserved_llm_calls": admission["approved_llm_calls"],
                "reserved_ai_cost_usd": admission["approved_ai_cost_usd"],
                "reserved_simulated_exposure_usd": admission["approved_simulated_exposure_usd"],
                "previous_run_event_hash": GENESIS_HASH, "job_started": False,
                **self._fixed_false(),
            }
            return self._append_locked(records, result)

    def _transition(
        self, run_id: str, state: str, at: str | datetime,
        *, extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = _timestamp(at)
        if not _now() - MAX_CLOCK_SKEW <= current <= _now() + MAX_CLOCK_SKEW:
            raise ValueError("lifecycle event time must match actual time")
        with self._lock():
            records = self.verify()
            prior = self._latest(records).get(run_id)
            if prior is None or prior["state"] in TERMINAL_STATES:
                raise RuntimeError("Hermes run is unknown or already terminal")
            if any(item["policy_id"] == self._activation(prior["activation_id"])["policy_id"] for item in self.stop_ledger.verify()):
                raise RuntimeError("Hermes emergency stop is latched")
            activation = self._activation(prior["activation_id"])
            permitted = {
                "STARTED": {"RESERVED_NOT_STARTED"},
                "HEARTBEAT": {"STARTED", "HEARTBEAT"},
                "SUCCEEDED": {"STARTED", "HEARTBEAT"},
                "FAILED": {"STARTED", "HEARTBEAT"},
            }
            if prior["state"] not in permitted.get(state, set()):
                raise RuntimeError("Hermes lifecycle transition is not permitted")
            if current < _timestamp(prior["event_at"]):
                raise ValueError("lifecycle event cannot predate the prior event")
            if current > _timestamp(self._reservation(records, run_id)["deadline"]):
                raise RuntimeError("Hermes run exceeded its approved deadline")
            if state in {"HEARTBEAT", "SUCCEEDED", "FAILED"} and current - _timestamp(prior["event_at"]) > timedelta(seconds=activation["stale_heartbeat_seconds"]):
                raise RuntimeError("Hermes heartbeat is stale")
            result = self._event(prior, state, current, extra)
            return self._append_locked(records, result)

    def _event(
        self, prior: Mapping[str, Any], state: str, at: datetime,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "lifecycle_policy_version": POLICY_VERSION,
            "record_type": "HERMES_WORKER_LIFECYCLE_EVENT",
            "state": state, "run_id": prior["run_id"],
            "request_id": prior["request_id"], "activation_id": prior["activation_id"],
            "activation_record_hash": prior["activation_record_hash"],
            "job_name": prior["job_name"], "git_revision": prior["git_revision"],
            "event_at": at.isoformat(), "previous_run_event_hash": prior["record_hash"],
            "job_started": prior["state"] != "RESERVED_NOT_STARTED" or state == "STARTED",
            **(dict(extra or {})), **self._fixed_false(),
        }

    @staticmethod
    def _fixed_false() -> dict[str, bool]:
        return {
            "scheduler_created": False, "work_executed_by_ledger": False,
            "network_access_allowed": False, "broker_access_allowed": False,
            "aws_access_allowed": False, "github_write_allowed": False,
            "promotion_allowed": False, "order_submission_allowed": False,
            "live_trading_enabled": False,
        }

    def _violation(
        self, latest: Mapping[str, Mapping[str, Any]], records: list[dict[str, Any]],
        current: datetime,
    ) -> tuple[Mapping[str, Any], str] | None:
        for run in latest.values():
            if run["state"] not in ACTIVE_STATES:
                continue
            activation = self._activation(run["activation_id"])
            reservation = self._reservation(records, run["run_id"])
            if current > _timestamp(reservation["deadline"]):
                return run, "JOB_DEADLINE_EXCEEDED"
            if run["state"] in {"STARTED", "HEARTBEAT"} and current - _timestamp(run["event_at"]) > timedelta(seconds=activation["stale_heartbeat_seconds"]):
                return run, "STALE_HEARTBEAT"
        failure_counts: dict[str, int] = {}
        completed = sorted(
            (value for value in latest.values() if value["state"] in TERMINAL_STATES),
            key=lambda value: _timestamp(value["event_at"]), reverse=True,
        )
        for item in completed:
            activation = self._activation(item["activation_id"])
            policy = self._policy(activation["policy_id"])
            policy_id = policy["policy_id"]
            if policy_id not in failure_counts:
                failure_counts[policy_id] = 0
            if item["state"] == "SUCCEEDED":
                failure_counts[policy_id] = -1
                continue
            if failure_counts[policy_id] < 0:
                continue
            failure_counts[policy_id] += 1
            if failure_counts[policy_id] >= policy["max_consecutive_failures"]:
                return item, "CONSECUTIVE_FAILURE_LIMIT_REACHED"
        return None

    def _activation(self, activation_id: str) -> dict[str, Any]:
        match = next(
            (item for item in self.activation_ledger.verify() if item["activation_id"] == activation_id),
            None,
        )
        if match is None:
            raise LedgerIntegrityError("Hermes lifecycle lost its activation evidence.")
        return match

    def _policy(self, policy_id: str) -> dict[str, Any]:
        match = next(
            (item for item in self.activation_ledger.policy_ledger.verify() if item["policy_id"] == policy_id),
            None,
        )
        if match is None:
            raise LedgerIntegrityError("Hermes lifecycle lost its policy evidence.")
        return match

    @staticmethod
    def _latest(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for item in records:
            latest[item["run_id"]] = item
        return latest

    @staticmethod
    def _reservation(records: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
        match = next(
            (item for item in records if item["run_id"] == run_id and item["state"] == "RESERVED_NOT_STARTED"),
            None,
        )
        if match is None:
            raise LedgerIntegrityError("Hermes run lost its reservation evidence.")
        return match

    def _append_locked(
        self, records: list[dict[str, Any]], result: dict[str, Any]
    ) -> dict[str, Any]:
        material = {
            **result,
            "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
        }
        record = {**material, "record_hash": _record_hash(material)}
        target = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            _write_all(target, (_canonical_json(record) + "\n").encode())
            os.fsync(target)
        finally:
            os.close(target)
        return record

    class _Lock:
        def __init__(self, path: Path) -> None:
            self.descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        def __enter__(self):
            fcntl.flock(self.descriptor, fcntl.LOCK_EX)
            return self
        def __exit__(self, *_args):
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)

    def _lock(self) -> "HermesWorkerLifecycleLedger._Lock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return self._Lock(self.path.with_suffix(self.path.suffix + ".lock"))
