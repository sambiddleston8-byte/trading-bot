from __future__ import annotations

"""Immutable, source-neutral congressional trade disclosure evidence."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.portfolio_valuation import _canonical_json, _record_hash, _write_all


DISCLOSURE_SCHEMA_VERSION = "1.0"
DISCLOSURE_POLICY_VERSION = "point-in-time-congressional-disclosure-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
ALLOWED_CHAMBERS = {"HOUSE", "SENATE"}
ALLOWED_SOURCES = {"OFFICIAL_HOUSE", "OFFICIAL_SENATE", "CAPITOL_TRADES", "LICENSED_PROVIDER"}
ALLOWED_ACCESS_BASES = {"OFFICIAL_USE_LEGAL_REVIEWED", "LICENSED_FEED", "MANUAL_RESEARCH_REVIEWED"}
ALLOWED_TRANSACTION_TYPES = {"PURCHASE", "SALE", "EXCHANGE"}
ALLOWED_OWNERS = {"SELF", "SPOUSE", "DEPENDENT_CHILD", "JOINT", "TRUST", "UNKNOWN"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HTTP_URL = re.compile(r"^https://[^\s]+$")
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")


def _required(value: Any, name: str, maximum: int = 500) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    if len(result) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return result


def _enum(value: Any, name: str, allowed: set[str]) -> str:
    result = _required(value, name, 80).upper()
    if result not in allowed:
        raise ValueError(f"{name} is not supported")
    return result


def _date(value: str | date) -> date:
    if isinstance(value, datetime):
        raise ValueError("date values must not contain a time")
    try:
        result = value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError("date must use YYYY-MM-DD") from error
    return result


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _hash(value: Any, name: str) -> str:
    result = str(value or "").strip().lower()
    if not _SHA256.fullmatch(result):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return result


def _money(value: Any, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not result.is_finite() or result < 0:
        raise ValueError(f"{name} must be a non-negative finite decimal")
    return result


def _canonical_decimal(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


DISCLOSURE_ID_FIELDS = (
    "chamber", "politician_name", "source", "source_record_id", "source_url",
    "source_access_basis", "source_terms_review_reference", "raw_document_sha256",
    "availability_evidence_sha256", "transaction_date", "notification_date",
    "filed_at", "source_first_publicly_available_at", "system_observed_at",
    "owner", "asset_name", "ticker", "transaction_type", "amount_min_usd",
    "amount_max_usd",
)


def _disclosure_id(values: Mapping[str, Any]) -> str:
    return "CTR-" + hashlib.sha256(
        _canonical_json([
            {field: values.get(field) for field in DISCLOSURE_ID_FIELDS},
            DISCLOSURE_POLICY_VERSION,
        ]).encode()
    ).hexdigest()[:32].upper()


class CongressionalTradeDisclosureLedger:
    """Stores evidence without downloading data or creating a trade instruction."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Congressional-disclosure ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank congressional-disclosure line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(f"Invalid JSON at congressional-disclosure line {line_number}.") from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(f"Congressional-disclosure line {line_number} is not an object.")
                records.append(record)
        return records

    def append(
        self, *, chamber: str, politician_name: str, source: str,
        source_record_id: str, source_url: str, source_access_basis: str,
        source_terms_review_reference: str, raw_document_sha256: str,
        availability_evidence_sha256: str, transaction_date: str | date,
        notification_date: str | date | None, filed_at: str | datetime,
        source_first_publicly_available_at: str | datetime,
        system_observed_at: str | datetime, owner: str,
        asset_name: str, ticker: str | None, transaction_type: str,
        amount_min_usd: Any, amount_max_usd: Any,
        recorded_by: str, recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        resolved_source = _enum(source, "source", ALLOWED_SOURCES)
        access_basis = _enum(source_access_basis, "source_access_basis", ALLOWED_ACCESS_BASES)
        if resolved_source == "CAPITOL_TRADES" and access_basis != "LICENSED_FEED":
            raise ValueError("Capitol Trades requires a licensed feed; public-site scraping is not permitted")
        source_link = _required(source_url, "source_url", 1000)
        if not _HTTP_URL.fullmatch(source_link):
            raise ValueError("source_url must be an HTTPS URL")
        transaction = _date(transaction_date)
        notification = _date(notification_date) if notification_date is not None else None
        filed = _timestamp(filed_at); available = _timestamp(source_first_publicly_available_at); observed = _timestamp(system_observed_at)
        recorded = _timestamp(recorded_at or datetime.now(timezone.utc))
        if filed.date() < transaction:
            raise ValueError("filed_at cannot predate the transaction")
        if notification is not None and notification < transaction:
            raise ValueError("notification_date cannot predate the transaction")
        if available < filed:
            raise ValueError("public availability cannot predate filing")
        if observed < available:
            raise ValueError("system observation cannot predate public availability")
        if recorded < observed:
            raise ValueError("recorded_at cannot predate system observation")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        minimum = _money(amount_min_usd, "amount_min_usd"); maximum = _money(amount_max_usd, "amount_max_usd")
        if maximum < minimum:
            raise ValueError("amount_max_usd cannot be below amount_min_usd")
        resolved_ticker = str(ticker or "").strip().upper() or None
        if resolved_ticker and not _TICKER.fullmatch(resolved_ticker):
            raise ValueError("ticker format is invalid")
        raw_hash = _hash(raw_document_sha256, "raw_document_sha256")
        source_id = _required(source_record_id, "source_record_id", 300)
        result = {
            "schema_version": DISCLOSURE_SCHEMA_VERSION,
            "policy_version": DISCLOSURE_POLICY_VERSION,
            "record_type": "POINT_IN_TIME_CONGRESSIONAL_TRADE_DISCLOSURE",
            "status": "RESEARCH_EVIDENCE_ONLY",
            "recorded_at": recorded.isoformat(),
            "recorded_by": _required(recorded_by, "recorded_by", 100),
            "chamber": _enum(chamber, "chamber", ALLOWED_CHAMBERS),
            "politician_name": _required(politician_name, "politician_name", 200),
            "source": resolved_source,
            "source_record_id": source_id,
            "source_url": source_link,
            "source_access_basis": access_basis,
            "source_terms_review_reference": _required(source_terms_review_reference, "source_terms_review_reference", 500),
            "raw_document_sha256": raw_hash,
            "availability_evidence_sha256": _hash(availability_evidence_sha256, "availability_evidence_sha256"),
            "transaction_date": transaction.isoformat(),
            "notification_date": notification.isoformat() if notification else None,
            "filed_at": filed.isoformat(),
            "source_first_publicly_available_at": available.isoformat(),
            "system_observed_at": observed.isoformat(),
            "historical_point_in_time_signal_at": available.isoformat(),
            "live_system_signal_at": observed.isoformat(),
            "transaction_to_public_delay_days": (available.date() - transaction).days,
            "transaction_to_system_delay_days": (observed.date() - transaction).days,
            "owner": _enum(owner, "owner", ALLOWED_OWNERS),
            "asset_name": _required(asset_name, "asset_name", 500),
            "ticker": resolved_ticker,
            "transaction_type": _enum(transaction_type, "transaction_type", ALLOWED_TRANSACTION_TYPES),
            "amount_min_usd": _canonical_decimal(minimum),
            "amount_max_usd": _canonical_decimal(maximum),
            "exact_trade_amount_known": minimum == maximum,
            "automatic_trade_signal": False,
            "copy_trade_allowed": False,
            "broker_connection_allowed": False,
            "order_submitted": False,
            "live_trading_enabled": False,
        }
        result["disclosure_id"] = _disclosure_id(result)
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH; seen_ids = set(); records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash or record.get("record_hash") != _record_hash(material): raise LedgerIntegrityError(f"Congressional-disclosure record {index} has been modified.")
            try:
                source = _enum(record.get("source"), "source", ALLOWED_SOURCES); access = _enum(record.get("source_access_basis"), "access", ALLOWED_ACCESS_BASES); source_id = _required(record.get("source_record_id"), "source id", 300); raw_hash = _hash(record.get("raw_document_sha256"), "raw")
                transaction = _date(record.get("transaction_date")); notification = _date(record.get("notification_date")) if record.get("notification_date") else None
                filed = _timestamp(record.get("filed_at")); available = _timestamp(record.get("source_first_publicly_available_at")); observed = _timestamp(record.get("system_observed_at")); recorded = _timestamp(record.get("recorded_at"))
                minimum = _money(record.get("amount_min_usd"), "minimum"); maximum = _money(record.get("amount_max_usd"), "maximum")
                _enum(record.get("chamber"), "chamber", ALLOWED_CHAMBERS); _enum(record.get("owner"), "owner", ALLOWED_OWNERS); _enum(record.get("transaction_type"), "transaction", ALLOWED_TRANSACTION_TYPES)
                _required(record.get("politician_name"), "politician", 200); _required(record.get("asset_name"), "asset", 500); _required(record.get("source_terms_review_reference"), "terms", 500); _hash(record.get("availability_evidence_sha256"), "availability")
            except (TypeError, ValueError) as error: raise LedgerIntegrityError(f"Congressional-disclosure record {index} has invalid values.") from error
            expected_id = _disclosure_id(record)
            boundary = (
                record.get("schema_version") == DISCLOSURE_SCHEMA_VERSION and record.get("policy_version") == DISCLOSURE_POLICY_VERSION and record.get("disclosure_id") == expected_id and expected_id not in seen_ids
                and record.get("record_type") == "POINT_IN_TIME_CONGRESSIONAL_TRADE_DISCLOSURE" and record.get("status") == "RESEARCH_EVIDENCE_ONLY"
                and not (source == "CAPITOL_TRADES" and access != "LICENSED_FEED") and record.get("source_url", "").startswith("https://")
                and filed.date() >= transaction and (notification is None or notification >= transaction) and available >= filed and observed >= available and recorded >= observed and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("historical_point_in_time_signal_at") == available.isoformat() and record.get("live_system_signal_at") == observed.isoformat()
                and record.get("transaction_to_public_delay_days") == (available.date() - transaction).days and record.get("transaction_to_system_delay_days") == (observed.date() - transaction).days
                and maximum >= minimum and record.get("amount_min_usd") == _canonical_decimal(minimum) and record.get("amount_max_usd") == _canonical_decimal(maximum) and record.get("exact_trade_amount_known") is (minimum == maximum)
                and all(record.get(field) is False for field in ("automatic_trade_signal","copy_trade_allowed","broker_connection_allowed","order_submitted","live_trading_enabled"))
            )
            if not boundary: raise LedgerIntegrityError(f"Congressional-disclosure record {index} violates its boundary.")
            seen_ids.add(expected_id); previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True); descriptor=os.open(self.path.with_suffix(self.path.suffix+".lock"),os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(descriptor,fcntl.LOCK_EX); records=self.verify(); existing=next((item for item in records if item["disclosure_id"]==result["disclosure_id"]),None)
            if existing:
                ignored={"previous_hash","record_hash","recorded_at"}
                if allow_existing and {k:v for k,v in existing.items() if k not in ignored}=={k:v for k,v in result.items() if k not in ignored}: return existing
                raise LedgerIntegrityError("Congressional disclosure already exists.")
            material={**result,"previous_hash":records[-1]["record_hash"] if records else GENESIS_HASH}; record={**material,"record_hash":_record_hash(material)}
            target=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            try: _write_all(target,(_canonical_json(record)+"\n").encode()); os.fsync(target)
            finally: os.close(target)
            return record
        finally: fcntl.flock(descriptor,fcntl.LOCK_UN); os.close(descriptor)
