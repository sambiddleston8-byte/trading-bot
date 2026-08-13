from __future__ import annotations

"""Strict parsing of authenticated replay artifacts without interpreting a replay."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Mapping, Sequence

from core.data_quality.authenticated_source_content import AuthenticatedSourceContentLedger
from core.decision_ledger import canonical_timestamp
from core.orchestration.replay_dataset_admission import ReplayDatasetAdmissionLedger


SCHEMA_VERSION = "1.0"
SUPPORTED_ROLES = {
    "TOTAL_RETURN_PRICES",
    "MARKET_CALENDARS_AND_HALTS",
    "CORPORATE_ACTIONS",
    "DELISTING_OUTCOMES",
}
IDENTIFIER = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
DECIMAL_TEXT = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
MAX_DECIMAL_DIGITS = 30
MAX_DECIMAL_PLACES = 12
TOP_KEYS = {"schema_version", "artifact_role", "coverage", "rows"}
COVERAGE_KEYS = {
    "tickers", "covers_from_at", "through_at", "provider_declared_completeness"
}
ROW_KEYS = {
    "TOTAL_RETURN_PRICES": {
        "ticker", "observed_at", "available_at", "bid", "ask", "last", "volume",
        "source_row_locator",
    },
    "MARKET_CALENDARS_AND_HALTS": {
        "ticker", "starts_at", "ends_at", "status", "source_row_locator",
    },
    "CORPORATE_ACTIONS": {
        "ticker", "event_id", "event_type", "effective_at", "available_at",
        "source_row_locator",
    },
    "DELISTING_OUTCOMES": {
        "ticker", "event_id", "status", "effective_at", "available_at",
        "source_row_locator",
    },
}


def _required(value: Any, name: str, maximum: int = 1000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    resolved = value.strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _ticker(value: Any) -> str:
    resolved = _required(value, "ticker", 32).upper()
    if not IDENTIFIER.fullmatch(resolved):
        raise ValueError("ticker must use the normalized replay identifier format")
    return resolved


def _timestamp(value: Any, name: str) -> datetime:
    try:
        resolved = datetime.fromisoformat(canonical_timestamp(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error
    return resolved.astimezone(timezone.utc)


def _timestamp_text(value: Any, name: str) -> str:
    return _timestamp(value, name).isoformat()


def _decimal(value: Any, name: str, *, positive: bool) -> str:
    if not isinstance(value, str) or not DECIMAL_TEXT.fullmatch(value):
        raise ValueError(f"{name} must be a plain exact decimal string")
    try:
        resolved = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    digits = len(resolved.as_tuple().digits)
    places = max(0, -resolved.as_tuple().exponent)
    if (
        not resolved.is_finite()
        or digits > MAX_DECIMAL_DIGITS
        or places > MAX_DECIMAL_PLACES
        or resolved < 0
        or (positive and resolved <= 0)
    ):
        raise ValueError(f"{name} is outside the supported finite decimal boundary")
    return "0" if resolved == 0 else format(resolved.normalize(), "f")


def _sequence(value: Any, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a sequence")
    return value


def _exact_keys(value: Any, expected: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{name} has missing or unsupported fields")
    return value


def _coverage(value: Any) -> tuple[dict[str, Any], datetime, datetime]:
    source = _exact_keys(value, COVERAGE_KEYS, "coverage")
    tickers = [_ticker(item) for item in _sequence(source["tickers"], "coverage.tickers")]
    if not tickers or tickers != sorted(tickers) or len(set(tickers)) != len(tickers):
        raise ValueError("coverage.tickers must be nonempty, unique, and sorted")
    start = _timestamp(source["covers_from_at"], "covers_from_at")
    end = _timestamp(source["through_at"], "through_at")
    if start >= end:
        raise ValueError("coverage must have a positive half-open interval")
    return ({
        "tickers": tickers,
        "covers_from_at": start.isoformat(),
        "through_at": end.isoformat(),
        "provider_declared_completeness": _required(
            source["provider_declared_completeness"], "provider_declared_completeness", 100
        ),
    }, start, end)


def _in_coverage(moment: datetime, start: datetime, end: datetime, name: str) -> None:
    if not start <= moment < end:
        raise ValueError(f"{name} is outside declared half-open coverage")


def _price_row(value: Any, tickers: set[str], start: datetime, end: datetime) -> dict[str, Any]:
    row = _exact_keys(value, ROW_KEYS["TOTAL_RETURN_PRICES"], "price row")
    ticker = _ticker(row["ticker"])
    observed = _timestamp(row["observed_at"], "observed_at")
    available = _timestamp(row["available_at"], "available_at")
    if ticker not in tickers or observed > available:
        raise ValueError("price row ticker or availability chronology is invalid")
    _in_coverage(observed, start, end, "observed_at")
    return {
        "ticker": ticker, "observed_at": observed.isoformat(),
        "available_at": available.isoformat(),
        "bid": _decimal(row["bid"], "bid", positive=True),
        "ask": _decimal(row["ask"], "ask", positive=True),
        "last": _decimal(row["last"], "last", positive=True),
        "volume": _decimal(row["volume"], "volume", positive=False),
        "source_row_locator": _required(row["source_row_locator"], "source_row_locator"),
    }


def _calendar_row(value: Any, tickers: set[str], start: datetime, end: datetime) -> dict[str, Any]:
    row = _exact_keys(value, ROW_KEYS["MARKET_CALENDARS_AND_HALTS"], "calendar row")
    ticker = _ticker(row["ticker"])
    begins = _timestamp(row["starts_at"], "starts_at")
    ends = _timestamp(row["ends_at"], "ends_at")
    status = _required(row["status"], "status").upper()
    if ticker not in tickers or status not in {"OPEN", "CLOSED", "HALTED"}:
        raise ValueError("calendar ticker or status is invalid")
    if not start <= begins < ends <= end:
        raise ValueError("calendar row must be a positive interval within coverage")
    return {
        "ticker": ticker, "starts_at": begins.isoformat(), "ends_at": ends.isoformat(),
        "status": status,
        "source_row_locator": _required(row["source_row_locator"], "source_row_locator"),
    }


def _event_row(
    value: Any, role: str, tickers: set[str], start: datetime, end: datetime
) -> dict[str, Any]:
    row = _exact_keys(value, ROW_KEYS[role], "event row")
    ticker = _ticker(row["ticker"])
    effective = _timestamp(row["effective_at"], "effective_at")
    available = _timestamp(row["available_at"], "available_at")
    if ticker not in tickers or effective > available:
        raise ValueError("event ticker or availability chronology is invalid")
    _in_coverage(effective, start, end, "effective_at")
    result = {
        "ticker": ticker,
        "event_id": _required(row["event_id"], "event_id", 200),
        "effective_at": effective.isoformat(), "available_at": available.isoformat(),
        "source_row_locator": _required(row["source_row_locator"], "source_row_locator"),
    }
    if role == "CORPORATE_ACTIONS":
        event_type = _required(row["event_type"], "event_type").upper()
        if event_type not in {"SPLIT", "SYMBOL_CHANGE", "OTHER"}:
            raise ValueError("corporate-action event_type is unsupported")
        result["event_type"] = event_type
    else:
        status = _required(row["status"], "status").upper()
        if status not in {"DELISTED", "ACQUIRED", "BANKRUPT", "OTHER_TERMINAL"}:
            raise ValueError("delisting outcome status is unsupported")
        result["status"] = status
    return result


def _row_key(role: str, row: Mapping[str, Any]) -> tuple[Any, ...]:
    if role == "TOTAL_RETURN_PRICES":
        return (row["ticker"], row["observed_at"], row["available_at"], row["source_row_locator"])
    if role == "MARKET_CALENDARS_AND_HALTS":
        return (
            row["ticker"], row["starts_at"], row["ends_at"], row["status"],
            row["source_row_locator"],
        )
    return (
        row["ticker"], row["effective_at"], row["available_at"], row["event_id"],
        row["source_row_locator"],
    )


def _validate_rows(
    role: str, values: Any, tickers: set[str], start: datetime, end: datetime
) -> tuple[list[dict[str, Any]], bool]:
    rows = []
    for value in _sequence(values, "rows"):
        if role == "TOTAL_RETURN_PRICES":
            rows.append(_price_row(value, tickers, start, end))
        elif role == "MARKET_CALENDARS_AND_HALTS":
            rows.append(_calendar_row(value, tickers, start, end))
        else:
            rows.append(_event_row(value, role, tickers, start, end))
    keys = [_row_key(role, row) for row in rows]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise ValueError("artifact rows must be uniquely and deterministically sorted")
    if role == "CORPORATE_ACTIONS":
        identities = [(row["ticker"], row["event_id"]) for row in rows]
        if len(set(identities)) != len(identities):
            raise ValueError("corporate-action identities must be unique")
    if role == "DELISTING_OUTCOMES":
        names = [row["ticker"] for row in rows]
        if len(set(names)) != len(names):
            raise ValueError("at most one terminal outcome is allowed per ticker")
    calendar_proven = False
    if role == "MARKET_CALENDARS_AND_HALTS":
        for ticker in sorted(tickers):
            intervals = [row for row in rows if row["ticker"] == ticker]
            if not intervals:
                raise ValueError("every declared ticker requires complete calendar intervals")
            cursor = start
            for interval in intervals:
                begins = _timestamp(interval["starts_at"], "starts_at")
                ends = _timestamp(interval["ends_at"], "ends_at")
                if begins != cursor or begins >= ends:
                    raise ValueError("calendar intervals must be contiguous and non-overlapping")
                cursor = ends
            if cursor != end:
                raise ValueError("calendar intervals must exactly reach coverage through_at")
        calendar_proven = True
    return rows, calendar_proven


def load_authenticated_replay_artifact(
    *,
    admission_ledger: ReplayDatasetAdmissionLedger,
    content_ledger: AuthenticatedSourceContentLedger,
    admission_id: str,
    role: str,
) -> dict[str, Any]:
    """Return normalized authenticated structure; never select or execute observations."""
    resolved_role = _required(role, "role").upper()
    if resolved_role not in SUPPORTED_ROLES:
        raise ValueError("role is outside this execution-artifact parser boundary")
    admission = next(
        (
            item for item in admission_ledger.verify()
            if item.get("admission_id") == _required(admission_id, "admission_id")
        ),
        None,
    )
    if admission is None:
        raise ValueError("a verified replay dataset admission is required")
    reference = next(
        (item for item in admission["artifacts"] if item["role"] == resolved_role),
        None,
    )
    if reference is None:
        raise ValueError("verified admission does not contain the requested artifact role")
    content, payload = content_ledger.read_verified(reference["content_evidence_id"])
    if content["content_evidence_id"] != reference["content_evidence_id"]:
        raise ValueError("authenticated artifact content does not match its admission")
    try:
        source = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("authenticated replay artifact must be valid JSON") from error
    root = _exact_keys(source, TOP_KEYS, "artifact")
    if root["schema_version"] != SCHEMA_VERSION or root["artifact_role"] != resolved_role:
        raise ValueError("artifact type or schema version is unsupported")
    coverage, start, end = _coverage(root["coverage"])
    rows, calendar_proven = _validate_rows(
        resolved_role, root["rows"], set(coverage["tickers"]), start, end
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_role": resolved_role,
        "admission_id": admission["admission_id"],
        "admission_record_hash": admission["record_hash"],
        "replay_plan_id": admission["replay_plan_id"],
        "replay_plan_record_hash": admission["replay_plan_record_hash"],
        "dataset_commitment_sha256": admission["dataset_commitment_sha256"],
        "content_evidence_id": content["content_evidence_id"],
        "content_record_hash": content["record_hash"],
        "source_input_sha256": content["source_input_sha256"],
        "coverage": coverage,
        "rows": rows,
        "source_bytes_authenticated": True,
        "artifact_structure_validated": True,
        "calendar_interval_coverage_proven": calendar_proven,
        "coverage_completeness_proven": False,
        "plan_coverage_adequacy_proven": False,
        "financial_semantics_independently_proven": False,
        "observation_selected": False,
        "replay_executed": False,
        "costs_calculated": False,
        "fills_generated": False,
        "performance_calculated": False,
        "model_trained": False,
        "learning_applied": False,
        "network_allowed": False,
        "broker_connection_allowed": False,
        "orders_submitted": False,
        "live_trading_enabled": False,
    }
