from __future__ import annotations

"""Immutable candidate disposition registry; no strategy is activated here."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.robustness_result import RobustnessTestResultLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


REGISTRY_SCHEMA_VERSION = "1.0"
REGISTRY_POLICY_VERSION = "candidate-disposition-registry-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)


def _required(value: Any, name: str, maximum: int = 200) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _entry_id(result_id: str, recorded_at: str) -> str:
    return "STRATEGY-" + hashlib.sha256(
        _canonical_json([result_id, recorded_at, REGISTRY_POLICY_VERSION]).encode()
    ).hexdigest()[:32].upper()


class CandidateStrategyRegistryLedger:
    """Classifies experiment candidates without starting shadow or production use."""

    def __init__(self, path: str | Path, robustness_result_ledger: RobustnessTestResultLedger) -> None:
        self.path = Path(path)
        self.robustness_result_ledger = robustness_result_ledger
        self.result_ledger = robustness_result_ledger.plan_ledger.result_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Strategy-registry ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank strategy-registry line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(f"Invalid JSON at strategy-registry line {line_number}.") from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(f"Strategy-registry line {line_number} is not an object.")
                records.append(record)
        return records

    def classify(
        self, *, robustness_result_id: str, recorded_by: str,
        recorded_at: str | datetime | None = None, allow_existing: bool = True,
    ) -> dict[str, Any]:
        robustness_results = self.robustness_result_ledger.verify()
        robustness = next((item for item in robustness_results if item.get("robustness_result_id") == robustness_result_id), None)
        if robustness is None:
            raise ValueError("A verified robustness result is required")
        results = self.result_ledger.verify()
        result = next((item for item in results if item.get("result_id") == robustness["experiment_result_id"]), None)
        if result is None: raise LedgerIntegrityError("Robustness result lost its experiment result.")
        experiment = self._experiment_for(result)
        recorded = _timestamp(recorded_at or datetime.now(timezone.utc))
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        if recorded < _timestamp(result["completed_at"]):
            raise ValueError("recorded_at cannot predate the experiment result")
        accepted = robustness["status"] == "ROBUSTNESS_CRITERIA_MET"
        status = "ELIGIBLE_FOR_SHADOW_NOT_STARTED" if accepted else "REJECTED_EXPERIMENT"
        entry = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "policy_version": REGISTRY_POLICY_VERSION,
            "strategy_registry_entry_id": _entry_id(robustness_result_id, recorded.isoformat()),
            "record_type": "CONTROLLED_LEARNING_CANDIDATE_STRATEGY_DISPOSITION",
            "status": status,
            "recorded_at": recorded.isoformat(),
            "recorded_by": _required(recorded_by, "recorded_by", 100),
            "candidate_strategy_version": experiment["candidate_strategy_version"],
            "baseline_strategy_version": experiment["baseline_strategy_version"],
            "result_id": result["result_id"],
            "result_record_hash": result["record_hash"],
            "robustness_result_id": robustness["robustness_result_id"],
            "robustness_result_record_hash": robustness["record_hash"],
            "experiment_id": experiment["experiment_id"],
            "experiment_record_hash": experiment["record_hash"],
            "experiment_criteria_met": accepted,
            "shadow_eligible": accepted,
            "shadow_test_started": False,
            "incumbent": False,
            "production_active": False,
            "human_promotion_approved": False,
            "code_changed": False,
            "deployment_performed": False,
            "order_submitted": False,
            "live_trading_enabled": False,
        }
        return self._append(entry, allow_existing=allow_existing)

    def _experiment_for(self, result: Mapping[str, Any]) -> dict[str, Any]:
        experiments = self.result_ledger.run_manifest_ledger.experiment_ledger.verify()
        values, reasons = resolve_pinned_records(
            experiments, [result.get("experiment_id")], [result.get("experiment_record_hash")],
            id_field="experiment_id", label="experiment",
        )
        if reasons or len(values) != 1:
            raise LedgerIntegrityError("Experiment result lost its preregistered experiment.")
        return values[0]

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_results = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Strategy-registry record {index} has been modified.")
            robustness_results, reasons = resolve_pinned_records(
                self.robustness_result_ledger.verify(), [record.get("robustness_result_id")], [record.get("robustness_result_record_hash")],
                id_field="robustness_result_id", label="robustness result",
            )
            if reasons or len(robustness_results) != 1: raise LedgerIntegrityError(f"Strategy-registry record {index} lost its robustness result.")
            robustness = robustness_results[0]
            results=[item for item in self.result_ledger.verify() if item.get("result_id")==robustness["experiment_result_id"]]
            if len(results)!=1: raise LedgerIntegrityError(f"Strategy-registry record {index} lost its result.")
            result=results[0]
            experiment = self._experiment_for(result)
            try:
                recorded = _timestamp(record.get("recorded_at"))
                _required(record.get("recorded_by"), "recorded_by", 100)
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Strategy-registry record {index} has invalid values.") from error
            accepted = robustness["status"] == "ROBUSTNESS_CRITERIA_MET"
            expected_status = "ELIGIBLE_FOR_SHADOW_NOT_STARTED" if accepted else "REJECTED_EXPERIMENT"
            expected_id = _entry_id(robustness["robustness_result_id"], recorded.isoformat())
            fixed_false = (
                "shadow_test_started", "incumbent", "production_active",
                "human_promotion_approved", "code_changed", "deployment_performed",
                "order_submitted", "live_trading_enabled",
            )
            boundary = (
                record.get("schema_version") == REGISTRY_SCHEMA_VERSION
                and record.get("policy_version") == REGISTRY_POLICY_VERSION
                and record.get("strategy_registry_entry_id") == expected_id
                and record.get("record_type") == "CONTROLLED_LEARNING_CANDIDATE_STRATEGY_DISPOSITION"
                and record.get("status") == expected_status
                and record.get("result_id") not in seen_results
                and record.get("result_id") == result["result_id"]
                and record.get("result_record_hash") == result["record_hash"]
                and record.get("robustness_result_id") == robustness["robustness_result_id"]
                and record.get("robustness_result_record_hash") == robustness["record_hash"]
                and record.get("experiment_id") == experiment["experiment_id"]
                and record.get("experiment_record_hash") == experiment["record_hash"]
                and record.get("candidate_strategy_version") == experiment["candidate_strategy_version"]
                and record.get("baseline_strategy_version") == experiment["baseline_strategy_version"]
                and recorded >= _timestamp(result["completed_at"])
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("experiment_criteria_met") is accepted
                and record.get("shadow_eligible") is accepted
                and all(record.get(field) is False for field in fixed_false)
            )
            if not boundary:
                raise LedgerIntegrityError(f"Strategy-registry record {index} violates its boundary.")
            seen_results.add(result["result_id"])
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next((item for item in records if item["strategy_registry_entry_id"] == result["strategy_registry_entry_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash"}
                if allow_existing and {k: v for k, v in existing.items() if k not in ignored} == {k: v for k, v in result.items() if k not in ignored}:
                    return existing
                raise LedgerIntegrityError("Strategy-registry entry already exists.")
            if any(item["result_id"] == result["result_id"] for item in records):
                raise LedgerIntegrityError("Experiment result already has a candidate disposition.")
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
