from __future__ import annotations

"""Immutable economic outcomes for delisted historical-universe members."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.portfolio.historical_universe import HistoricalUniverseEventLedger


DELISTING_OUTCOME_SCHEMA_VERSION = "1.0"
DELISTING_OUTCOME_POLICY_VERSION = "evidence-backed-terminal-outcome-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
OUTCOME_FOR_TREATMENT = {
    "ACQUISITION_CASH_OR_STOCK_CONSIDERATION_REQUIRED": "ACQUISITION_CONSIDERATION",
    "BANKRUPTCY_OR_LIQUIDATION_OUTCOME_REQUIRED": "BANKRUPTCY_OR_LIQUIDATION",
    "LAST_TRADABLE_TOTAL_RETURN_REQUIRED": "LAST_TRADABLE_TOTAL_RETURN",
}
VALUATION_METHODS = {
    "CASH_PROCEEDS_PER_SHARE",
    "STOCK_OR_MIXED_PROCEEDS_PER_SHARE_AT_COMPLETION",
    "FINAL_DISTRIBUTION_PER_SHARE",
    "ZERO_RECOVERY_FINAL_OUTCOME",
    "LAST_TRADABLE_UNADJUSTED_CLOSE",
}
METHODS_FOR_OUTCOME = {
    "ACQUISITION_CONSIDERATION": {
        "CASH_PROCEEDS_PER_SHARE",
        "STOCK_OR_MIXED_PROCEEDS_PER_SHARE_AT_COMPLETION",
    },
    "BANKRUPTCY_OR_LIQUIDATION": {
        "FINAL_DISTRIBUTION_PER_SHARE",
        "ZERO_RECOVERY_FINAL_OUTCOME",
    },
    "LAST_TRADABLE_TOTAL_RETURN": {"LAST_TRADABLE_UNADJUSTED_CLOSE"},
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete delisting-outcome append")
        written += count


def _required(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").lower()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _https_uri(value: Any, name: str) -> str:
    resolved = _required(value, name)
    parsed = urlsplit(resolved)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{name} must be an HTTPS URL with a hostname")
    if parsed.username or parsed.password:
        raise ValueError(f"{name} cannot contain credentials")
    return resolved


def _nonnegative_decimal(value: Any, name: str) -> str:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a non-negative finite decimal") from error
    if not resolved.is_finite() or resolved < 0:
        raise ValueError(f"{name} must be a non-negative finite decimal")
    return format(resolved.normalize(), "f")


def _outcome_id(event_id: str, event_hash: str, source_hash: str, locator: str) -> str:
    material = [
        event_id,
        event_hash,
        source_hash,
        locator,
        DELISTING_OUTCOME_POLICY_VERSION,
    ]
    return "DOUT-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class DelistingOutcomeLedger:
    """Append-only terminal values linked to an immutable delisting event."""

    def __init__(
        self,
        path: str | Path,
        historical_universe_ledger: HistoricalUniverseEventLedger,
    ) -> None:
        self.path = Path(path)
        self.historical_universe_ledger = historical_universe_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Delisting-outcome ledger has an incomplete final line.")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank delisting-outcome line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at delisting-outcome line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Delisting-outcome line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def record_outcome(
        self,
        *,
        delisting_event_id: str,
        delisting_event_record_hash: str,
        outcome_type: str,
        terminal_value_per_share: Any,
        currency: str,
        valuation_method: str,
        outcome_effective_at: str | datetime,
        publicly_available_at: str | datetime,
        retrieved_at: str | datetime,
        source_uri: str,
        source_input_sha256: str,
        source_locator: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        event = self._linked_event(delisting_event_id, delisting_event_record_hash)
        resolved_type = _required(outcome_type, "outcome_type").upper()
        expected_type = OUTCOME_FOR_TREATMENT.get(event["terminal_outcome_treatment"])
        if expected_type is None:
            raise ValueError("delisting event has an unsupported terminal outcome treatment")
        if resolved_type != expected_type:
            raise ValueError("outcome_type does not match the delisting event treatment")
        method = _required(valuation_method, "valuation_method").upper()
        if method not in VALUATION_METHODS or method not in METHODS_FOR_OUTCOME[resolved_type]:
            raise ValueError("valuation_method does not match outcome_type")
        value = _nonnegative_decimal(terminal_value_per_share, "terminal_value_per_share")
        if method == "ZERO_RECOVERY_FINAL_OUTCOME" and Decimal(value) != 0:
            raise ValueError("ZERO_RECOVERY_FINAL_OUTCOME requires a zero terminal value")
        if method != "ZERO_RECOVERY_FINAL_OUTCOME" and Decimal(value) <= 0:
            raise ValueError("non-zero-recovery methods require a positive terminal value")
        resolved_currency = _required(currency, "currency").upper()
        if not CURRENCY_PATTERN.fullmatch(resolved_currency):
            raise ValueError("currency must be a three-letter code")
        effective = _timestamp(outcome_effective_at)
        public = _timestamp(publicly_available_at)
        retrieved = _timestamp(retrieved_at)
        recorded = _timestamp(recorded_at or datetime.now(timezone.utc))
        if public > retrieved or retrieved > recorded:
            raise ValueError("public availability, retrieval and recording must be chronological")
        if effective > retrieved:
            raise ValueError("terminal outcome must have occurred before its evidence was retrieved")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        if effective < _timestamp(event["effective_boundary_at"]):
            raise ValueError("outcome cannot predate the linked delisting")
        source_hash = _sha256(source_input_sha256, "source_input_sha256")
        locator = _required(source_locator, "source_locator")
        result = {
            "schema_version": DELISTING_OUTCOME_SCHEMA_VERSION,
            "policy_version": DELISTING_OUTCOME_POLICY_VERSION,
            "outcome_id": _outcome_id(
                event["event_id"], event["record_hash"], source_hash, locator
            ),
            "record_type": "EVIDENCE_BACKED_DELISTING_TERMINAL_OUTCOME",
            "status": "TERMINAL_OUTCOME_EVIDENCE_RECORDED",
            "recorded_at": recorded.isoformat(),
            "delisting_event_id": event["event_id"],
            "delisting_event_record_hash": event["record_hash"],
            "universe": event["universe"],
            "ticker": event["ticker"],
            "issuer_name": event["issuer_name"],
            "declared_terminal_outcome_treatment": event["terminal_outcome_treatment"],
            "outcome_type": resolved_type,
            "terminal_value_per_share": value,
            "currency": resolved_currency,
            "valuation_method": method,
            "outcome_effective_at": effective.isoformat(),
            "publicly_available_at": public.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "source_uri": _https_uri(source_uri, "source_uri"),
            "source_input_sha256": source_hash,
            "source_locator": locator,
            "actual_terminal_outcome_evidence": True,
            "coverage_completeness_proven": False,
            "survivorship_safe_replay_ready": False,
            "performance_calculated": False,
            "recommendation_provided": False,
            "broker_submission_enabled": False,
            "live_trading_enabled": False,
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        events = {item["event_id"]: item for item in self.historical_universe_ledger.verify()}
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        seen_events: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Delisting-outcome record {index} has been modified.")
            try:
                event = events[_required(record.get("delisting_event_id"), "event id")]
                event_hash = _sha256(record.get("delisting_event_record_hash"), "event hash")
                source_hash = _sha256(record.get("source_input_sha256"), "source hash")
                locator = _required(record.get("source_locator"), "source_locator")
                outcome_type = _required(record.get("outcome_type"), "outcome_type").upper()
                method = _required(record.get("valuation_method"), "valuation_method").upper()
                value = _nonnegative_decimal(
                    record.get("terminal_value_per_share"), "terminal value"
                )
                currency = _required(record.get("currency"), "currency").upper()
                effective = _timestamp(record.get("outcome_effective_at"))
                public = _timestamp(record.get("publicly_available_at"))
                retrieved = _timestamp(record.get("retrieved_at"))
                recorded = _timestamp(record.get("recorded_at"))
                _https_uri(record.get("source_uri"), "source_uri")
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Delisting-outcome record {index} is invalid."
                ) from error
            expected_type = OUTCOME_FOR_TREATMENT.get(event.get("terminal_outcome_treatment"))
            expected_id = _outcome_id(event["event_id"], event_hash, source_hash, locator)
            zero_method_valid = (
                (method == "ZERO_RECOVERY_FINAL_OUTCOME" and Decimal(value) == 0)
                or (method != "ZERO_RECOVERY_FINAL_OUTCOME" and Decimal(value) > 0)
            )
            fixed_false = (
                "coverage_completeness_proven",
                "survivorship_safe_replay_ready",
                "performance_calculated",
                "recommendation_provided",
                "broker_submission_enabled",
                "live_trading_enabled",
            )
            boundary = (
                record.get("schema_version") == DELISTING_OUTCOME_SCHEMA_VERSION
                and record.get("policy_version") == DELISTING_OUTCOME_POLICY_VERSION
                and record.get("outcome_id") == expected_id
                and expected_id not in seen_ids
                and event["event_id"] not in seen_events
                and event.get("event_type") == "DELISTED"
                and event_hash == event["record_hash"]
                and record.get("record_type") == "EVIDENCE_BACKED_DELISTING_TERMINAL_OUTCOME"
                and record.get("status") == "TERMINAL_OUTCOME_EVIDENCE_RECORDED"
                and record.get("universe") == event["universe"]
                and record.get("ticker") == event["ticker"]
                and record.get("issuer_name") == event["issuer_name"]
                and record.get("declared_terminal_outcome_treatment")
                == event["terminal_outcome_treatment"]
                and outcome_type == expected_type
                and method in METHODS_FOR_OUTCOME.get(outcome_type, set())
                and method in VALUATION_METHODS
                and zero_method_valid
                and CURRENCY_PATTERN.fullmatch(currency) is not None
                and _timestamp(event["effective_boundary_at"]) <= effective
                and public <= retrieved <= recorded
                and effective <= retrieved
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("actual_terminal_outcome_evidence") is True
                and all(record.get(field) is False for field in fixed_false)
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Delisting-outcome record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            seen_events.add(event["event_id"])
            previous_hash = record["record_hash"]
        return records

    def evidence_for_event(
        self, delisting_event_id: str, *, known_as_of: str | datetime
    ) -> dict[str, Any]:
        cutoff = _timestamp(known_as_of)
        match = next(
            (
                item
                for item in self.verify()
                if item["delisting_event_id"] == delisting_event_id
                and _timestamp(item["publicly_available_at"]) <= cutoff
            ),
            None,
        )
        return {
            "delisting_event_id": delisting_event_id,
            "known_as_of": cutoff.isoformat(),
            "terminal_outcome_evidence_complete": match is not None,
            "outcome": match,
            "coverage_completeness_proven": False,
            "survivorship_safe_replay_ready": False,
            "performance_calculated": False,
        }

    def _linked_event(self, event_id: str, event_hash: str) -> dict[str, Any]:
        expected_hash = _sha256(event_hash, "delisting_event_record_hash")
        match = next(
            (
                item
                for item in self.historical_universe_ledger.verify()
                if item["event_id"] == _required(event_id, "delisting_event_id")
            ),
            None,
        )
        if match is None or match["record_hash"] != expected_hash:
            raise ValueError("linked delisting event is missing or has changed")
        if match["event_type"] != "DELISTED":
            raise ValueError("linked universe event is not a delisting")
        return match

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["outcome_id"] == result["outcome_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in result.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError("Delisting outcome already exists.")
            if any(
                item["delisting_event_id"] == result["delisting_event_id"] for item in records
            ):
                raise LedgerIntegrityError("Delisting event already has a terminal outcome.")
            material = {
                **result,
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
