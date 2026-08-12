from __future__ import annotations

"""Durable one-way Hermes emergency stop; no resume or activation capability."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.orchestration.hermes_policy import HermesPermissionPolicyLedger
from core.performance.pinned_support import resolve_pinned_records
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


STOP_SCHEMA_VERSION = "1.0"
STOP_POLICY_VERSION = "durable-latched-hermes-stop-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
ALLOWED_SOURCES = {"HUMAN", "SAFETY_MONITOR", "SYSTEM_FAILURE_GUARD"}


def _required(value: Any, name: str, maximum: int = 1000) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    if len(result) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return result


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _stop_id(policy_id: str, triggered_at: str, source: str, reason: str) -> str:
    return "HSTOP-" + hashlib.sha256(
        _canonical_json([policy_id, triggered_at, source, reason, STOP_POLICY_VERSION]).encode()
    ).hexdigest()[:32].upper()


class HermesEmergencyStopLedger:
    """Records a latched stop. This class intentionally has no clear/resume method."""

    def __init__(self, path: str | Path, policy_ledger: HermesPermissionPolicyLedger) -> None:
        self.path = Path(path)
        self.policy_ledger = policy_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Hermes-stop ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank Hermes-stop line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(f"Invalid JSON at Hermes-stop line {line_number}.") from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(f"Hermes-stop line {line_number} is not an object.")
                records.append(record)
        return records

    def trigger(
        self, *, policy_id: str, emergency_stop_identifier: str,
        trigger_source: str, reason: str, triggered_by: str,
        triggered_at: str | datetime | None = None, allow_existing: bool = True,
    ) -> dict[str, Any]:
        policies = self.policy_ledger.verify()
        policy = next((item for item in policies if item.get("policy_id") == policy_id), None)
        if policy is None:
            raise ValueError("A verified Hermes policy is required")
        identifier = _required(emergency_stop_identifier, "emergency_stop_identifier", 200)
        if identifier != policy["emergency_stop_identifier"]:
            raise ValueError("emergency_stop_identifier does not match the policy")
        source = _required(trigger_source, "trigger_source", 50).upper()
        if source not in ALLOWED_SOURCES:
            raise ValueError("trigger_source is not permitted")
        resolved_reason = _required(reason, "reason")
        triggered = _timestamp(triggered_at or datetime.now(timezone.utc))
        if triggered > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("triggered_at cannot be in the future")
        if triggered < _timestamp(policy["recorded_at"]):
            raise ValueError("triggered_at cannot predate the policy")
        result = {
            "schema_version": STOP_SCHEMA_VERSION,
            "stop_policy_version": STOP_POLICY_VERSION,
            "stop_id": _stop_id(policy["policy_id"], triggered.isoformat(), source, resolved_reason),
            "record_type": "HERMES_DURABLE_EMERGENCY_STOP",
            "status": "STOPPED_LATCHED",
            "agent_id": "HERMES",
            "triggered_at": triggered.isoformat(),
            "triggered_by": _required(triggered_by, "triggered_by", 100),
            "trigger_source": source,
            "reason": resolved_reason,
            "policy_id": policy["policy_id"],
            "policy_record_hash": policy["record_hash"],
            "emergency_stop_identifier": identifier,
            "new_jobs_allowed": False,
            "running_jobs_must_terminate": True,
            "evidence_deletion_allowed": False,
            "automatic_resume_allowed": False,
            "self_resume_allowed": False,
            "scheduler_enabled": False,
            "model_invocation_allowed": False,
            "network_access_allowed": False,
            "broker_access_allowed": False,
            "aws_access_allowed": False,
            "github_write_allowed": False,
            "production_rule_write_allowed": False,
            "promotion_allowed": False,
            "order_submission_allowed": False,
            "live_trading_enabled": False,
        }
        return self._append(result, allow_existing=allow_existing)

    def runtime_state(self, policy_id: str) -> dict[str, Any]:
        policies = self.policy_ledger.verify()
        policy = next((item for item in policies if item.get("policy_id") == policy_id), None)
        if policy is None:
            return {"state": "STOPPED", "work_allowed": False, "reason": "UNKNOWN_POLICY"}
        stops = [item for item in self.verify() if item["policy_id"] == policy_id]
        if stops:
            return {"state": "STOPPED_LATCHED", "work_allowed": False, "reason": "EMERGENCY_STOP", "stop_id": stops[-1]["stop_id"]}
        return {"state": "STOPPED", "work_allowed": False, "reason": "POLICY_INACTIVE"}

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_policies = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(f"Hermes-stop record {index} has been modified.")
            policies, reasons = resolve_pinned_records(
                self.policy_ledger.verify(), [record.get("policy_id")], [record.get("policy_record_hash")],
                id_field="policy_id", label="Hermes policy",
            )
            if reasons or len(policies) != 1:
                raise LedgerIntegrityError(f"Hermes-stop record {index} lost its policy.")
            policy = policies[0]
            try:
                triggered = _timestamp(record.get("triggered_at")); source = _required(record.get("trigger_source"), "source", 50).upper(); reason = _required(record.get("reason"), "reason")
                _required(record.get("triggered_by"), "triggered_by", 100)
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Hermes-stop record {index} has invalid values.") from error
            expected_id = _stop_id(policy["policy_id"], triggered.isoformat(), source, reason)
            fixed_false = ("new_jobs_allowed","evidence_deletion_allowed","automatic_resume_allowed","self_resume_allowed","scheduler_enabled","model_invocation_allowed","network_access_allowed","broker_access_allowed","aws_access_allowed","github_write_allowed","production_rule_write_allowed","promotion_allowed","order_submission_allowed","live_trading_enabled")
            boundary = (
                record.get("schema_version") == STOP_SCHEMA_VERSION and record.get("stop_policy_version") == STOP_POLICY_VERSION
                and record.get("stop_id") == expected_id and policy["policy_id"] not in seen_policies
                and record.get("record_type") == "HERMES_DURABLE_EMERGENCY_STOP" and record.get("status") == "STOPPED_LATCHED" and record.get("agent_id") == "HERMES"
                and record.get("policy_id") == policy["policy_id"] and record.get("policy_record_hash") == policy["record_hash"]
                and record.get("emergency_stop_identifier") == policy["emergency_stop_identifier"]
                and triggered >= _timestamp(policy["recorded_at"]) and triggered <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and source in ALLOWED_SOURCES and record.get("running_jobs_must_terminate") is True
                and all(record.get(field) is False for field in fixed_false)
            )
            if not boundary: raise LedgerIntegrityError(f"Hermes-stop record {index} violates its boundary.")
            seen_policies.add(policy["policy_id"]); previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True); descriptor = os.open(self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX); records = self.verify()
            existing = next((item for item in records if item["stop_id"] == result["stop_id"]), None)
            if existing:
                ignored={"previous_hash","record_hash"}
                if allow_existing and {k:v for k,v in existing.items() if k not in ignored} == {k:v for k,v in result.items() if k not in ignored}: return existing
                raise LedgerIntegrityError("Hermes emergency stop already exists.")
            if any(item["policy_id"] == result["policy_id"] for item in records): raise LedgerIntegrityError("Hermes policy is already stopped and latched.")
            material={**result,"previous_hash":records[-1]["record_hash"] if records else GENESIS_HASH}; record={**material,"record_hash":_record_hash(material)}
            target=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            try: _write_all(target,(_canonical_json(record)+"\n").encode()); os.fsync(target)
            finally: os.close(target)
            return record
        finally: fcntl.flock(descriptor,fcntl.LOCK_UN); os.close(descriptor)
