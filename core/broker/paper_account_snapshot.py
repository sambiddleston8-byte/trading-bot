from __future__ import annotations

"""Immutable paper-broker cash evidence; no order or return is produced."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from core.broker.paper_broker_capture import (
    MAX_LEDGER_BYTES,
    _check_regular_descriptor,
    _fsync_directory,
    _no_follow,
    _private_directory,
    _read_private_file,
)
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
SCHEMA_VERSION = "1.0"
POLICY_VERSION = "paper-broker-cash-snapshot-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
RECORD_FIELDS = frozenset(
    {
        "schema_version", "policy_version", "snapshot_id", "record_type", "status",
        "broker", "broker_environment", "account_reference_sha256", "observed_at",
        "recorded_at", "currency", "cash", "settled_cash", "unsettled_cash",
        "buying_power", "equity", "exact_fractions", "source_payload_sha256",
        "source_payload_stored", "paper_account_confirmed",
        "long_only_cash_account_semantics", "gross_pre_tax_performance_relabelled",
        "tax_liability_estimated", "broker_reconciliation_complete", "order_submitted",
        "performance_metric_calculated", "recommendation_provided", "learning_eligible",
        "track_record_claim", "live_trading_enabled", "previous_hash", "record_hash",
    }
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Unable to complete paper-account snapshot append")
        offset += count


def _fraction(material: dict[str, Any], name: str) -> Fraction:
    try:
        denominator = int(material["denominator"])
        value = Fraction(int(material["numerator"]), denominator)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{name} exact fraction is invalid") from error
    if denominator <= 0:
        raise ValueError(f"{name} exact fraction denominator must be positive")
    return value


def _fraction_material(value: Fraction) -> dict[str, str]:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _decimal_string(value: Fraction) -> str:
    decimal = Decimal(value.numerator) / Decimal(value.denominator)
    return "0" if decimal == 0 else format(decimal.normalize(), "f")


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _amount(value: Any, name: str) -> Fraction:
    if isinstance(value, (bool, float)) or not isinstance(value, (str, Decimal)):
        raise ValueError(f"{name} must be an exact decimal string or Decimal")
    if isinstance(value, str) and (not value or value != value.strip()):
        raise ValueError(f"{name} must be an exact decimal string or Decimal")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite non-negative decimal") from error
    if not decimal.is_finite() or decimal < 0:
        raise ValueError(f"{name} must be a finite non-negative decimal")
    return Fraction(decimal)


def _required(value: Any, name: str, maximum: int = 200) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise ValueError(f"{name} is required and must not exceed {maximum} characters")
    return text


def _snapshot_id(account_hash: str, observed_at: str) -> str:
    return "PBAS-" + hashlib.sha256(
        _canonical_json([account_hash, observed_at, POLICY_VERSION]).encode("utf-8")
    ).hexdigest()[:32].upper()


class PaperBrokerAccountSnapshotLedger:
    """Append-only normalized snapshots from an explicitly paper account."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = _read_private_file(
                self.path,
                mode=0o600,
                name="Paper-account snapshot ledger",
                maximum_bytes=MAX_LEDGER_BYTES,
            )
        except OSError as error:
            raise LedgerIntegrityError("Paper-account snapshot ledger is unsafe.") from error
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Paper-account snapshot has an incomplete final line.")
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise LedgerIntegrityError("Paper-account snapshot ledger is not UTF-8.") from error
        records = []
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                raise LedgerIntegrityError(f"Blank paper-account snapshot at {line_number}.")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise LedgerIntegrityError(
                    f"Invalid paper-account snapshot JSON at {line_number}."
                ) from error
            if not isinstance(record, dict):
                raise LedgerIntegrityError(
                    f"Paper-account snapshot {line_number} is not an object."
                )
            records.append(record)
        return records

    def record(
        self,
        *,
        broker: str,
        account_reference_sha256: str,
        observed_at: str | datetime,
        cash: Any,
        settled_cash: Any,
        unsettled_cash: Any,
        buying_power: Any,
        equity: Any,
        source_payload_sha256: str,
        paper_account_confirmed: bool,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        if paper_account_confirmed is not True:
            raise ValueError("An explicitly confirmed paper account is required")
        broker_name = _required(broker, "broker")
        account_hash = _required(account_reference_sha256, "account_reference_sha256", 64)
        payload_hash = _required(source_payload_sha256, "source_payload_sha256", 64)
        if any(
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            for value in (account_hash, payload_hash)
        ):
            raise ValueError("account and payload references must be lowercase SHA-256 hashes")
        observed = _timestamp(observed_at)
        recorded = _timestamp(recorded_at or datetime.now(timezone.utc))
        now = datetime.now(timezone.utc)
        if observed > recorded or recorded > now + MAX_CLOCK_SKEW:
            raise ValueError("snapshot times cannot be future-dated or reverse ordered")
        values = {
            "cash": _amount(cash, "cash"),
            "settled_cash": _amount(settled_cash, "settled_cash"),
            "unsettled_cash": _amount(unsettled_cash, "unsettled_cash"),
            "buying_power": _amount(buying_power, "buying_power"),
            "equity": _amount(equity, "equity"),
        }
        if values["cash"] != values["settled_cash"] + values["unsettled_cash"]:
            raise ValueError("cash must equal settled_cash plus unsettled_cash")
        if values["equity"] < values["cash"]:
            raise ValueError("long-only paper account equity cannot be below cash")
        identity = {
            "broker": broker_name,
            "broker_environment": "PAPER",
            "account_reference_sha256": account_hash,
            "observed_at": observed.isoformat(),
        }
        result = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "snapshot_id": _snapshot_id(account_hash, observed.isoformat()),
            "record_type": "PAPER_BROKER_ACCOUNT_CASH_SNAPSHOT",
            "status": "OBSERVED",
            **identity,
            "recorded_at": recorded.isoformat(),
            "currency": "USD",
            **{name: _decimal_string(value) for name, value in values.items()},
            "exact_fractions": {
                name: _fraction_material(value) for name, value in values.items()
            },
            "source_payload_sha256": payload_hash,
            "source_payload_stored": False,
            "paper_account_confirmed": True,
            "long_only_cash_account_semantics": True,
            "gross_pre_tax_performance_relabelled": False,
            "tax_liability_estimated": False,
            "broker_reconciliation_complete": False,
            "order_submitted": False,
            "performance_metric_calculated": False,
            "recommendation_provided": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "live_trading_enabled": False,
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen = set()
        previous_time_by_account: dict[str, datetime] = {}
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Paper-account snapshot {index} has been modified.")
            try:
                if set(record) != RECORD_FIELDS:
                    raise ValueError("record field alphabet is invalid")
                account_hash = _required(
                    record.get("account_reference_sha256"), "account_reference_sha256", 64
                )
                payload_hash = _required(
                    record.get("source_payload_sha256"), "source_payload_sha256", 64
                )
                observed = _timestamp(record.get("observed_at"))
                recorded = _timestamp(record.get("recorded_at"))
                values = {
                    name: _fraction(record["exact_fractions"][name], name)
                    for name in ("cash", "settled_cash", "unsettled_cash", "buying_power", "equity")
                }
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Paper-account snapshot {index} is invalid.") from error
            expected_id = _snapshot_id(account_hash, observed.isoformat())
            prior = previous_time_by_account.get(account_hash)
            boundary = (
                record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("snapshot_id") == expected_id
                and expected_id not in seen
                and record.get("record_type") == "PAPER_BROKER_ACCOUNT_CASH_SNAPSHOT"
                and record.get("status") == "OBSERVED"
                and record.get("broker_environment") == "PAPER"
                and record.get("currency") == "USD"
                and len(account_hash) == len(payload_hash) == 64
                and all(character in "0123456789abcdef" for value in (account_hash, payload_hash) for character in value)
                and observed <= recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and (prior is None or observed > prior)
                and values["cash"] == values["settled_cash"] + values["unsettled_cash"]
                and values["equity"] >= values["cash"]
                and all(record.get(name) == _decimal_string(value) for name, value in values.items())
                and record.get("source_payload_stored") is False
                and record.get("paper_account_confirmed") is True
                and record.get("long_only_cash_account_semantics") is True
                and record.get("gross_pre_tax_performance_relabelled") is False
                and record.get("tax_liability_estimated") is False
                and record.get("broker_reconciliation_complete") is False
                and record.get("order_submitted") is False
                and record.get("performance_metric_calculated") is False
                and record.get("recommendation_provided") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("live_trading_enabled") is False
            )
            if not boundary:
                raise LedgerIntegrityError(f"Paper-account snapshot {index} violates its boundary.")
            seen.add(expected_id)
            previous_time_by_account[account_hash] = observed
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool):
        _private_directory(self.path.parent)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        try:
            descriptor = os.open(
                lock_path, os.O_CREAT | os.O_RDWR | _no_follow(), 0o600
            )
        except OSError as error:
            raise LedgerIntegrityError("Paper-account snapshot lock is unsafe.") from error
        try:
            _check_regular_descriptor(
                descriptor, mode=0o600, name="Paper-account snapshot lock"
            )
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next((item for item in records if item["snapshot_id"] == result["snapshot_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                if allow_existing and {k: v for k, v in existing.items() if k not in ignored} == {k: v for k, v in result.items() if k not in ignored}:
                    return existing
                raise LedgerIntegrityError(f"Paper snapshot {result['snapshot_id']} already exists.")
            prior = next(
                (
                    item
                    for item in reversed(records)
                    if item["account_reference_sha256"]
                    == result["account_reference_sha256"]
                ),
                None,
            )
            if prior is not None and _timestamp(result["observed_at"]) <= _timestamp(
                prior["observed_at"]
            ):
                raise ValueError("observed_at must move forward for each paper account")
            material = {**result, "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH}
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | _no_follow(),
                0o600,
            )
            try:
                _check_regular_descriptor(
                    target, mode=0o600, name="Paper-account snapshot ledger"
                )
                _write_all(target, (_canonical_json(record) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            _fsync_directory(self.path.parent)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
