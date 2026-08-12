from __future__ import annotations

"""Immutable sourced corporate-action evidence; no return is calculated here."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.broker.local_paper_execution import LocalPaperExecutionLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp


CORPORATE_ACTION_SCHEMA_VERSION = "1.0"
MAX_CLOCK_SKEW = timedelta(minutes=5)
COMPLETENESS_STATUSES = {"COMPLETE", "UNCERTAIN"}
EVENT_TYPES = {"CASH_DIVIDEND", "STOCK_SPLIT", "OTHER"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete append-only corporate-action write")
        written += count


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _positive_decimal(value: Any, name: str) -> str:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a positive finite decimal") from error
    if not resolved.is_finite() or resolved <= 0:
        raise ValueError(f"{name} must be a positive finite decimal")
    return format(resolved.normalize(), "f")


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").lower()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _event_time(event: Mapping[str, Any]) -> datetime:
    field = "ex_at" if event.get("event_type") == "CASH_DIVIDEND" else "effective_at"
    return _as_datetime(event.get(field))


def _normalise_event(
    value: Mapping[str, Any], *, covers_from: datetime, through: datetime
) -> dict[str, Any]:
    event_type = str(value.get("event_type") or "").upper()
    if event_type not in EVENT_TYPES:
        raise ValueError(f"event_type must be one of: {', '.join(sorted(EVENT_TYPES))}")
    common = {
        "event_type": event_type,
        "source_event_id": _required(value.get("source_event_id"), "source_event_id"),
    }
    if event_type == "CASH_DIVIDEND":
        ex_at = _as_datetime(value.get("ex_at"))
        payment_value = value.get("payment_at")
        payment_at = _as_datetime(payment_value) if payment_value else None
        if payment_at is not None and payment_at < ex_at:
            raise ValueError("dividend payment_at cannot predate ex_at")
        currency = str(value.get("currency") or "").upper()
        if not CURRENCY_PATTERN.fullmatch(currency):
            raise ValueError("dividend currency must be a three-letter code")
        normalised = {
            **common,
            "ex_at": ex_at.isoformat(),
            "payment_at": payment_at.isoformat() if payment_at else None,
            "amount_per_share": _positive_decimal(
                value.get("amount_per_share"), "amount_per_share"
            ),
            "currency": currency,
        }
    elif event_type == "STOCK_SPLIT":
        effective_at = _as_datetime(value.get("effective_at"))
        normalised = {
            **common,
            "effective_at": effective_at.isoformat(),
            "numerator": _positive_decimal(value.get("numerator"), "numerator"),
            "denominator": _positive_decimal(value.get("denominator"), "denominator"),
        }
    else:
        effective_at = _as_datetime(value.get("effective_at"))
        normalised = {
            **common,
            "effective_at": effective_at.isoformat(),
            "description": _required(value.get("description"), "description"),
        }
    effective_at = _event_time(normalised)
    if not covers_from <= effective_at <= through:
        raise ValueError("corporate action falls outside the declared coverage interval")
    return normalised


def _evidence_id(fill_id: str, covers_from: str, through: str) -> str:
    key = _canonical_json([fill_id, covers_from, through])
    return "CAE-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32].upper()


def _source_evidence_hash(record: Mapping[str, Any]) -> str:
    material = {
        "fill_id": record.get("fill_id"),
        "ticker": record.get("ticker"),
        "covers_from_at": record.get("covers_from_at"),
        "through_at": record.get("through_at"),
        "completeness_status": record.get("completeness_status"),
        "uncertainty_reasons": record.get("uncertainty_reasons"),
        "events": record.get("events"),
        "data_source": record.get("data_source"),
        "source_version": record.get("source_version"),
        "source_input_sha256": record.get("source_input_sha256"),
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _economic_events_in_interval(
    events: Sequence[Mapping[str, Any]], start: datetime, end: datetime
) -> list[dict[str, Any]]:
    material = [
        {key: value for key, value in event.items() if key != "source_event_id"}
        for event in events
        if start <= _event_time(event) <= end
    ]
    return sorted(material, key=_canonical_json)


class CorporateActionLedger:
    """Append-only corporate-action coverage linked to verified simulated fills."""

    def __init__(
        self, path: str | Path, execution_ledger: LocalPaperExecutionLedger
    ) -> None:
        self.path = Path(path)
        self.execution_ledger = execution_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Corporate-action ledger has an incomplete final line; run explicit tail repair."
            )
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank corporate-action line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at corporate-action line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Corporate-action line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def record(
        self,
        *,
        fill_id: str,
        covers_from_at: str | datetime,
        through_at: str | datetime,
        data_source: str,
        source_version: str,
        source_input_sha256: str,
        events: Sequence[Mapping[str, Any]] = (),
        completeness_status: str = "COMPLETE",
        uncertainty_reasons: Sequence[str] = (),
        retrieved_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        fills = {item["fill_id"]: item for item in self.execution_ledger.verify()}
        fill = fills.get(str(fill_id))
        if fill is None:
            raise ValueError("A verified local simulated fill is required")
        covers_from = _as_datetime(covers_from_at)
        through = _as_datetime(through_at)
        retrieved = _as_datetime(retrieved_at or datetime.now(timezone.utc))
        fill_at = _as_datetime(fill["filled_at"])
        if covers_from > fill_at:
            raise ValueError("coverage must start at or before the simulated fill")
        if through < fill_at or through < covers_from:
            raise ValueError("coverage must extend from the fill to a valid later time")
        if retrieved < through:
            raise ValueError("retrieved_at cannot predate the covered-through time")
        if retrieved > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("retrieved_at cannot be in the future")
        completeness = str(completeness_status or "").upper()
        if completeness not in COMPLETENESS_STATUSES:
            raise ValueError("completeness_status must be COMPLETE or UNCERTAIN")
        reasons = sorted({_required(item, "uncertainty reason") for item in uncertainty_reasons})
        if (completeness == "UNCERTAIN") != bool(reasons):
            raise ValueError("UNCERTAIN evidence requires reasons and COMPLETE evidence forbids them")
        normalised_events = [
            _normalise_event(item, covers_from=covers_from, through=through)
            for item in events
        ]
        normalised_events.sort(
            key=lambda item: (
                _event_time(item).isoformat(),
                item["event_type"],
                item["source_event_id"],
            )
        )
        source_event_ids = [item["source_event_id"] for item in normalised_events]
        if len(source_event_ids) != len(set(source_event_ids)):
            raise ValueError("source_event_id values must be unique within evidence")
        if completeness == "UNCERTAIN":
            status = "UNCERTAIN"
        elif not normalised_events:
            status = "NO_EVENTS"
        elif any(item["event_type"] == "OTHER" for item in normalised_events):
            status = "UNSUPPORTED_EVENTS_PRESENT"
        else:
            status = "SUPPORTED_EVENTS_PRESENT"
        record = {
            "schema_version": CORPORATE_ACTION_SCHEMA_VERSION,
            "evidence_id": _evidence_id(
                fill["fill_id"], covers_from.isoformat(), through.isoformat()
            ),
            "record_type": "CORPORATE_ACTION_EVIDENCE",
            "status": status,
            "performance_claim": False,
            "total_return_calculated": False,
            "learning_eligible": False,
            "fill_id": fill["fill_id"],
            "execution_record_hash": fill["record_hash"],
            "order_id": fill["order_id"],
            "decision_id": fill["decision_id"],
            "portfolio_version": fill["portfolio_version"],
            "ticker": fill["ticker"],
            "covers_from_at": covers_from.isoformat(),
            "through_at": through.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "completeness_status": completeness,
            "uncertainty_reasons": reasons,
            "events": normalised_events,
            "data_source": _required(data_source, "data_source"),
            "source_version": _required(source_version, "source_version"),
            "source_input_sha256": _sha256(
                source_input_sha256, "source_input_sha256"
            ),
            "strategy_version": fill["strategy_version"],
            "model_versions": fill["model_versions"],
            "git_revision": fill["git_revision"],
        }
        record["source_evidence_sha256"] = _source_evidence_hash(record)
        return self._append(record, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        fills = {item["fill_id"]: item for item in self.execution_ledger.verify()}
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        seen_coverage: set[tuple[str, str, str]] = set()
        verified_intervals: list[
            tuple[str, datetime, datetime, str, list[dict[str, Any]]]
        ] = []
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Corporate-action chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Corporate-action record {index} has been modified."
                )
            fill = fills.get(str(record.get("fill_id") or ""))
            if fill is None:
                raise LedgerIntegrityError(
                    f"Corporate-action record {index} has no verified fill."
                )
            try:
                covers_from = _as_datetime(record.get("covers_from_at"))
                through = _as_datetime(record.get("through_at"))
                retrieved = _as_datetime(record.get("retrieved_at"))
                fill_at = _as_datetime(fill["filled_at"])
                completeness = str(record.get("completeness_status") or "")
                reasons = sorted(
                    {_required(item, "uncertainty reason") for item in record.get("uncertainty_reasons", [])}
                )
                events = [
                    _normalise_event(item, covers_from=covers_from, through=through)
                    for item in record.get("events", [])
                ]
                events.sort(
                    key=lambda item: (
                        _event_time(item).isoformat(),
                        item["event_type"],
                        item["source_event_id"],
                    )
                )
                _required(record.get("data_source"), "data_source")
                _required(record.get("source_version"), "source_version")
                _sha256(record.get("source_input_sha256"), "source_input_sha256")
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Corporate-action record {index} has invalid values."
                ) from error
            event_ids = [item["source_event_id"] for item in events]
            expected_status = (
                "UNCERTAIN"
                if completeness == "UNCERTAIN"
                else "NO_EVENTS"
                if not events
                else "UNSUPPORTED_EVENTS_PRESENT"
                if any(item["event_type"] == "OTHER" for item in events)
                else "SUPPORTED_EVENTS_PRESENT"
            )
            expected_id = _evidence_id(
                fill["fill_id"], covers_from.isoformat(), through.isoformat()
            )
            coverage_key = (
                fill["fill_id"],
                covers_from.isoformat(),
                through.isoformat(),
            )
            linked = all(
                record.get(key) == fill.get(source_key)
                for key, source_key in {
                    "execution_record_hash": "record_hash",
                    "order_id": "order_id",
                    "decision_id": "decision_id",
                    "portfolio_version": "portfolio_version",
                    "ticker": "ticker",
                    "strategy_version": "strategy_version",
                    "model_versions": "model_versions",
                    "git_revision": "git_revision",
                }.items()
            )
            boundary = (
                record.get("schema_version") == CORPORATE_ACTION_SCHEMA_VERSION
                and record.get("record_type") == "CORPORATE_ACTION_EVIDENCE"
                and record.get("status") == expected_status
                and record.get("performance_claim") is False
                and record.get("total_return_calculated") is False
                and record.get("learning_eligible") is False
                and record.get("evidence_id") == expected_id
                and expected_id not in seen_ids
                and coverage_key not in seen_coverage
                and completeness in COMPLETENESS_STATUSES
                and ((completeness == "UNCERTAIN") == bool(reasons))
                and record.get("uncertainty_reasons") == reasons
                and record.get("events") == events
                and len(event_ids) == len(set(event_ids))
                and covers_from <= fill_at <= through <= retrieved
                and retrieved <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("source_evidence_sha256")
                == _source_evidence_hash(record)
            )
            if not linked or not boundary:
                raise LedgerIntegrityError(
                    f"Corporate-action record {index} violates its boundary."
                )
            if completeness == "COMPLETE":
                for (
                    prior_fill_id,
                    prior_start,
                    prior_end,
                    prior_completeness,
                    prior_events,
                ) in verified_intervals:
                    overlap_start = max(covers_from, prior_start)
                    overlap_end = min(through, prior_end)
                    if (
                        prior_fill_id == fill["fill_id"]
                        and prior_completeness == "COMPLETE"
                        and overlap_start <= overlap_end
                        and _economic_events_in_interval(
                            events, overlap_start, overlap_end
                        )
                        != _economic_events_in_interval(
                            prior_events, overlap_start, overlap_end
                        )
                    ):
                        raise LedgerIntegrityError(
                            "Complete corporate-action evidence conflicts across "
                            f"overlapping coverage at record {index}."
                        )
            seen_ids.add(expected_id)
            seen_coverage.add(coverage_key)
            verified_intervals.append(
                (fill["fill_id"], covers_from, through, completeness, events)
            )
            previous_hash = record["record_hash"]
        return records

    def evidence_for(
        self, *, fill_id: str, through_at: str | datetime
    ) -> dict[str, Any] | None:
        target = _as_datetime(through_at)
        fill = next(
            (item for item in self.execution_ledger.verify() if item["fill_id"] == fill_id),
            None,
        )
        if fill is None:
            raise ValueError("A verified local simulated fill is required")
        fill_at = _as_datetime(fill["filled_at"])
        candidates = [
            item
            for item in self.verify()
            if item["fill_id"] == fill_id
            and _as_datetime(item["covers_from_at"]) <= fill_at
            and _as_datetime(item["through_at"]) >= target
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: (
                item["completeness_status"] != "COMPLETE",
                _as_datetime(item["through_at"]),
            ),
        )

    def no_event_check(
        self, *, fill_id: str, through_at: str | datetime
    ) -> dict[str, Any]:
        evidence = self.evidence_for(fill_id=fill_id, through_at=through_at)
        if evidence is None:
            raise ValueError("No verified corporate-action coverage is available")
        if evidence["status"] != "NO_EVENTS":
            raise ValueError("Verified corporate-action evidence contains or may contain events")
        return {
            "status": "NO_EVENTS",
            "data_source": evidence["data_source"],
            "source_version": evidence["source_version"],
            "checked_at": evidence["retrieved_at"],
            "covers_from_at": evidence["covers_from_at"],
            "through_at": evidence["through_at"],
            "evidence_sha256": evidence["source_evidence_sha256"],
            "evidence_id": evidence["evidence_id"],
            "evidence_record_hash": evidence["record_hash"],
        }

    def _append(
        self, evidence: dict[str, Any], *, allow_existing: bool
    ) -> dict[str, Any]:
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
                    if item["evidence_id"] == evidence["evidence_id"]
                ),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "retrieved_at"}
                comparable = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in evidence.items() if key not in ignored}
                if allow_existing and comparable == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Corporate-action evidence {evidence['evidence_id']} already exists."
                )
            if evidence["completeness_status"] == "COMPLETE":
                candidate_start = _as_datetime(evidence["covers_from_at"])
                candidate_end = _as_datetime(evidence["through_at"])
                for prior in records:
                    overlap_start = max(
                        candidate_start, _as_datetime(prior["covers_from_at"])
                    )
                    overlap_end = min(
                        candidate_end, _as_datetime(prior["through_at"])
                    )
                    if (
                        prior["fill_id"] == evidence["fill_id"]
                        and prior["completeness_status"] == "COMPLETE"
                        and overlap_start <= overlap_end
                        and _economic_events_in_interval(
                            evidence["events"], overlap_start, overlap_end
                        )
                        != _economic_events_in_interval(
                            prior["events"], overlap_start, overlap_end
                        )
                    ):
                        raise LedgerIntegrityError(
                            "Complete corporate-action evidence conflicts across "
                            "overlapping coverage."
                        )
            material = {
                **evidence,
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
                json.loads(tail.decode("utf-8"))
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
