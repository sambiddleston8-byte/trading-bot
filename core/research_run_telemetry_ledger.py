from __future__ import annotations

"""Append-only, tamper-evident observations of research-run performance.

This ledger measures research attempts.  It has no authority over research
decisions, provider selection, retries, caching, order creation, or trading.
"""

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.portfolio_valuation import _canonical_json, _record_hash


TELEMETRY_SCHEMA_VERSION = "1.0"
AUTHORITY_POLICY = (
    "TELEMETRY_OBSERVATION_ONLY_NOT_RESEARCH_DECISION_OR_RETRY_CACHE_AUTHORITY"
)
ALLOWED_OUTCOMES = {"COMPLETE", "ERROR"}
ALLOWED_OBSERVATION_KEYS = {
    "component",
    "provider",
    "duration_ms",
    "retry_count",
    "cache_hit",
}
RECORD_KEYS = {
    "schema_version",
    "observation_id",
    "batch_id",
    "ticker",
    "sequence_index",
    "outcome",
    "error_type",
    "wall_duration_ms",
    "pacing_delay_seconds_configured",
    "component_observations",
    "observed_at",
    "authority_policy",
    "previous_hash",
    "record_hash",
}
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TELEMETRY_LEDGER_PATH = (
    PROJECT_ROOT / "data" / "research" / "research_run_telemetry.jsonl"
)


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete append-only research-telemetry write")
        written += count


def _observation_id(batch_id: str, ticker: str, sequence_index: int) -> str:
    identity = {
        "batch_id": batch_id,
        "ticker": ticker,
        "sequence_index": sequence_index,
    }
    return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()


def _finite_non_negative(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite non-negative number")
    try:
        resolved = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite non-negative number") from error
    if not math.isfinite(resolved) or resolved < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return resolved


def _identifier(value: Any, name: str) -> str:
    resolved = str(value or "")
    if not _IDENTIFIER_RE.fullmatch(resolved):
        raise ValueError(f"{name} must be a safe identifier")
    return resolved


def _component_observations(
    observations: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if observations is None:
        return None
    if isinstance(observations, (str, bytes)):
        raise ValueError("component_observations must be a sequence of objects")
    resolved = []
    for item in observations:
        if not isinstance(item, Mapping):
            raise ValueError("Each component observation must be an object")
        unknown = set(item) - ALLOWED_OBSERVATION_KEYS
        if unknown:
            raise ValueError("Component observations contain unsupported fields")
        if "component" not in item:
            raise ValueError("Each component observation requires a component")
        normalized: dict[str, Any] = {
            "component": _identifier(item["component"], "component")
        }
        if "provider" in item:
            normalized["provider"] = (
                None
                if item["provider"] is None
                else _identifier(item["provider"], "provider")
            )
        if "duration_ms" in item:
            normalized["duration_ms"] = _finite_non_negative(
                item["duration_ms"], "duration_ms"
            )
        if "retry_count" in item:
            retry_count = item["retry_count"]
            if isinstance(retry_count, bool) or not isinstance(retry_count, int) or retry_count < 0:
                raise ValueError("retry_count must be a non-negative integer")
            normalized["retry_count"] = retry_count
        if "cache_hit" in item:
            if not isinstance(item["cache_hit"], bool):
                raise ValueError("cache_hit must be a boolean")
            normalized["cache_hit"] = item["cache_hit"]
        resolved.append(normalized)
    return resolved


class ResearchRunTelemetryLedger:
    """Write-once JSONL ledger of research-run duration and outcome."""

    def __init__(self, path: str | Path = DEFAULT_TELEMETRY_LEDGER_PATH) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Research-telemetry ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank research-telemetry line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at research-telemetry line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Research-telemetry line {line_number} is not an object."
                    )
                records.append(record)
        return records

    @staticmethod
    def _validate_record(record: Mapping[str, Any], index: int) -> None:
        try:
            if set(record) != RECORD_KEYS:
                raise ValueError("record fields do not match the closed schema")
            batch_id = _identifier(record.get("batch_id"), "batch_id")
            ticker = _identifier(record.get("ticker"), "ticker").upper()
            sequence_index = record.get("sequence_index")
            if (
                isinstance(sequence_index, bool)
                or not isinstance(sequence_index, int)
                or sequence_index < 0
            ):
                raise ValueError("sequence_index must be a non-negative integer")
            outcome = str(record.get("outcome") or "")
            if outcome not in ALLOWED_OUTCOMES:
                raise ValueError("outcome is invalid")
            error_type = record.get("error_type")
            if outcome == "ERROR":
                _identifier(error_type, "error_type")
            elif error_type is not None:
                raise ValueError("COMPLETE observations cannot have an error_type")
            _finite_non_negative(record.get("wall_duration_ms"), "wall_duration_ms")
            _finite_non_negative(
                record.get("pacing_delay_seconds_configured"),
                "pacing_delay_seconds_configured",
            )
            _component_observations(record.get("component_observations"))
            canonical_timestamp(record.get("observed_at"))
            expected_id = _observation_id(batch_id, ticker, sequence_index)
            if record.get("observation_id") != expected_id:
                raise ValueError("observation_id does not match its identity")
            if record.get("schema_version") != TELEMETRY_SCHEMA_VERSION:
                raise ValueError("schema_version is invalid")
            if record.get("authority_policy") != AUTHORITY_POLICY:
                raise ValueError("authority_policy is invalid")
        except (TypeError, ValueError) as error:
            raise LedgerIntegrityError(
                f"Research-telemetry record {index} violates its boundary."
            ) from error

    def verify(self) -> list[dict[str, Any]]:
        records = self.records()
        previous_hash = GENESIS_HASH
        seen_ids = set()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Research-telemetry chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Research-telemetry record {index} has been modified."
                )
            self._validate_record(record, index)
            observation_id = record["observation_id"]
            if observation_id in seen_ids:
                raise LedgerIntegrityError(
                    f"Research-telemetry observation is duplicated at record {index}."
                )
            seen_ids.add(observation_id)
            previous_hash = record["record_hash"]
        return records

    def append_observation(
        self,
        *,
        batch_id: str,
        ticker: str,
        sequence_index: int,
        outcome: str,
        wall_duration_ms: float,
        pacing_delay_seconds_configured: float,
        error_type: str | None = None,
        component_observations: Sequence[Mapping[str, Any]] | None = None,
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        resolved_batch_id = _identifier(batch_id, "batch_id")
        resolved_ticker = _identifier(ticker, "ticker").upper()
        if (
            isinstance(sequence_index, bool)
            or not isinstance(sequence_index, int)
            or sequence_index < 0
        ):
            raise ValueError("sequence_index must be a non-negative integer")
        resolved_outcome = str(outcome or "").upper()
        if resolved_outcome not in ALLOWED_OUTCOMES:
            raise ValueError("outcome must be COMPLETE or ERROR")
        if resolved_outcome == "ERROR":
            resolved_error_type = _identifier(error_type, "error_type")
        elif error_type is not None:
            raise ValueError("COMPLETE observations cannot have an error_type")
        else:
            resolved_error_type = None
        resolved_components = _component_observations(component_observations)
        result = {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "observation_id": _observation_id(
                resolved_batch_id, resolved_ticker, sequence_index
            ),
            "batch_id": resolved_batch_id,
            "ticker": resolved_ticker,
            "sequence_index": sequence_index,
            "outcome": resolved_outcome,
            "error_type": resolved_error_type,
            "wall_duration_ms": _finite_non_negative(
                wall_duration_ms, "wall_duration_ms"
            ),
            "pacing_delay_seconds_configured": _finite_non_negative(
                pacing_delay_seconds_configured,
                "pacing_delay_seconds_configured",
            ),
            "component_observations": resolved_components,
            "observed_at": canonical_timestamp(
                observed_at or datetime.now(timezone.utc).isoformat()
            ),
            "authority_policy": AUTHORITY_POLICY,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (
                    item
                    for item in records
                    if item["observation_id"] == result["observation_id"]
                ),
                None,
            )
            if existing:
                current = {
                    key: value
                    for key, value in existing.items()
                    if key not in {"previous_hash", "record_hash", "observed_at"}
                }
                proposed = {
                    key: value for key, value in result.items() if key != "observed_at"
                }
                if current == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Research telemetry {result['observation_id']} already exists with different content."
                )
            material = {
                **result,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
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
                decoded_tail = json.loads(tail.decode("utf-8"))
                if not isinstance(decoded_tail, dict):
                    raise json.JSONDecodeError("not an object", "", 0)
            except (UnicodeDecodeError, json.JSONDecodeError):
                backup = self.path.with_suffix(
                    self.path.suffix + f".incomplete-tail-{uuid4().hex}"
                )
                backup_descriptor = os.open(
                    backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
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


def default_sink(
    path: str | Path = DEFAULT_TELEMETRY_LEDGER_PATH,
) -> Callable[[dict[str, Any]], None]:
    ledger = ResearchRunTelemetryLedger(path)

    def sink(observation: dict[str, Any]) -> None:
        ledger.append_observation(**observation)

    return sink
