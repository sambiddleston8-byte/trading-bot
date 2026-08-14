from __future__ import annotations

"""Durable one-way provider-paper kill switch; no clear or resume capability."""

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.broker.provider_paper_risk_policy import ProviderPaperRiskControlPolicyLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.pinned_support import resolve_pinned_records


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "durable-latched-provider-paper-kill-switch-v2"
MAX_CLOCK_SKEW = timedelta(minutes=5)
ALLOWED_SOURCES = {"HUMAN", "RISK_MONITOR", "SYSTEM_FAILURE_GUARD"}
FIXED_FALSE_FIELDS = (
    "paper_order_proposal_allowed",
    "paper_order_submission_allowed",
    "order_cancel_replace_allowed",
    "broker_access_allowed",
    "automatic_resume_allowed",
    "self_resume_allowed",
    "risk_limits_enforced",
    "order_route_exists",
    "external_head_anchor_present",
    "cryptographic_authentication_present",
    "live_trading_enabled",
)
RECORD_FIELDS = frozenset(
    {
        "schema_version", "stop_policy_version", "stop_id", "record_type", "status",
        "triggered_at", "recorded_at", "triggered_by", "trigger_source", "reason", "policy_id",
        "policy_record_hash", "account_reference_sha256", "kill_switch_identifier",
        "trading_halted",
        "previous_hash", "record_hash",
    }
    | set(FIXED_FALSE_FIELDS)
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Unable to complete provider-paper-kill-switch append")
        offset += count


def _required(value: Any, name: str, maximum: int = 1000) -> str:
    resolved = str(value or "").strip()
    if not resolved or len(resolved) > maximum:
        raise ValueError(f"{name} is required and must not exceed {maximum} characters")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _stop_id(
    policy_id: str,
    policy_record_hash: str,
    account_reference_sha256: str,
    kill_switch_identifier: str,
    triggered_at: str,
    recorded_at: str,
    source: str,
    reason: str,
    triggered_by: str,
) -> str:
    return "PPKS-" + hashlib.sha256(
        _canonical_json(
            [
                policy_id,
                policy_record_hash,
                account_reference_sha256,
                kill_switch_identifier,
                triggered_at,
                recorded_at,
                source,
                reason,
                triggered_by,
                POLICY_VERSION,
            ]
        ).encode("utf-8")
    ).hexdigest()[:32].upper()


def validate_kill_switch_prefix(
    records: Sequence[Mapping[str, Any]], count: Any, recorded_at: str | datetime
) -> None:
    """Require the prefix to equal the stop ledger state when the assessment was recorded."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 0 or count > len(records):
        raise ValueError("kill-switch record count is invalid")
    boundary_time = _timestamp(recorded_at)
    if any(_timestamp(stop.get("recorded_at")) > boundary_time for stop in records[:count]):
        raise ValueError("kill-switch prefix includes a stop recorded later")
    if any(_timestamp(stop.get("recorded_at")) <= boundary_time for stop in records[count:]):
        raise ValueError("kill-switch prefix omits an already-recorded stop")


class ProviderPaperKillSwitchLedger:
    """Records a latched stop. This class intentionally has no clear/resume method."""

    def __init__(self, path: str | Path, policy_ledger: ProviderPaperRiskControlPolicyLedger) -> None:
        self.path = Path(path)
        self.policy_ledger = policy_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Provider-paper-kill-switch ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank provider-paper-kill-switch line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at provider-paper-kill-switch line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Provider-paper-kill-switch line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def trigger(
        self,
        *,
        policy_id: str,
        kill_switch_identifier: str,
        trigger_source: str,
        reason: str,
        triggered_by: str,
        triggered_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        policies = self.policy_ledger.verify()
        policy = next((item for item in policies if item.get("policy_id") == policy_id), None)
        if policy is None:
            raise ValueError("A verified provider-paper risk policy is required")
        identifier = _required(kill_switch_identifier, "kill_switch_identifier", 200)
        if identifier != policy["kill_switch_identifier"]:
            raise ValueError("kill_switch_identifier does not match the policy")
        source = _required(trigger_source, "trigger_source", 50).upper()
        if source not in ALLOWED_SOURCES:
            raise ValueError("trigger_source is not permitted")
        resolved_reason = _required(reason, "reason")
        triggered = _timestamp(triggered_at or datetime.now(timezone.utc))
        if triggered > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("triggered_at cannot be in the future")
        if triggered < _timestamp(policy["recorded_at"]):
            raise ValueError("triggered_at cannot predate the policy")
        triggered_by_name = _required(triggered_by, "triggered_by", 100)
        account_hash = policy["account_reference_sha256"]
        result = {
            "schema_version": SCHEMA_VERSION,
            "stop_policy_version": POLICY_VERSION,
            "record_type": "PROVIDER_PAPER_KILL_SWITCH_LATCHED_STOP",
            "status": "STOPPED_LATCHED",
            "triggered_at": triggered.isoformat(),
            "triggered_by": triggered_by_name,
            "trigger_source": source,
            "reason": resolved_reason,
            "policy_id": policy["policy_id"],
            "policy_record_hash": policy["record_hash"],
            "account_reference_sha256": account_hash,
            "kill_switch_identifier": identifier,
            "trading_halted": True,
            **{field: False for field in FIXED_FALSE_FIELDS},
        }
        return self._append(result, allow_existing=allow_existing)

    def runtime_state(self, policy_id: str) -> dict[str, Any]:
        policies = self.policy_ledger.verify()
        policy = next((item for item in policies if item.get("policy_id") == policy_id), None)
        if policy is None:
            return {"state": "STOPPED", "work_allowed": False, "reason": "UNKNOWN_POLICY"}
        stops = [
            item
            for item in self.verify()
            if item["account_reference_sha256"] == policy["account_reference_sha256"]
            or item["kill_switch_identifier"] == policy["kill_switch_identifier"]
        ]
        if stops:
            return {
                "state": "STOPPED_LATCHED",
                "work_allowed": False,
                "reason": "KILL_SWITCH_TRIGGERED",
                "stop_id": stops[-1]["stop_id"],
            }
        return {"state": "STOPPED", "work_allowed": False, "reason": "POLICY_INACTIVE"}

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_accounts = set()
        seen_identifiers = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Provider-paper-kill-switch record {index} has been modified.")
            policies, reasons = resolve_pinned_records(
                self.policy_ledger.verify(),
                [record.get("policy_id")],
                [record.get("policy_record_hash")],
                id_field="policy_id",
                label="provider-paper risk policy",
            )
            if reasons or len(policies) != 1:
                raise LedgerIntegrityError(f"Provider-paper-kill-switch record {index} lost its policy.")
            policy = policies[0]
            try:
                triggered = _timestamp(record.get("triggered_at"))
                recorded = _timestamp(record.get("recorded_at"))
                source = _required(record.get("trigger_source"), "trigger_source", 50).upper()
                reason = _required(record.get("reason"), "reason")
                triggered_by = _required(record.get("triggered_by"), "triggered_by", 100)
                account_hash = _required(
                    record.get("account_reference_sha256"),
                    "account_reference_sha256",
                    64,
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Provider-paper-kill-switch record {index} has invalid values."
                ) from error
            expected_id = _stop_id(
                policy["policy_id"],
                policy["record_hash"],
                account_hash,
                policy["kill_switch_identifier"],
                triggered.isoformat(),
                recorded.isoformat(),
                source,
                reason,
                triggered_by,
            )
            boundary = (
                set(record) == RECORD_FIELDS
                and record.get("schema_version") == SCHEMA_VERSION
                and record.get("stop_policy_version") == POLICY_VERSION
                and record.get("stop_id") == expected_id
                and account_hash not in seen_accounts
                and policy["kill_switch_identifier"] not in seen_identifiers
                and record.get("record_type") == "PROVIDER_PAPER_KILL_SWITCH_LATCHED_STOP"
                and record.get("status") == "STOPPED_LATCHED"
                and record.get("policy_id") == policy["policy_id"]
                and record.get("policy_record_hash") == policy["record_hash"]
                and account_hash == policy["account_reference_sha256"]
                and record.get("kill_switch_identifier") == policy["kill_switch_identifier"]
                and triggered >= _timestamp(policy["recorded_at"])
                and triggered <= recorded + MAX_CLOCK_SKEW
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and (
                    index == 1
                    or recorded >= _timestamp(records[index - 2].get("recorded_at"))
                )
                and source in ALLOWED_SOURCES
                and record.get("trading_halted") is True
                and all(record.get(field) is False for field in FIXED_FALSE_FIELDS)
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Provider-paper-kill-switch record {index} violates its boundary."
                )
            seen_accounts.add(account_hash)
            seen_identifiers.add(policy["kill_switch_identifier"])
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (
                    item
                    for item in records
                    if item["account_reference_sha256"]
                    == result["account_reference_sha256"]
                    or item["kill_switch_identifier"] == result["kill_switch_identifier"]
                ),
                None,
            )
            if existing:
                logical_fields = (
                    "policy_id",
                    "policy_record_hash",
                    "account_reference_sha256",
                    "kill_switch_identifier",
                    "trigger_source",
                    "reason",
                    "triggered_by",
                )
                if allow_existing and all(
                    existing[field] == result[field] for field in logical_fields
                ):
                    return existing
                raise LedgerIntegrityError(
                    "Provider-paper account or kill-switch identity is already stopped and latched."
                )
            recorded = datetime.now(timezone.utc)
            if records:
                prior_recorded = _timestamp(records[-1]["recorded_at"])
                if recorded < prior_recorded:
                    recorded = prior_recorded
            completed = {
                **result,
                "recorded_at": recorded.isoformat(),
                "stop_id": _stop_id(
                    result["policy_id"],
                    result["policy_record_hash"],
                    result["account_reference_sha256"],
                    result["kill_switch_identifier"],
                    result["triggered_at"],
                    recorded.isoformat(),
                    result["trigger_source"],
                    result["reason"],
                    result["triggered_by"],
                ),
            }
            material = {
                **completed,
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
