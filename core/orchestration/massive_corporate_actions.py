"""Strict, offline-only Massive corporate-action normalization for quarantine."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from types import MappingProxyType
from typing import Any, Mapping

from core.orchestration.historical_role_cutoff import (
    SCHEMA_VERSION,
    normalized_payload_sha256,
    validate_historical_role_cutoff_observations,
)
from core.orchestration.massive_historical_adapter import _canonical_timestamp, _strict_json, _symbol


ROLE = "POINT_IN_TIME_CORPORATE_EVENTS"
PROVIDER_ID = "MASSIVE"
MAX_ACTIONS = 5000
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024
SYNTHETIC_SYMBOLS = frozenset({"AAPL", "MSFT", "SPY"})
SYNTHETIC_START = date(2024, 9, 1)
SYNTHETIC_END = date(2025, 7, 31)
_ROOT_FIELDS = frozenset({"status", "results"})
_ROOT_OPTIONAL = frozenset({"request_id", "next_url", "count"})
_DIVIDEND_FIELDS = frozenset({"id", "ticker", "cash_amount", "currency", "declaration_date", "ex_dividend_date", "pay_date", "record_date", "reported_at"})
_SPLIT_FIELDS = frozenset({"id", "ticker", "execution_date", "split_from", "split_to", "adjustment_type", "reported_at"})


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or len(value) > 200:
        raise ValueError(f"{name} must be canonical nonempty text")
    return value


def _date(value: Any, name: str) -> str:
    text = _text(value, name)
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO date") from error


def _decimal(value: Any, name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{name} must be exact decimal text or integer")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"{name} must be an exact decimal") from error
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return format(number, "f")


def _root(payload: bytes) -> list[Mapping[str, Any]]:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("Massive corporate-action payload must be bounded nonempty bytes")
    root = _strict_json(payload)
    if not isinstance(root, Mapping) or not _ROOT_FIELDS.issubset(root) or set(root) - _ROOT_FIELDS - _ROOT_OPTIONAL:
        raise ValueError("Massive corporate-action response has missing or unsupported fields")
    if root.get("status") != "OK" or root.get("next_url") is not None:
        raise ValueError("Massive corporate-action response is unsuccessful or incomplete")
    results = root.get("results")
    if not isinstance(results, list) or len(results) > MAX_ACTIONS:
        raise ValueError("Massive corporate-action results must be a bounded list")
    if "count" in root and (type(root.get("count")) is not int or root.get("count") != len(results)):
        raise ValueError("Massive corporate-action count does not match results")
    return results


def normalize_corporate_actions(
    *, payload: bytes, kind: str, retrieved_at: str | datetime, decision_at: str | datetime
) -> tuple[Mapping[str, Any], ...]:
    """Normalize exact synthetic bytes into non-qualified point-in-time envelopes."""

    if kind not in {"DIVIDEND", "STOCK_SPLIT"}:
        raise ValueError("kind must be DIVIDEND or STOCK_SPLIT")
    retrieved = _canonical_timestamp(retrieved_at, "retrieved_at")
    cutoff = _canonical_timestamp(decision_at, "decision_at")
    source_hash = hashlib.sha256(payload).hexdigest()
    observations = []
    seen = set()
    fields = _DIVIDEND_FIELDS if kind == "DIVIDEND" else _SPLIT_FIELDS
    for raw in _root(payload):
        if not isinstance(raw, Mapping) or set(raw) != fields:
            raise ValueError("Massive corporate action has missing or unsupported fields")
        identifier = _text(raw.get("id"), "id")
        if identifier in seen:
            raise ValueError("Massive corporate-action response repeats id")
        seen.add(identifier)
        ticker = _symbol(raw.get("ticker"))
        if ticker not in SYNTHETIC_SYMBOLS:
            raise ValueError("ticker is outside the bounded synthetic campaign basket")
        reported = _canonical_timestamp(raw.get("reported_at"), "reported_at")
        if datetime.fromisoformat(reported) > datetime.fromisoformat(retrieved):
            raise ValueError("reported_at cannot follow retrieval")
        if kind == "DIVIDEND":
            normalized = {
                "kind": kind, "id": identifier, "ticker": ticker,
                "cash_amount": _decimal(raw.get("cash_amount"), "cash_amount"),
                "currency": _text(raw.get("currency"), "currency"),
                "declaration_date": _date(raw.get("declaration_date"), "declaration_date"),
                "ex_dividend_date": _date(raw.get("ex_dividend_date"), "ex_dividend_date"),
                "pay_date": _date(raw.get("pay_date"), "pay_date"),
                "record_date": _date(raw.get("record_date"), "record_date"),
                "reported_at": reported,
            }
            if normalized["currency"] != "USD":
                raise ValueError("synthetic campaign dividends must use USD")
            if normalized["declaration_date"] > normalized["ex_dividend_date"]:
                raise ValueError("declaration_date cannot follow ex_dividend_date")
            effective = normalized["ex_dividend_date"] + "T00:00:00+00:00"
        else:
            normalized = {
                "kind": kind, "id": identifier, "ticker": ticker,
                "execution_date": _date(raw.get("execution_date"), "execution_date"),
                "split_from": _decimal(raw.get("split_from"), "split_from"),
                "split_to": _decimal(raw.get("split_to"), "split_to"),
                "adjustment_type": _text(raw.get("adjustment_type"), "adjustment_type"),
                "reported_at": reported,
            }
            effective = normalized["execution_date"] + "T00:00:00+00:00"
        if not SYNTHETIC_START <= date.fromisoformat(effective[:10]) <= SYNTHETIC_END:
            raise ValueError("corporate action is outside the bounded synthetic window")
        normalized["point_in_time_basis"] = "SYNTHETIC_REPORTED_AT_UNQUALIFIED"
        observations.append({
            "schema_version": SCHEMA_VERSION, "role": ROLE, "provider_id": PROVIDER_ID,
            "provider_dataset_id": f"MASSIVE_{kind}S_V1", "provider_record_id": f"MASSIVE:{kind}:{identifier}",
            "effective_at": effective, "available_at": reported, "retrieved_at": retrieved,
            "observation_cutoff_at": cutoff, "source_payload_sha256": source_hash,
            "normalized_payload_sha256": normalized_payload_sha256(normalized), "payload": normalized,
        })
    observations.sort(key=lambda item: item["provider_record_id"])
    validated = validate_historical_role_cutoff_observations(role=ROLE, decision_at=cutoff, observations=observations)
    return tuple(MappingProxyType(dict(item)) for item in validated)
