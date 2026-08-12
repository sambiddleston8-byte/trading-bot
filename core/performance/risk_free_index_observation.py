from __future__ import annotations

"""Immutable final SOFR Index evidence; no excess-return metric is calculated."""

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
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.portfolio_valuation import (
    _canonical_json,
    _decimal_string,
    _fraction_material,
    _record_hash,
    _write_all,
)


RISK_FREE_INDEX_SCHEMA_VERSION = "1.0"
RISK_FREE_INDEX_POLICY_VERSION = "ny-fed-final-sofr-index-v1"
PROVIDER = "FEDERAL_RESERVE_BANK_OF_NEW_YORK"
SERIES = "SOFR_INDEX"
SERIES_START = date(2018, 4, 2)
FINAL_REVISION_TIME = time(15, 0)
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
NEW_YORK = ZoneInfo("America/New_York")
METHODOLOGY_URI = (
    "https://www.newyorkfed.org/markets/reference-rates/"
    "additional-information-about-reference-rates"
)


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _value_date(value: Any) -> str:
    try:
        resolved = date.fromisoformat(_required(value, "value_date"))
    except ValueError as error:
        raise ValueError("value_date must be an ISO calendar date") from error
    if resolved < SERIES_START:
        raise ValueError("SOFR Index value_date cannot predate 2018-04-02")
    return resolved.isoformat()


def _index_value(value: Any) -> Fraction:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("index_value must be a positive finite exact decimal") from error
    if not decimal.is_finite() or decimal <= 0:
        raise ValueError("index_value must be a positive finite exact decimal")
    if decimal.as_tuple().exponent < -8:
        raise ValueError("official SOFR Index precision cannot exceed eight decimal places")
    return Fraction(decimal)


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").lower()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _official_uri(value: Any, expected_date: str) -> str:
    resolved = _required(value, "source_uri")
    parsed = urlsplit(resolved)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "markets.newyorkfed.org"
        or parsed.path != "/read"
        or parsed.username
        or parsed.password
    ):
        raise ValueError("source_uri must be the official credential-free New York Fed read endpoint")
    query = parse_qs(parsed.query)
    event_codes = query.get("eventCodes", query.get("eventCode", []))
    if (
        query.get("productCode") != ["50"]
        or event_codes != ["525"]
        or query.get("startDt") != [expected_date]
        or query.get("endDt") != [expected_date]
        or query.get("format") != ["json"]
    ):
        raise ValueError("source_uri must request exactly the recorded SOFR Index value date")
    return resolved


def _source_payload(value: str | bytes) -> tuple[str, Fraction, str, str, str]:
    raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
    if not raw or len(raw) > 1_000_000:
        raise ValueError("source_payload must be non-empty official JSON under one megabyte")
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text, parse_float=Decimal, parse_int=Decimal)
        rates = payload["refRates"]
        if not isinstance(rates, list) or len(rates) != 1:
            raise ValueError
        observation = rates[0]
        if not isinstance(observation, dict) or observation.get("type") != "SOFRAI":
            raise ValueError
    except (KeyError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(
            "source_payload must contain exactly one valid official SOFRAI observation"
        ) from error
    resolved_date = _value_date(observation.get("effectiveDate"))
    value = _index_value(observation.get("index"))
    revision = observation.get("revisionIndicator", "")
    if revision not in ("", "R"):
        raise ValueError("source_payload has an invalid revisionIndicator")
    return resolved_date, value, revision, hashlib.sha256(raw).hexdigest(), text


def _observation_id(value_date: str) -> str:
    material = [PROVIDER, SERIES, value_date, RISK_FREE_INDEX_POLICY_VERSION]
    return "RFOBS-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


def _source_hash(record: Mapping[str, Any]) -> str:
    material = {
        key: record.get(key)
        for key in (
            "provider",
            "series",
            "value_date",
            "index_value",
            "exact_index_value",
            "retrieved_at",
            "source_uri",
            "source_payload_sha256",
            "source_revision_indicator",
            "methodology_uri",
            "revision_status",
        )
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


class RiskFreeIndexObservationLedger:
    """Append-only final official SOFR Index values for later period matching."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Risk-free-index ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank risk-free-index line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at risk-free-index line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Risk-free-index line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def observe(
        self,
        *,
        source_payload: str | bytes,
        retrieved_at: str | datetime,
        source_uri: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        resolved_date, value, revision, payload_hash, payload_text = _source_payload(
            source_payload
        )
        retrieved = _as_datetime(retrieved_at)
        recorded = _as_datetime(recorded_at or datetime.now(timezone.utc))
        local_retrieval = retrieved.astimezone(NEW_YORK)
        final_at = datetime.combine(
            date.fromisoformat(resolved_date), FINAL_REVISION_TIME, NEW_YORK
        )
        if retrieved < final_at:
            raise ValueError(
                "retrieved_at must be after the conservative 15:00 New York revision window"
            )
        if recorded < retrieved:
            raise ValueError("recorded_at cannot predate retrieval")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        record = {
            "schema_version": RISK_FREE_INDEX_SCHEMA_VERSION,
            "policy_version": RISK_FREE_INDEX_POLICY_VERSION,
            "observation_id": _observation_id(resolved_date),
            "status": "OBSERVED_FINAL",
            "record_type": "OFFICIAL_RISK_FREE_INDEX_EVIDENCE",
            "evidence_only": True,
            "provider": PROVIDER,
            "series": SERIES,
            "currency": "USD",
            "value_date": resolved_date,
            "index_value": _decimal_string(value),
            "exact_index_value": _fraction_material(value),
            "retrieved_at": retrieved.isoformat(),
            "recorded_at": recorded.isoformat(),
            "availability": (
                "CONTEMPORANEOUS_FINAL_SAME_DAY"
                if local_retrieval.date().isoformat() == resolved_date
                else "BACKFILLED_FINAL"
            ),
            "revision_status": "FINAL_AFTER_SAME_DAY_REVISION_WINDOW",
            "source_uri": _official_uri(source_uri, resolved_date),
            "source_payload": payload_text,
            "source_payload_sha256": payload_hash,
            "source_revision_indicator": revision,
            "methodology_uri": METHODOLOGY_URI,
            "risk_free_period_return_calculated": False,
            "sharpe_calculated": False,
            "sortino_calculated": False,
            "performance_metric_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
            "provider_download_enabled": False,
        }
        record["source_observation_sha256"] = _source_hash(record)
        return self._append(record, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids = set()
        seen_dates = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(
                    f"Risk-free-index record {index} has been modified."
                )
            try:
                resolved_date = _value_date(record.get("value_date"))
                value = _index_value(record.get("index_value"))
                exact = record.get("exact_index_value") or {}
                exact_value = Fraction(
                    int(exact.get("numerator")), int(exact.get("denominator"))
                )
                retrieved = _as_datetime(record.get("retrieved_at"))
                recorded = _as_datetime(record.get("recorded_at"))
                (
                    payload_date,
                    payload_value,
                    payload_revision,
                    payload_hash,
                    payload_text,
                ) = _source_payload(record.get("source_payload"))
                source_uri = _official_uri(record.get("source_uri"), resolved_date)
                source_hash = _sha256(
                    record.get("source_payload_sha256"), "source_payload_sha256"
                )
            except (TypeError, ValueError, ZeroDivisionError) as error:
                raise LedgerIntegrityError(
                    f"Risk-free-index record {index} has invalid values."
                ) from error
            local_retrieval = retrieved.astimezone(NEW_YORK)
            final_at = datetime.combine(
                date.fromisoformat(resolved_date), FINAL_REVISION_TIME, NEW_YORK
            )
            availability = (
                "CONTEMPORANEOUS_FINAL_SAME_DAY"
                if local_retrieval.date().isoformat() == resolved_date
                else "BACKFILLED_FINAL"
            )
            expected_id = _observation_id(resolved_date)
            boundary = (
                record.get("schema_version") == RISK_FREE_INDEX_SCHEMA_VERSION
                and record.get("policy_version") == RISK_FREE_INDEX_POLICY_VERSION
                and record.get("observation_id") == expected_id
                and expected_id not in seen_ids
                and resolved_date not in seen_dates
                and record.get("status") == "OBSERVED_FINAL"
                and record.get("record_type") == "OFFICIAL_RISK_FREE_INDEX_EVIDENCE"
                and record.get("evidence_only") is True
                and record.get("provider") == PROVIDER
                and record.get("series") == SERIES
                and record.get("currency") == "USD"
                and exact_value == value
                and payload_date == resolved_date
                and payload_value == value
                and payload_revision == record.get("source_revision_indicator")
                and payload_hash == source_hash
                and payload_text == record.get("source_payload")
                and record.get("exact_index_value") == _fraction_material(value)
                and retrieved >= final_at
                and recorded >= retrieved
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("availability") == availability
                and record.get("revision_status")
                == "FINAL_AFTER_SAME_DAY_REVISION_WINDOW"
                and source_uri == record.get("source_uri")
                and record.get("methodology_uri") == METHODOLOGY_URI
                and record.get("source_observation_sha256") == _source_hash(record)
                and record.get("risk_free_period_return_calculated") is False
                and record.get("sharpe_calculated") is False
                and record.get("sortino_calculated") is False
                and record.get("performance_metric_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("live_trading_enabled") is False
                and record.get("provider_download_enabled") is False
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Risk-free-index record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            seen_dates.add(resolved_date)
            previous_hash = record["record_hash"]
        return records

    def _append(self, observation: dict[str, Any], *, allow_existing: bool):
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
                    if item["observation_id"] == observation["observation_id"]
                ),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {
                    key: value for key, value in observation.items() if key not in ignored
                }
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(
                    "Conflicting SOFR Index evidence requires an explicit future supersession policy."
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
