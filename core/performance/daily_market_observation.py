from __future__ import annotations

"""Immutable daily market-close evidence; no portfolio value is calculated."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from core.broker.local_paper_execution import LocalPaperExecutionLedger
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.portfolio_valuation import (
    _canonical_json,
    _decimal_string,
    _fraction_material,
    _record_hash,
    _write_all,
)


DAILY_MARKET_OBSERVATION_SCHEMA_VERSION = "1.0"
DAILY_MARKET_OBSERVATION_POLICY_VERSION = "us-regular-session-unadjusted-close-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
CONTEMPORANEOUS_DELAY = timedelta(hours=72)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
NEW_YORK = ZoneInfo("America/New_York")


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _price(value: Any) -> Fraction:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("close_price must be a positive finite exact decimal") from error
    if not resolved.is_finite() or resolved <= 0:
        raise ValueError("close_price must be a positive finite exact decimal")
    return Fraction(resolved)


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").lower()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _https_uri(value: Any) -> str:
    resolved = _required(value, "source_uri")
    parsed = urlsplit(resolved)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("source_uri must be an HTTPS URL with a hostname")
    if parsed.username or parsed.password:
        raise ValueError("source_uri cannot contain credentials")
    return resolved


def _session_date(value: Any) -> str:
    resolved = _required(value, "market_session_date")
    try:
        return date.fromisoformat(resolved).isoformat()
    except ValueError as error:
        raise ValueError("market_session_date must be an ISO calendar date") from error


def _observation_id(fill_id: str, session_date: str) -> str:
    material = [fill_id, session_date, DAILY_MARKET_OBSERVATION_POLICY_VERSION]
    return "DMOBS-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _source_hash(record: Mapping[str, Any]) -> str:
    material = {
        key: record.get(key)
        for key in (
            "ticker",
            "market_session_date",
            "close_price",
            "exact_close_price",
            "price_basis",
            "market_session_status",
            "price_effective_at",
            "retrieved_at",
            "provider",
            "source_version",
            "source_uri",
            "source_input_sha256",
        )
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


class DailyMarketObservationLedger:
    """Append-only per-fill official-close evidence for later daily valuation."""

    def __init__(self, path: str | Path, execution_ledger: LocalPaperExecutionLedger) -> None:
        self.path = Path(path)
        self.execution_ledger = execution_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Daily-market-observation ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank daily-market-observation line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at daily-market-observation line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Daily-market-observation line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def observe(
        self,
        *,
        fill_id: str,
        market_session_date: str,
        close_price: Any,
        price_effective_at: str | datetime,
        retrieved_at: str | datetime,
        provider: str,
        source_version: str,
        source_uri: str,
        source_input_sha256: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        resolved_fill_id = _required(fill_id, "fill_id")
        fill = next(
            (item for item in self.execution_ledger.verify() if item.get("fill_id") == resolved_fill_id),
            None,
        )
        if fill is None:
            raise ValueError("A verified simulated fill is required")
        session = _session_date(market_session_date)
        price = _price(close_price)
        effective = _as_datetime(price_effective_at)
        retrieved = _as_datetime(retrieved_at)
        recorded = _as_datetime(recorded_at or datetime.now(timezone.utc))
        filled = _as_datetime(fill["filled_at"])
        if effective.astimezone(NEW_YORK).date().isoformat() != session:
            raise ValueError("market_session_date must match the New York effective date")
        if effective.astimezone(NEW_YORK).time() != time(16, 0):
            raise ValueError(
                "v1 accepts only a regular-session close exactly at 16:00 New York time"
            )
        if effective < filled:
            raise ValueError("Daily close cannot predate the simulated fill")
        if retrieved < effective:
            raise ValueError("retrieved_at cannot predate the official close")
        if recorded < retrieved:
            raise ValueError("recorded_at cannot predate retrieval")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        record = {
            "schema_version": DAILY_MARKET_OBSERVATION_SCHEMA_VERSION,
            "policy_version": DAILY_MARKET_OBSERVATION_POLICY_VERSION,
            "observation_id": _observation_id(resolved_fill_id, session),
            "status": "OBSERVED",
            "record_type": "SIMULATED_POSITION_DAILY_MARKET_CLOSE_EVIDENCE",
            "simulation_only": True,
            "fill_id": resolved_fill_id,
            "execution_record_hash": fill["record_hash"],
            "order_id": fill["order_id"],
            "decision_id": fill["decision_id"],
            "portfolio_version": fill["portfolio_version"],
            "ticker": fill["ticker"],
            "filled_quantity": fill["filled_quantity"],
            "filled_at": fill["filled_at"],
            "market_session_date": session,
            "close_price": _decimal_string(price),
            "exact_close_price": _fraction_material(price),
            "price_basis": "UNADJUSTED_CLOSE",
            "market_session_status": "OFFICIAL_CLOSE",
            "price_effective_at": effective.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "recorded_at": recorded.isoformat(),
            "availability": (
                "CONTEMPORANEOUS"
                if retrieved <= effective + CONTEMPORANEOUS_DELAY
                else "BACKFILLED_AFTER_72_HOURS"
            ),
            "provider": _required(provider, "provider"),
            "source_version": _required(source_version, "source_version"),
            "source_uri": _https_uri(source_uri),
            "source_input_sha256": _sha256(source_input_sha256, "source_input_sha256"),
            "daily_portfolio_valuation_calculated": False,
            "performance_metric_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "strategy_version": fill["strategy_version"],
            "model_versions": fill["model_versions"],
            "git_revision": fill["git_revision"],
        }
        record["source_observation_sha256"] = _source_hash(record)
        return self._append(record, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        fills = {item["fill_id"]: item for item in self.execution_ledger.verify()}
        previous_hash = GENESIS_HASH
        seen_ids = set()
        seen_keys = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Daily-market-observation chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Daily-market-observation record {index} has been modified."
                )
            fill = fills.get(record.get("fill_id"))
            if fill is None:
                raise LedgerIntegrityError(
                    f"Daily-market-observation record {index} lost its fill."
                )
            try:
                session = _session_date(record.get("market_session_date"))
                price = _price(record.get("close_price"))
                exact_material = record.get("exact_close_price") or {}
                exact_price = Fraction(
                    int(exact_material.get("numerator")),
                    int(exact_material.get("denominator")),
                )
                effective = _as_datetime(record.get("price_effective_at"))
                retrieved = _as_datetime(record.get("retrieved_at"))
                recorded = _as_datetime(record.get("recorded_at"))
                _required(record.get("provider"), "provider")
                _required(record.get("source_version"), "source_version")
                _https_uri(record.get("source_uri"))
                _sha256(record.get("source_input_sha256"), "source_input_sha256")
            except (TypeError, ValueError, ZeroDivisionError) as error:
                raise LedgerIntegrityError(
                    f"Daily-market-observation record {index} has invalid values."
                ) from error
            key = (fill["fill_id"], session)
            expected_availability = (
                "CONTEMPORANEOUS"
                if retrieved <= effective + CONTEMPORANEOUS_DELAY
                else "BACKFILLED_AFTER_72_HOURS"
            )
            linked = all(
                record.get(field) == fill.get(source)
                for field, source in (
                    ("execution_record_hash", "record_hash"),
                    ("order_id", "order_id"),
                    ("decision_id", "decision_id"),
                    ("portfolio_version", "portfolio_version"),
                    ("ticker", "ticker"),
                    ("filled_quantity", "filled_quantity"),
                    ("filled_at", "filled_at"),
                    ("strategy_version", "strategy_version"),
                    ("model_versions", "model_versions"),
                    ("git_revision", "git_revision"),
                )
            )
            boundary = (
                record.get("schema_version") == DAILY_MARKET_OBSERVATION_SCHEMA_VERSION
                and record.get("policy_version") == DAILY_MARKET_OBSERVATION_POLICY_VERSION
                and record.get("observation_id") == _observation_id(fill["fill_id"], session)
                and record.get("observation_id") not in seen_ids
                and key not in seen_keys
                and record.get("status") == "OBSERVED"
                and record.get("record_type") == "SIMULATED_POSITION_DAILY_MARKET_CLOSE_EVIDENCE"
                and record.get("simulation_only") is True
                and exact_price == price
                and record.get("exact_close_price") == _fraction_material(price)
                and record.get("price_basis") == "UNADJUSTED_CLOSE"
                and record.get("market_session_status") == "OFFICIAL_CLOSE"
                and effective.astimezone(NEW_YORK).date().isoformat() == session
                and effective.astimezone(NEW_YORK).time() == time(16, 0)
                and effective >= _as_datetime(fill["filled_at"])
                and retrieved >= effective
                and recorded >= retrieved
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("availability") == expected_availability
                and record.get("source_observation_sha256") == _source_hash(record)
                and record.get("daily_portfolio_valuation_calculated") is False
                and record.get("performance_metric_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
            )
            if not linked or not boundary:
                raise LedgerIntegrityError(
                    f"Daily-market-observation record {index} violates its boundary."
                )
            seen_ids.add(record["observation_id"])
            seen_keys.add(key)
            previous_hash = record["record_hash"]
        return records

    def _append(self, observation: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["observation_id"] == observation["observation_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in observation.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(
                    "Conflicting daily close evidence requires an explicit future supersession policy."
                )
            material = {
                **observation,
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
                backup_descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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
