from __future__ import annotations

"""Immutable preregistration for future paper/shadow observation windows."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.strategy_registry import CandidateStrategyRegistryLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


SHADOW_PLAN_SCHEMA_VERSION = "1.0"
SHADOW_PLAN_POLICY_VERSION = "future-paper-shadow-plan-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
MINIMUM_SHADOW_SPAN = timedelta(days=30)


def _required(value: Any, name: str, maximum: int = 300) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


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


def _plan_id(
    registry_entry_id: str, planned_at: str, start: str, end: str,
    minimum_complete_decisions: int, observation_cadence: str,
) -> str:
    return "SHADOW-" + hashlib.sha256(
        _canonical_json([
            registry_entry_id, planned_at, start, end,
            minimum_complete_decisions, observation_cadence,
            SHADOW_PLAN_POLICY_VERSION,
        ]).encode()
    ).hexdigest()[:32].upper()


class ShadowTestPlanLedger:
    """Plans a future observation window but cannot start or execute it."""

    def __init__(self, path: str | Path, strategy_registry: CandidateStrategyRegistryLedger) -> None:
        self.path = Path(path)
        self.strategy_registry = strategy_registry

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Shadow-plan ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank shadow-plan line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(f"Invalid JSON at shadow-plan line {line_number}.") from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(f"Shadow-plan line {line_number} is not an object.")
                records.append(record)
        return records

    def preregister(
        self, *, strategy_registry_entry_id: str,
        shadow_not_before: str | datetime, shadow_not_after: str | datetime,
        minimum_complete_decisions: int, observation_cadence: str,
        planned_by: str, planned_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        entries = self.strategy_registry.verify()
        entry = next((item for item in entries if item.get("strategy_registry_entry_id") == strategy_registry_entry_id), None)
        if entry is None or entry.get("status") != "ELIGIBLE_FOR_SHADOW_NOT_STARTED":
            raise ValueError("A verified shadow-eligible candidate is required")
        experiment = self._experiment_for(entry)
        planned = _timestamp(planned_at or datetime.now(timezone.utc))
        start = _timestamp(shadow_not_before)
        end = _timestamp(shadow_not_after)
        if planned > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("planned_at cannot be in the future")
        if start <= planned:
            raise ValueError("shadow window must begin after preregistration")
        if start < _timestamp(experiment["shadow_not_before"]):
            raise ValueError("shadow window cannot begin before the experiment boundary")
        if end < start + MINIMUM_SHADOW_SPAN:
            raise ValueError("shadow window must span at least 30 days")
        cadence = _required(observation_cadence, "observation_cadence", 40).upper()
        if cadence not in {"DAILY_MARKET_CLOSE", "WEEKLY_MARKET_CLOSE"}:
            raise ValueError("observation_cadence is not supported")
        decisions = _positive_int(minimum_complete_decisions, "minimum_complete_decisions", 100_000)
        result = {
            "schema_version": SHADOW_PLAN_SCHEMA_VERSION,
            "policy_version": SHADOW_PLAN_POLICY_VERSION,
            "shadow_plan_id": _plan_id(
                entry["strategy_registry_entry_id"], planned.isoformat(),
                start.isoformat(), end.isoformat(), decisions, cadence,
            ),
            "record_type": "CONTROLLED_LEARNING_PAPER_SHADOW_TEST_PLAN",
            "status": "PREREGISTERED_NOT_STARTED",
            "paper_only": True,
            "planned_at": planned.isoformat(),
            "planned_by": _required(planned_by, "planned_by", 100),
            "strategy_registry_entry_id": entry["strategy_registry_entry_id"],
            "strategy_registry_record_hash": entry["record_hash"],
            "experiment_id": experiment["experiment_id"],
            "experiment_record_hash": experiment["record_hash"],
            "candidate_strategy_version": entry["candidate_strategy_version"],
            "baseline_strategy_version": entry["baseline_strategy_version"],
            "shadow_not_before": start.isoformat(),
            "shadow_not_after": end.isoformat(),
            "minimum_complete_decisions": decisions,
            "observation_cadence": cadence,
            "primary_metric": experiment["primary_metric"],
            "minimum_primary_improvement": experiment["minimum_primary_improvement"],
            "maximum_drawdown_degradation": experiment["maximum_drawdown_degradation"],
            "maximum_turnover_increase": experiment["maximum_turnover_increase"],
            "retrospective_window_selection_allowed": False,
            "window_extension_after_start_allowed": False,
            "metric_substitution_allowed": False,
            "broker_connection_allowed": False,
            "shadow_test_started": False,
            "shadow_result_recorded": False,
            "promotion_approved": False,
            "incumbent_changed": False,
            "production_active": False,
            "deployment_performed": False,
            "order_submitted": False,
            "live_trading_enabled": False,
        }
        return self._append(result, allow_existing=allow_existing)

    def _experiment_for(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        result_ledger = self.strategy_registry.result_ledger
        experiments = result_ledger.run_manifest_ledger.experiment_ledger.verify()
        values, reasons = resolve_pinned_records(
            experiments, [entry.get("experiment_id")], [entry.get("experiment_record_hash")],
            id_field="experiment_id", label="experiment",
        )
        if reasons or len(values) != 1:
            raise LedgerIntegrityError("Strategy disposition lost its experiment.")
        return values[0]

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_entries = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Shadow-plan record {index} has been modified.")
            entries, reasons = resolve_pinned_records(
                self.strategy_registry.verify(), [record.get("strategy_registry_entry_id")],
                [record.get("strategy_registry_record_hash")], id_field="strategy_registry_entry_id", label="strategy disposition",
            )
            if reasons or len(entries) != 1:
                raise LedgerIntegrityError(f"Shadow-plan record {index} lost its strategy disposition.")
            entry = entries[0]
            experiment = self._experiment_for(entry)
            try:
                planned = _timestamp(record.get("planned_at")); start = _timestamp(record.get("shadow_not_before")); end = _timestamp(record.get("shadow_not_after"))
                decisions = _positive_int(record.get("minimum_complete_decisions"), "decisions", 100_000)
                cadence = _required(record.get("observation_cadence"), "cadence", 40).upper()
                _required(record.get("planned_by"), "planned_by", 100)
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Shadow-plan record {index} has invalid values.") from error
            expected_id = _plan_id(
                entry["strategy_registry_entry_id"], planned.isoformat(),
                start.isoformat(), end.isoformat(), decisions, cadence,
            )
            fixed_false = (
                "retrospective_window_selection_allowed", "window_extension_after_start_allowed",
                "metric_substitution_allowed", "broker_connection_allowed", "shadow_test_started",
                "shadow_result_recorded", "promotion_approved", "incumbent_changed",
                "production_active", "deployment_performed", "order_submitted", "live_trading_enabled",
            )
            boundary = (
                record.get("schema_version") == SHADOW_PLAN_SCHEMA_VERSION
                and record.get("policy_version") == SHADOW_PLAN_POLICY_VERSION
                and record.get("shadow_plan_id") == expected_id
                and record.get("record_type") == "CONTROLLED_LEARNING_PAPER_SHADOW_TEST_PLAN"
                and record.get("status") == "PREREGISTERED_NOT_STARTED" and record.get("paper_only") is True
                and entry.get("status") == "ELIGIBLE_FOR_SHADOW_NOT_STARTED"
                and entry["strategy_registry_entry_id"] not in seen_entries
                and record.get("strategy_registry_entry_id") == entry["strategy_registry_entry_id"]
                and record.get("strategy_registry_record_hash") == entry["record_hash"]
                and record.get("experiment_id") == experiment["experiment_id"]
                and record.get("experiment_record_hash") == experiment["record_hash"]
                and record.get("candidate_strategy_version") == entry["candidate_strategy_version"]
                and record.get("baseline_strategy_version") == entry["baseline_strategy_version"]
                and planned <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW and planned < start
                and start >= _timestamp(experiment["shadow_not_before"]) and end >= start + MINIMUM_SHADOW_SPAN
                and decisions == record.get("minimum_complete_decisions")
                and cadence in {"DAILY_MARKET_CLOSE", "WEEKLY_MARKET_CLOSE"}
                and record.get("primary_metric") == experiment["primary_metric"]
                and record.get("minimum_primary_improvement") == experiment["minimum_primary_improvement"]
                and record.get("maximum_drawdown_degradation") == experiment["maximum_drawdown_degradation"]
                and record.get("maximum_turnover_increase") == experiment["maximum_turnover_increase"]
                and all(record.get(field) is False for field in fixed_false)
            )
            if not boundary:
                raise LedgerIntegrityError(f"Shadow-plan record {index} violates its boundary.")
            seen_entries.add(entry["strategy_registry_entry_id"]); previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX); records = self.verify()
            existing = next((item for item in records if item["shadow_plan_id"] == result["shadow_plan_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash"}
                if allow_existing and {k: v for k, v in existing.items() if k not in ignored} == {k: v for k, v in result.items() if k not in ignored}: return existing
                raise LedgerIntegrityError("Shadow plan already exists.")
            if any(item["strategy_registry_entry_id"] == result["strategy_registry_entry_id"] for item in records):
                raise LedgerIntegrityError("Candidate already has a shadow plan.")
            material = {**result, "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH}
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                _write_all(target, (_canonical_json(record) + "\n").encode()); os.fsync(target)
            finally: os.close(target)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN); os.close(descriptor)
