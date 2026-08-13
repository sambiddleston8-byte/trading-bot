from __future__ import annotations

"""Immutable execution-realism policy bound to a preregistered replay plan."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.active_pipeline_replay_plan import ActivePipelineReplayPlanLedger
from core.orchestration.replay_execution_policy import ReplayExecutionPolicy


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "preregistered-replay-execution-policy-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
BASE_TO_PLAN_COST = {
    "commission_bps_per_side": "commission_bps_per_side",
    "spread_bps_per_side": "bid_ask_half_spread_bps_per_side",
    "slippage_bps_per_side": "slippage_bps_per_side",
    "latency_bps_per_side": "latency_bps_per_side",
    "market_impact_bps_per_side": "market_impact_bps_per_side",
}
FIXED_FALSE = (
    "evaluation_dataset_opened",
    "evaluation_observations_interpreted",
    "costs_calculated",
    "replay_executed",
    "fills_generated",
    "position_mutated",
    "cash_mutated",
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


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Unable to complete replay execution-policy append")
        offset += count


def _identity(plan: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "replay_plan_id": plan["replay_plan_id"],
        "replay_plan_record_hash": plan["record_hash"],
        "execution_policy_id": policy["execution_policy_id"],
        "execution_policy_version": policy["policy_version"],
        "scenarios": policy["scenarios"],
    }


def _policy_record_id(identity: Mapping[str, Any]) -> str:
    return "REXP-" + hashlib.sha256(
        _canonical_json([identity, POLICY_VERSION]).encode("utf-8")
    ).hexdigest()[:32].upper()


def _require_plan_alignment(plan: Mapping[str, Any], policy: Mapping[str, Any]) -> None:
    base = policy["scenarios"]["BASE"]
    pessimistic = policy["scenarios"]["PESSIMISTIC"]
    costs = plan.get("cost_model") or {}
    multiplier = Decimal(str(costs.get("pessimistic_cost_multiplier")))
    for policy_field, plan_field in BASE_TO_PLAN_COST.items():
        base_value = Decimal(base[policy_field])
        if base_value != Decimal(str(costs.get(plan_field))):
            raise ValueError(f"BASE.{policy_field} must match the replay plan")
        if Decimal(pessimistic[policy_field]) != base_value * multiplier:
            raise ValueError(
                f"PESSIMISTIC.{policy_field} must equal BASE times the plan multiplier"
            )


class ReplayExecutionPolicyLedger:
    """Preregister one inert execution policy before sealed replay data access."""

    def __init__(
        self,
        path: str | Path,
        plan_ledger: ActivePipelineReplayPlanLedger,
    ) -> None:
        self.path = Path(path)
        self.plan_ledger = plan_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Replay execution-policy ledger has an incomplete final line.")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank execution-policy line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid execution-policy JSON at line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Execution-policy line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def preregister(
        self,
        *,
        replay_plan_id: str,
        scenarios: Mapping[str, Mapping[str, Any]],
        defined_by: str,
        defined_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        defined = _timestamp(defined_at or datetime.now(timezone.utc))
        now = datetime.now(timezone.utc)
        if not now - MAX_CLOCK_SKEW <= defined <= now + MAX_CLOCK_SKEW:
            raise ValueError("defined_at must match the actual append time")
        plan = self._plan(replay_plan_id)
        if defined >= _timestamp(plan["evaluation_data_access_not_before"]):
            raise ValueError("execution policy must be defined before sealed evaluation-data access")
        policy = ReplayExecutionPolicy.define(scenarios)
        _require_plan_alignment(plan, policy)
        identity = _identity(plan, policy)
        record = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "replay_execution_policy_record_id": _policy_record_id(identity),
            "record_type": "PREREGISTERED_REPLAY_EXECUTION_REALISM_POLICY",
            "status": "PREREGISTERED_BEFORE_EVALUATION_DATA_ACCESS",
            "defined_at": defined.isoformat(),
            "defined_by": _required(defined_by, "defined_by"),
            **identity,
            "simulation_only": True,
            "next_tradable_observation_required": True,
            **{field: False for field in FIXED_FALSE},
        }
        return self._append(record, allow_existing=allow_existing)

    def _plan(self, replay_plan_id: Any) -> dict[str, Any]:
        identifier = _required(replay_plan_id, "replay_plan_id")
        plan = next(
            (item for item in self.plan_ledger.verify() if item.get("replay_plan_id") == identifier),
            None,
        )
        if plan is None:
            raise ValueError("a verified replay plan is required")
        return plan

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        seen_plans: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _hash(material):
                raise LedgerIntegrityError(f"Replay execution-policy record {index} was modified.")
            try:
                plan = self._plan(record.get("replay_plan_id"))
                defined = _timestamp(record.get("defined_at"))
                policy = ReplayExecutionPolicy.define(record.get("scenarios"))
                _require_plan_alignment(plan, policy)
                identity = _identity(plan, policy)
                _required(record.get("defined_by"), "defined_by")
            except (ArithmeticError, KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Replay execution-policy record {index} has invalid values."
                ) from error
            expected_id = _policy_record_id(identity)
            boundary = (
                record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("replay_execution_policy_record_id") == expected_id
                and expected_id not in seen_ids
                and record.get("replay_plan_id") not in seen_plans
                and record.get("record_type") == "PREREGISTERED_REPLAY_EXECUTION_REALISM_POLICY"
                and record.get("status") == "PREREGISTERED_BEFORE_EVALUATION_DATA_ACCESS"
                and all(record.get(key) == value for key, value in identity.items())
                and defined < _timestamp(plan["evaluation_data_access_not_before"])
                and defined <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("simulation_only") is True
                and record.get("next_tradable_observation_required") is True
                and all(record.get(field) is False for field in FIXED_FALSE)
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Replay execution-policy record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            seen_plans.add(record["replay_plan_id"])
            previous_hash = record["record_hash"]
        return records

    def _append(self, record: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["replay_plan_id"] == record["replay_plan_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "defined_at"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in record.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError("Replay plan already has its single execution policy.")
            material = {
                **record,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            result = {**material, "record_hash": _hash(material)}
            target = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                _write_all(target, (_canonical_json(result) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            return result
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
