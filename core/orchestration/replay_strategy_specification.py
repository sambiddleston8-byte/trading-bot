from __future__ import annotations

"""Immutable pre-data-access strategy identity for one sealed replay plan."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.guardrailed_backtest import (
    canonical_strategy_parameters,
    strategy_parameter_hash,
)
from core.orchestration.active_pipeline_replay_plan import ActivePipelineReplayPlanLedger


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "single-preregistered-replay-strategy-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
FIXED_FALSE = (
    "evaluation_dataset_opened",
    "strategy_search_allowed",
    "parameter_search_allowed",
    "post_access_mutation_allowed",
    "replay_executed",
    "performance_calculated",
    "performance_claim_allowed",
    "model_trained",
    "learning_applied",
    "network_allowed",
    "paper_broker_submission_enabled",
    "broker_connection_allowed",
    "orders_submitted",
    "live_trading_enabled",
)
REQUIRED_FIELDS = {
    "schema_version", "policy_version", "strategy_specification_id",
    "record_type", "status", "registered_at", "registered_by",
    "replay_plan_id", "replay_plan_record_hash", "replay_plan_git_revision",
    "replay_plan_evaluation_start", "replay_plan_evaluation_end",
    "strategy_entrypoint",
    "strategy_version", "strategy_source_sha256", "parameters_canonical_json",
    "parameter_hash", "single_strategy_for_plan", "simulation_only",
    *FIXED_FALSE, "previous_hash", "record_hash",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    )


def _record_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _time(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)


def _required(value: Any, name: str, maximum: int = 200) -> str:
    resolved = str(value or "").strip()
    if not resolved or len(resolved) > maximum:
        raise ValueError(f"{name} is required and must not exceed {maximum} characters")
    return resolved


def _sha256(value: Any, name: str) -> str:
    resolved = _required(value, name, 64).lower()
    if not SHA256.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _git_revision(value: Any) -> str:
    resolved = _required(value, "replay_plan_git_revision", 64).lower()
    if not GIT_REVISION.fullmatch(resolved):
        raise ValueError("replay plan requires a full lowercase Git commit ID")
    return resolved


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("strategy-specification append made no progress")
        offset += count


def _identity(
    *,
    plan: Mapping[str, Any],
    strategy_entrypoint: Any,
    strategy_version: Any,
    strategy_source_sha256: Any,
    parameters_canonical_json: str,
    parameter_hash: str,
) -> dict[str, Any]:
    return {
        "replay_plan_id": _required(plan.get("replay_plan_id"), "replay_plan_id"),
        "replay_plan_record_hash": _sha256(
            plan.get("record_hash"), "replay_plan_record_hash"
        ),
        "replay_plan_git_revision": _git_revision(plan.get("git_revision")),
        "replay_plan_evaluation_start": _time(
            plan.get("evaluation_not_before")
        ).isoformat(timespec="microseconds"),
        "replay_plan_evaluation_end": _time(
            plan.get("evaluation_not_after")
        ).isoformat(timespec="microseconds"),
        "strategy_entrypoint": _required(
            strategy_entrypoint, "strategy_entrypoint", 300
        ),
        "strategy_version": _required(strategy_version, "strategy_version", 200),
        "strategy_source_sha256": _sha256(
            strategy_source_sha256, "strategy_source_sha256"
        ),
        "parameters_canonical_json": parameters_canonical_json,
        "parameter_hash": _sha256(parameter_hash, "parameter_hash"),
    }


def _specification_id(identity: Mapping[str, Any]) -> str:
    return "RSTRAT-" + hashlib.sha256(
        _canonical_json([identity, POLICY_VERSION]).encode("utf-8")
    ).hexdigest()[:32].upper()


class ReplayStrategySpecificationLedger:
    """Bind exactly one strategy and parameter set to a plan before data access."""

    def __init__(
        self,
        path: str | Path,
        *,
        plan_ledger: ActivePipelineReplayPlanLedger,
    ) -> None:
        self.path = Path(path)
        self.plan_ledger = plan_ledger

    def _plan(self, replay_plan_id: Any) -> dict[str, Any]:
        identifier = _required(replay_plan_id, "replay_plan_id")
        plan = next(
            (
                item for item in self.plan_ledger.verify()
                if item.get("replay_plan_id") == identifier
            ),
            None,
        )
        if plan is None:
            raise ValueError("a verified replay plan is required")
        return plan

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Replay-strategy specification ledger has an incomplete final line."
            )
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank replay-strategy specification line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid replay-strategy JSON at line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Replay-strategy line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def preregister(
        self,
        *,
        replay_plan_id: str,
        strategy_entrypoint: str,
        strategy_version: str,
        strategy_source_sha256: str,
        parameters: Mapping[str, Any],
        registered_by: str,
        registered_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        registered = _time(registered_at or datetime.now(timezone.utc))
        now = datetime.now(timezone.utc)
        if not now - MAX_CLOCK_SKEW <= registered <= now + MAX_CLOCK_SKEW:
            raise ValueError("registered_at must match the actual append time")
        plan = self._plan(replay_plan_id)
        if registered >= _time(plan["evaluation_data_access_not_before"]):
            raise ValueError("strategy must be preregistered before sealed data access")
        canonical_parameters = canonical_strategy_parameters(parameters)
        parameter_digest = strategy_parameter_hash(parameters)
        identity = _identity(
            plan=plan,
            strategy_entrypoint=strategy_entrypoint,
            strategy_version=strategy_version,
            strategy_source_sha256=strategy_source_sha256,
            parameters_canonical_json=canonical_parameters,
            parameter_hash=parameter_digest,
        )
        record = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "strategy_specification_id": _specification_id(identity),
            "record_type": "PREREGISTERED_REPLAY_STRATEGY_SPECIFICATION",
            "status": "PREREGISTERED_BEFORE_EVALUATION_DATA_ACCESS",
            "registered_at": registered.isoformat(timespec="microseconds"),
            "registered_by": _required(registered_by, "registered_by", 100),
            **identity,
            "single_strategy_for_plan": True,
            "simulation_only": True,
            **{field: False for field in FIXED_FALSE},
        }
        return self._append(record, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous = GENESIS_HASH
        seen_ids: set[str] = set()
        seen_plans: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            try:
                if set(record) != REQUIRED_FIELDS:
                    raise ValueError("missing or unsupported fields")
                plan = self._plan(record["replay_plan_id"])
                registered = _time(record["registered_at"])
                canonical_parameters = _required(
                    record["parameters_canonical_json"],
                    "parameters_canonical_json",
                    100_000,
                )
                decoded = json.loads(canonical_parameters)
                if (
                    not isinstance(decoded, dict)
                    or canonical_strategy_parameters(decoded) != canonical_parameters
                ):
                    raise ValueError("parameters are not a canonical JSON object")
                parameter_digest = hashlib.sha256(
                    canonical_parameters.encode("utf-8")
                ).hexdigest()
                identity = _identity(
                    plan=plan,
                    strategy_entrypoint=record["strategy_entrypoint"],
                    strategy_version=record["strategy_version"],
                    strategy_source_sha256=record["strategy_source_sha256"],
                    parameters_canonical_json=canonical_parameters,
                    parameter_hash=parameter_digest,
                )
                material = {
                    key: value for key, value in record.items() if key != "record_hash"
                }
                expected_id = _specification_id(identity)
                boundary = (
                    record["schema_version"] == SCHEMA_VERSION
                    and record["policy_version"] == POLICY_VERSION
                    and record["record_type"]
                    == "PREREGISTERED_REPLAY_STRATEGY_SPECIFICATION"
                    and record["status"]
                    == "PREREGISTERED_BEFORE_EVALUATION_DATA_ACCESS"
                    and record["strategy_specification_id"] == expected_id
                    and expected_id not in seen_ids
                    and record["replay_plan_id"] not in seen_plans
                    and all(record[key] == value for key, value in identity.items())
                    and registered < _time(plan["evaluation_data_access_not_before"])
                    and registered <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                    and record["single_strategy_for_plan"] is True
                    and record["simulation_only"] is True
                    and all(record[field] is False for field in FIXED_FALSE)
                    and record["previous_hash"] == previous
                    and record["record_hash"] == _record_hash(material)
                    and bool(_required(record["registered_by"], "registered_by", 100))
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise LedgerIntegrityError(
                    f"Replay-strategy specification {index} is invalid."
                ) from error
            if not boundary:
                raise LedgerIntegrityError(
                    f"Replay-strategy specification {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            seen_plans.add(record["replay_plan_id"])
            previous = record["record_hash"]
        return records

    def _append(
        self, record: dict[str, Any], *, allow_existing: bool,
    ) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (
                    item for item in records
                    if item["replay_plan_id"] == record["replay_plan_id"]
                ),
                None,
            )
            if existing:
                ignored = {"registered_at", "previous_hash", "record_hash"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {
                    key: value for key, value in record.items() if key not in ignored
                }:
                    return existing
                raise LedgerIntegrityError(
                    "replay plan already has its single strategy specification"
                )
            material = {
                **record,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            complete = {**material, "record_hash": _record_hash(material)}
            descriptor = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            try:
                _write_all(
                    descriptor, (_canonical_json(complete) + "\n").encode("utf-8")
                )
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return complete
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
