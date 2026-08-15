from __future__ import annotations

"""Offline mock normalizer for cloud-delivered historical JSON and CSV.

This module is a conformance fixture, not a provider connector. It accepts only
in-memory synthetic bytes using one of two exact schemas, produces the canonical
historical observation envelope, and immediately applies the active engine's
point-in-time cutoff. It performs no I/O and imports no network, provider,
broker, execution, or deployment surface.

JSON schema::

    {"schema_version":"1.0","records":[
      {"provider_record_id":"...","effective_at":"...",
       "available_at":"...","payload":{...}}
    ]}

CSV schema::

    provider_record_id,effective_at,available_at,payload_json

``payload_json`` must encode one JSON object. These transport schemas do not
claim that any real provider uses them or that role-specific payload semantics
have been qualified.
"""

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
from typing import Any, Mapping, Sequence

from core.decision_ledger import canonical_timestamp
from core.orchestration.historical_role_cutoff import (
    HistoricalRoleCutoffBatch,
    ROLE_SCHEMAS,
    SCHEMA_VERSION as OBSERVATION_SCHEMA_VERSION,
    enforce_historical_role_cutoffs,
    normalized_payload_sha256,
)


MOCK_SCHEMA_VERSION = "1.0"
JSON_FORMAT = "JSON"
CSV_FORMAT = "CSV"
SUPPORTED_FORMATS = frozenset({JSON_FORMAT, CSV_FORMAT})
CSV_FIELDS = (
    "provider_record_id",
    "effective_at",
    "available_at",
    "payload_json",
)
JSON_ROOT_FIELDS = frozenset({"schema_version", "records"})
JSON_RECORD_FIELDS = frozenset(
    {"provider_record_id", "effective_at", "available_at", "payload"}
)
MAX_SOURCE_BYTES = 5 * 1024 * 1024
MAX_RECORDS = 10_000
MAX_TEXT = 1_000


def _text(value: Any, name: str, *, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    resolved = value.strip()
    if not resolved or resolved != value or len(resolved) > maximum:
        raise ValueError(f"{name} must be nonempty canonical text")
    if any(ord(character) < 32 for character in resolved):
        raise ValueError(f"{name} must not contain control characters")
    return resolved


def _timestamp(value: Any, name: str) -> str:
    try:
        resolved = datetime.fromisoformat(canonical_timestamp(value)).astimezone(
            timezone.utc
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error
    return resolved.isoformat(timespec="microseconds")


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is unsupported: {value}")


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON object repeats field: {key}")
        result[key] = value
    return result


def _json(value: str, name: str) -> Any:
    try:
        return json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError) as error:
        raise ValueError(f"{name} must be strict JSON with unique fields") from error


@dataclass(frozen=True, slots=True)
class MockHistoricalSource:
    """One deterministic synthetic flat-file or REST-response fixture."""

    role: str
    transport_format: str
    provider_id: str
    provider_dataset_id: str
    retrieved_at: str | datetime
    payload_bytes: bytes

    def __post_init__(self) -> None:
        role = _text(self.role, "role", maximum=100)
        if role not in ROLE_SCHEMAS:
            raise ValueError("role is outside the canonical historical role registry")
        transport = _text(
            self.transport_format, "transport_format", maximum=10
        ).upper()
        if transport not in SUPPORTED_FORMATS:
            raise ValueError("transport_format must be JSON or CSV")
        provider = _text(self.provider_id, "provider_id", maximum=300)
        dataset = _text(self.provider_dataset_id, "provider_dataset_id", maximum=300)
        if not provider.startswith("SYNTHETIC_") or not dataset.startswith("SYNTHETIC_"):
            raise ValueError("mock normalizer accepts synthetic provider identities only")
        retrieved = _timestamp(self.retrieved_at, "retrieved_at")
        payload = self.payload_bytes
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_SOURCE_BYTES:
            raise ValueError("payload_bytes must contain a bounded nonempty byte string")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "transport_format", transport)
        object.__setattr__(self, "provider_id", provider)
        object.__setattr__(self, "provider_dataset_id", dataset)
        object.__setattr__(self, "retrieved_at", retrieved)


def _decode_utf8(payload: bytes) -> str:
    try:
        value = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("mock historical source must be strict UTF-8") from error
    if "\x00" in value:
        raise ValueError("mock historical source must not contain NUL bytes")
    return value


def _json_records(payload: bytes) -> list[Mapping[str, Any]]:
    root = _json(_decode_utf8(payload), "JSON source")
    if not isinstance(root, Mapping) or set(root) != JSON_ROOT_FIELDS:
        raise ValueError("JSON source has missing or unsupported root fields")
    if root.get("schema_version") != MOCK_SCHEMA_VERSION:
        raise ValueError("JSON source schema_version is unsupported")
    records = root.get("records")
    if not isinstance(records, list):
        raise ValueError("JSON source records must be a list")
    if len(records) > MAX_RECORDS:
        raise ValueError("JSON source exceeds the record limit")
    if any(not isinstance(record, Mapping) for record in records):
        raise ValueError("every JSON source record must be an object")
    return records


def _csv_records(payload: bytes) -> list[Mapping[str, Any]]:
    try:
        rows = list(csv.reader(io.StringIO(_decode_utf8(payload), newline=""), strict=True))
    except csv.Error as error:
        raise ValueError("CSV source must use strict RFC-style quoting") from error
    if not rows or tuple(rows[0]) != CSV_FIELDS:
        raise ValueError("CSV source header must match the canonical field order")
    body = rows[1:]
    if len(body) > MAX_RECORDS:
        raise ValueError("CSV source exceeds the record limit")
    records: list[Mapping[str, Any]] = []
    for row in body:
        if len(row) != len(CSV_FIELDS):
            raise ValueError("CSV source row has the wrong field count")
        values = dict(zip(CSV_FIELDS, row))
        payload_value = _json(values["payload_json"], "CSV payload_json")
        records.append(
            {
                "provider_record_id": values["provider_record_id"],
                "effective_at": values["effective_at"],
                "available_at": values["available_at"],
                "payload": payload_value,
            }
        )
    return records


def _transport_records(source: MockHistoricalSource) -> list[Mapping[str, Any]]:
    if source.transport_format == JSON_FORMAT:
        return _json_records(source.payload_bytes)
    return _csv_records(source.payload_bytes)


def _normalize_source(
    source: MockHistoricalSource,
    *,
    observation_cutoff_at: str,
) -> list[dict[str, Any]]:
    source_hash = hashlib.sha256(source.payload_bytes).hexdigest()
    records: list[dict[str, Any]] = []
    identities: set[str] = set()
    retrieved = datetime.fromisoformat(str(source.retrieved_at))
    for value in _transport_records(source):
        if set(value) != JSON_RECORD_FIELDS:
            raise ValueError("source record has missing or unsupported fields")
        record_id = _text(
            value.get("provider_record_id"), "provider_record_id", maximum=MAX_TEXT
        )
        if record_id in identities:
            raise ValueError("source repeats a provider_record_id")
        identities.add(record_id)
        effective_at = _timestamp(value.get("effective_at"), "effective_at")
        available_at = _timestamp(value.get("available_at"), "available_at")
        if datetime.fromisoformat(available_at) > retrieved:
            raise ValueError("source retrieved_at cannot predate record available_at")
        payload = value.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("source record payload must be a JSON object")
        records.append(
            {
                "schema_version": OBSERVATION_SCHEMA_VERSION,
                "role": source.role,
                "provider_id": source.provider_id,
                "provider_dataset_id": source.provider_dataset_id,
                "provider_record_id": record_id,
                "effective_at": effective_at,
                "available_at": available_at,
                "retrieved_at": source.retrieved_at,
                "observation_cutoff_at": observation_cutoff_at,
                "source_payload_sha256": source_hash,
                "normalized_payload_sha256": normalized_payload_sha256(payload),
                "payload": dict(payload),
            }
        )
    return sorted(records, key=lambda item: item["provider_record_id"])


class MockHistoricalNormalizerAdapter:
    """Normalize synthetic fixtures and enforce one caller-supplied cutoff.

    This mock deliberately accepts no sealed invocation. Its successful result
    therefore remains non-engine-ready and does not attest a ledger-issued clock.
    """

    def normalize_for_engine(
        self,
        *,
        engine_key: str,
        decision_at: str | datetime,
        sources_by_role: Mapping[str, MockHistoricalSource],
    ) -> HistoricalRoleCutoffBatch:
        cutoff = _timestamp(decision_at, "decision_at")
        if not isinstance(sources_by_role, Mapping):
            raise ValueError("sources_by_role must be a role mapping")
        observations: dict[str, list[dict[str, Any]]] = {}
        for role, source in sources_by_role.items():
            if not isinstance(role, str) or not isinstance(source, MockHistoricalSource):
                raise ValueError("sources_by_role must contain mock source objects")
            if role != source.role:
                raise ValueError("source role does not match its mapping key")
            observations[role] = _normalize_source(
                source,
                observation_cutoff_at=cutoff,
            )
        return enforce_historical_role_cutoffs(
            engine_key=engine_key,
            decision_at=cutoff,
            observations_by_role=observations,
        )
