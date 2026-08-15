from __future__ import annotations

"""Append-only audit trail for synthetic VectorBT pilot runs.

This ledger is deliberately separate from `ReplayRunAuditLedger`.  It stores a
different `record_type`, pins no authenticated evidence roles and grants no
authority.  Nothing here is admissible replay evidence, and no record may be
copied into an authenticated or performance ledger.
"""

from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
from typing import Any

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.research.vectorbt_pilot import (
    ASSUMPTIONS_VERSION,
    FIXED_FALSE,
    POLICY_VERSION as PILOT_POLICY_VERSION,
    VECTORBT_LICENCE,
    VECTORBT_PINNED_VERSION,
    VectorBTPilotResult,
    _digest,
    _required,
    _sha256,
)
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "vectorbt-synthetic-pilot-audit-v1"
RECORD_TYPE = "VECTORBT_SYNTHETIC_PILOT_ONLY"
STATUS = "SYNTHETIC_PILOT_DIAGNOSTIC_NOT_EVIDENCE"
MAX_CLOCK_SKEW = timedelta(minutes=5)


def _time(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _pilot_run_id(material: dict[str, Any]) -> str:
    return "VBT-PILOT-" + _sha256([material, POLICY_VERSION])[:32].upper()


class VectorBTPilotAuditLedger:
    """One immutable record per deterministic pilot run identity."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Pilot audit ledger has an incomplete final line.")
        values: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank pilot audit line at {line_number}.")
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid pilot audit JSON at line {line_number}."
                    ) from error
                if not isinstance(value, dict):
                    raise LedgerIntegrityError(
                        f"Pilot audit line {line_number} is not an object."
                    )
                values.append(value)
        return values

    @staticmethod
    def _identity(result: VectorBTPilotResult) -> dict[str, Any]:
        return {
            "input_sha256": result.input_sha256,
            "attestation_sha256": result.attestation_sha256,
            "assumptions_sha256": result.assumptions_sha256,
            "strategy_version": result.strategy_version,
            "policy_version": PILOT_POLICY_VERSION,
            "vectorbt_version": result.vectorbt_version,
        }

    def append(
        self,
        *,
        result: VectorBTPilotResult,
        recorded_by: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        if type(result) is not VectorBTPilotResult:
            raise ValueError("only a VectorBTPilotResult may be recorded")
        material = result.material()
        if any(material.get(field) is not False for field in FIXED_FALSE):
            raise ValueError("pilot result exceeds its simulation-only authority boundary")
        if material.get("cash_settlement_modelled") is not False:
            raise ValueError(
                "the pilot models no cash settlement; this must be recorded as False"
            )
        if result.assumptions_version != ASSUMPTIONS_VERSION:
            raise ValueError("pilot assumptions version is not the supported version")
        if result.vectorbt_version != VECTORBT_PINNED_VERSION:
            raise ValueError("pilot dependency version is not the pinned version")
        if result.vectorbt_licence != VECTORBT_LICENCE:
            raise ValueError("pilot dependency licence record changed")
        for name in ("input_sha256", "attestation_sha256", "assumptions_sha256"):
            _digest(getattr(result, name), name)

        identity = self._identity(result)
        run_id = _pilot_run_id(identity)
        recorded = _time(recorded_at or datetime.now(timezone.utc), "recorded_at")
        now = datetime.now(timezone.utc)
        if not now - MAX_CLOCK_SKEW <= recorded <= now + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at must match the actual append time")
        record = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "pilot_run_id": run_id,
            "record_type": RECORD_TYPE,
            "status": STATUS,
            "recorded_at": recorded.isoformat(),
            "recorded_by": _required(recorded_by, "recorded_by", 100),
            "result_sha256": result.result_sha256(),
            "result": material,
        }
        return self._append(record, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous = GENESIS_HASH
        seen_ids: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            try:
                material = {
                    key: value for key, value in record.items() if key != "record_hash"
                }
                payload = record.get("result")
                if not isinstance(payload, dict):
                    raise ValueError("pilot result payload must be an object")
                identity = {
                    "input_sha256": payload["input_sha256"],
                    "attestation_sha256": payload["attestation_sha256"],
                    "assumptions_sha256": payload["assumptions_sha256"],
                    "strategy_version": payload["strategy_version"],
                    "policy_version": payload["policy_version"],
                    "vectorbt_version": payload["vectorbt_version"],
                }
                expected_id = _pilot_run_id(identity)
                recorded = _time(record.get("recorded_at"), "recorded_at")
                boundary = (
                    record.get("schema_version") == SCHEMA_VERSION
                    and record.get("policy_version") == POLICY_VERSION
                    and record.get("record_type") == RECORD_TYPE
                    and record.get("status") == STATUS
                    and record.get("pilot_run_id") == expected_id
                    and expected_id not in seen_ids
                    and record.get("previous_hash") == previous
                    and record.get("record_hash") == _record_hash(material)
                    and record.get("result_sha256") == _sha256(payload)
                    and payload.get("policy_version") == PILOT_POLICY_VERSION
                    and payload.get("assumptions_version") == ASSUMPTIONS_VERSION
                    and isinstance(payload.get("assumptions"), dict)
                    and _sha256(payload["assumptions"])
                    == payload.get("assumptions_sha256")
                    and payload.get("vectorbt_version") == VECTORBT_PINNED_VERSION
                    and payload.get("vectorbt_licence") == VECTORBT_LICENCE
                    and payload.get("simulation_only") is True
                    and payload.get("synthetic_data_only") is True
                    and payload.get("cash_settlement_modelled") is False
                    and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                    and bool(_required(record.get("recorded_by"), "recorded_by", 100))
                    and all(payload.get(field) is False for field in FIXED_FALSE)
                )
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Pilot audit record {index} is invalid.") from error
            if not boundary:
                raise LedgerIntegrityError(
                    f"Pilot audit record {index} violates its immutable boundary."
                )
            seen_ids.add(expected_id)
            previous = record["record_hash"]
        return records

    def _append(self, record: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            existing = self.verify()
            same_id = [
                item for item in existing if item.get("pilot_run_id") == record["pilot_run_id"]
            ]
            if same_id:
                ignored = {"recorded_at", "previous_hash", "record_hash"}
                comparable = {
                    key: value for key, value in same_id[0].items() if key not in ignored
                }
                retry = {key: value for key, value in record.items() if key not in ignored}
                if allow_existing and comparable == retry:
                    return same_id[0]
                raise LedgerIntegrityError(
                    "pilot run identity already exists with different audit content"
                )
            material = {
                **record,
                "previous_hash": existing[-1]["record_hash"] if existing else GENESIS_HASH,
            }
            complete = {**material, "record_hash": _record_hash(material)}
            target = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                _write_all(target, (_canonical_json(complete) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            return complete
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
