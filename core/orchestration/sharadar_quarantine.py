from __future__ import annotations

"""Bounded, non-authoritative Sharadar connectivity captures.

This module deliberately stops at an owner-local raw-data quarantine.  It can
prove that an entitled API response was received and preserve its exact bytes,
but it cannot qualify provider semantics, historical completeness, PIT use, or
replay admission.  The API key is sent only as the provider-required query
parameter and is never included in returned metadata, errors, or persisted
records.
"""

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from types import MappingProxyType
from typing import Any, Callable, Mapping
from urllib.parse import urlencode, urljoin, urlsplit
import zipfile

import requests

from core.data_sources.provider_access import (
    ProviderAccessCoordinator,
    ProviderAccessError,
    ProviderAccessPolicy,
    safe_access_dict,
)
from core.decision_ledger import GENESIS_HASH, canonical_timestamp


PROVIDER_ID = "SHARADAR"
BASE_URL = "https://api.sharadar.com"
API_PATH_PREFIX = "/v1.0/data/"
POLICY_VERSION = "sharadar-bounded-connectivity-quarantine-v1"
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 25
MAX_HEADERS = 100
MAX_HEADER_VALUE_LENGTH = 10_000
QUARANTINE_RELATIVE_PATH = Path("data/research/sharadar_quarantine")
TEN_YEAR_TABLES = ("tickers", "stocks", "actions", "sp500", "fundamentals")
MAX_COMPRESSED_BYTES = MappingProxyType(
    {
        "tickers": 512 * 1024 * 1024,
        "stocks": 8 * 1024 * 1024 * 1024,
        "actions": 2 * 1024 * 1024 * 1024,
        "sp500": 512 * 1024 * 1024,
        "fundamentals": 4 * 1024 * 1024 * 1024,
    }
)
MAX_UNCOMPRESSED_BYTES = MappingProxyType(
    {table: maximum * 20 for table, maximum in MAX_COMPRESSED_BYTES.items()}
)
BULK_REQUIRED_FIELDS = MappingProxyType(
    {
        "tickers": frozenset(
            {
                "table",
                "permaticker",
                "ticker",
                "name",
                "exchange",
                "isdelisted",
                "firstpricedate",
                "lastpricedate",
            }
        ),
        "stocks": frozenset(
            {
                "ticker",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "closeadj",
                "closeunadj",
                "lastupdated",
            }
        ),
        "actions": frozenset(
            {"date", "action", "ticker", "name", "value", "contraticker", "contraname"}
        ),
        "sp500": frozenset(
            {"date", "action", "ticker", "name", "contraticker", "contraname", "note"}
        ),
        "fundamentals": frozenset(
            {
                "ticker",
                "dimension",
                "calendardate",
                "date",
                "reportperiod",
                "lastupdated",
                "revenue",
                "netinc",
                "equity",
                "debt",
                "cashneq",
                "ncfo",
                "capex",
                "sharesbas",
                "price",
                "marketcap",
                "ev",
                "pe",
                "pb",
                "ps",
            }
        ),
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CAPTURE_AUTHORITY = object()

SAFETY_FALSE = (
    "provider_payload_semantics_qualified",
    "historical_coverage_qualified",
    "point_in_time_semantics_qualified",
    "security_master_admitted",
    "corporate_actions_admitted",
    "daily_bars_admitted",
    "fundamentals_admitted",
    "dataset_admitted",
    "performance_authorized",
    "validation_opened",
    "test_opened",
    "engine_input_ready",
    "broker_connection_allowed",
    "orders_submitted",
    "live_trading_enabled",
)


@dataclass(frozen=True, slots=True)
class SharadarProbeDefinition:
    table: str
    role: str
    query: Mapping[str, str]
    expected_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.table not in {"tickers", "stocks", "fundamentals"}:
            raise ValueError("Sharadar probe table is unsupported")
        if self.role not in {
            "SECURITY_MASTER_CONNECTIVITY",
            "DAILY_BARS_CONNECTIVITY",
            "PIT_FUNDAMENTALS_CONNECTIVITY",
        }:
            raise ValueError("Sharadar probe role is unsupported")
        if not isinstance(self.query, Mapping) or not self.query:
            raise ValueError("Sharadar probe query must be a nonempty mapping")
        resolved: dict[str, str] = {}
        for key, value in self.query.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or not key
                or not value
                or key == "api_key"
                or any(ord(character) < 32 for character in key + value)
            ):
                raise ValueError("Sharadar probe query is invalid")
            resolved[key] = value
        if resolved.get("format") != "csv":
            raise ValueError("Sharadar probes require CSV responses")
        try:
            limit = int(resolved.get("limit", ""))
        except ValueError as error:
            raise ValueError("Sharadar probe limit must be an integer") from error
        if not 1 <= limit <= MAX_ROWS:
            raise ValueError("Sharadar probe limit exceeds the bounded maximum")
        if not self.expected_fields or len(set(self.expected_fields)) != len(
            self.expected_fields
        ):
            raise ValueError("Sharadar expected fields must be unique and nonempty")
        if tuple(resolved.get("fields", "").split(",")) != self.expected_fields:
            raise ValueError("Sharadar fields must exactly match the expected schema")
        object.__setattr__(self, "query", MappingProxyType(resolved))

    @property
    def request_uri(self) -> str:
        return f"{BASE_URL}{API_PATH_PREFIX}{self.table}"

    @property
    def request_query_canonical(self) -> str:
        return urlencode(sorted(self.query.items()), safe=",")


PROBE_DEFINITIONS = (
    SharadarProbeDefinition(
        table="tickers",
        role="SECURITY_MASTER_CONNECTIVITY",
        query={
            "fields": (
                "table,permaticker,ticker,name,exchange,isdelisted,"
                "firstpricedate,lastpricedate"
            ),
            "format": "csv",
            "limit": "5",
            "table": "fundamentals",
            "ticker": "AAPL",
        },
        expected_fields=(
            "table",
            "permaticker",
            "ticker",
            "name",
            "exchange",
            "isdelisted",
            "firstpricedate",
            "lastpricedate",
        ),
    ),
    SharadarProbeDefinition(
        table="stocks",
        role="DAILY_BARS_CONNECTIVITY",
        query={
            "fields": "ticker,date,open,high,low,close,volume,closeadj,closeunadj",
            "format": "csv",
            "from": "2022-01-03",
            "limit": "5",
            "sort": "date.asc",
            "ticker": "AAPL",
            "to": "2022-01-07",
        },
        expected_fields=(
            "ticker",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "closeadj",
            "closeunadj",
        ),
    ),
    SharadarProbeDefinition(
        table="fundamentals",
        role="PIT_FUNDAMENTALS_CONNECTIVITY",
        query={
            "dimension": "ARQ",
            "fields": "ticker,dimension,calendardate,date,reportperiod,lastupdated",
            "format": "csv",
            "from": "2022-01-01",
            "limit": "5",
            "sort": "date.asc",
            "ticker": "AAPL",
            "to": "2022-12-31",
        },
        expected_fields=(
            "ticker",
            "dimension",
            "calendardate",
            "date",
            "reportperiod",
            "lastupdated",
        ),
    ),
)


class SharadarCaptureError(RuntimeError):
    """Stable secret-free failure at the Sharadar network boundary."""


@dataclass(frozen=True, slots=True)
class SharadarBulkStatus:
    table: str
    name: str
    size: int
    modified: str
    payload_sha256: str
    requested_at: str
    retrieved_at: str
    provider_access: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.table not in TEN_YEAR_TABLES:
            raise ValueError("Sharadar bulk-status table is unsupported")
        if (
            not isinstance(self.name, str)
            or not self.name.endswith(".csv.zip")
            or len(self.name) > 200
            or "/" in self.name
            or "\\" in self.name
            or any(ord(character) < 32 for character in self.name)
        ):
            raise ValueError("Sharadar bulk-status filename is unexpected")
        if (
            isinstance(self.size, bool)
            or not isinstance(self.size, int)
            or not 0 < self.size < 2**63
        ):
            raise ValueError("Sharadar bulk-status size is outside the safe boundary")
        if (
            not isinstance(self.modified, str)
            or not self.modified
            or len(self.modified) > 200
            or any(ord(character) < 32 for character in self.modified)
        ):
            raise ValueError("Sharadar bulk-status modified value is invalid")
        _canonical_timestamp(self.requested_at, "requested_at")
        _canonical_timestamp(self.retrieved_at, "retrieved_at")
        if _SHA256_PATTERN.fullmatch(self.payload_sha256) is None:
            raise ValueError("Sharadar bulk-status payload hash is invalid")
        object.__setattr__(self, "provider_access", MappingProxyType(dict(self.provider_access)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "name": self.name,
            "size": self.size,
            "modified": self.modified,
            "status_payload_sha256": self.payload_sha256,
            "status_requested_at": self.requested_at,
            "status_retrieved_at": self.retrieved_at,
            "status_provider_access": dict(self.provider_access),
            "status_size_is_advisory": True,
            "status_modified_is_opaque": True,
        }


@dataclass(frozen=True, slots=True)
class SharadarBulkCapture:
    table: str
    years: int
    requested_at: str
    retrieved_at: str
    payload_sha256: str
    byte_length: int
    archive_member: str
    archive_member_declared_bytes: int
    csv_header_sha256: str
    redirect_host: str
    redirect_path_sha256: str
    status: SharadarBulkStatus
    _authority: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._authority is not _CAPTURE_AUTHORITY:
            raise PermissionError("SharadarBulkCapture must be issued by the client")
        if self.table not in TEN_YEAR_TABLES or self.years != 10:
            raise ValueError("Sharadar bulk capture scope is invalid")
        if self.status.table != self.table:
            raise ValueError("Sharadar bulk capture is not bound to its status response")
        if not 0 < self.byte_length <= MAX_COMPRESSED_BYTES[self.table]:
            raise ValueError("Sharadar bulk capture size is invalid")
        if not 0 < self.archive_member_declared_bytes <= MAX_UNCOMPRESSED_BYTES[self.table]:
            raise ValueError("Sharadar bulk archive member size is invalid")
        if not self.archive_member.endswith(".csv") or "/" in self.archive_member or "\\" in self.archive_member:
            raise ValueError("Sharadar bulk archive member is invalid")
        if not self.redirect_host or len(self.redirect_host) > 253:
            raise ValueError("Sharadar bulk redirect host is invalid")
        for value in (self.payload_sha256, self.csv_header_sha256, self.redirect_path_sha256):
            if _SHA256_PATTERN.fullmatch(value) is None:
                raise ValueError("Sharadar bulk capture hash is invalid")

    def as_record(self) -> dict[str, Any]:
        return {
            "policy_version": POLICY_VERSION,
            "provider_id": PROVIDER_ID,
            "capture_type": "TEN_YEAR_BULK_COMPRESSED_CSV",
            "table": self.table,
            "years": self.years,
            "requested_at": self.requested_at,
            "retrieved_at": self.retrieved_at,
            "payload_sha256": self.payload_sha256,
            "byte_length": self.byte_length,
            "archive_member": self.archive_member,
            "archive_member_declared_bytes": self.archive_member_declared_bytes,
            "csv_header_sha256": self.csv_header_sha256,
            "redirect_host": self.redirect_host,
            "redirect_path_sha256": self.redirect_path_sha256,
            **self.status.as_dict(),
            "entitlement_basis": "OPERATOR_ASSERTED_SHARADAR_10_YEAR_BUNDLE_UNAUTHENTICATED",
            "license_restricted": True,
            "raw_response_bytes_retained": True,
            "quarantine_only": True,
            **{name: False for name in SAFETY_FALSE},
        }

    def __reduce__(self) -> None:
        raise TypeError("Sharadar bulk captures are deliberately not pickleable")


def _canonical_timestamp(value: Any, name: str) -> str:
    try:
        return (
            datetime.fromisoformat(canonical_timestamp(value))
            .astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _strict_json(payload: bytes) -> Mapping[str, Any]:
    if not isinstance(payload, bytes) or not payload or len(payload) > 64 * 1024:
        raise ValueError("Sharadar status payload must be bounded nonempty bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Sharadar status payload must be strict UTF-8") from error
    if "\x00" in text:
        raise ValueError("Sharadar status payload must not contain NUL bytes")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Sharadar status repeats field: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"Sharadar status contains non-finite value: {value}")

    try:
        result = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, ValueError, RecursionError) as error:
        raise ValueError("Sharadar status payload must be strict JSON") from error
    if not isinstance(result, Mapping):
        raise ValueError("Sharadar status payload must be an object")
    return result


def _validate_key(value: Any) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError("Sharadar API key must be nonempty canonical text")
    if len(value) > 500 or any(not 33 <= ord(character) <= 126 for character in value):
        raise ValueError("Sharadar API key has an invalid format")
    return value


def _canonical_headers(value: Any) -> tuple[Mapping[str, str], str, str]:
    if not isinstance(value, Mapping) or len(value) > MAX_HEADERS:
        raise SharadarCaptureError("Sharadar response headers are invalid")
    headers: dict[str, str] = {}
    for key, item in value.items():
        name = str(key).strip().lower()
        resolved = str(item).strip()
        if (
            not name
            or name in headers
            or len(name) > 200
            or len(resolved) > MAX_HEADER_VALUE_LENGTH
            or any(ord(character) < 32 and character != "\t" for character in name + resolved)
        ):
            raise SharadarCaptureError("Sharadar response headers are invalid")
        headers[name] = resolved
    media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in {"text/csv", "application/csv", "application/octet-stream"}:
        raise SharadarCaptureError("Sharadar response media type is invalid")
    digest = hashlib.sha256(_canonical_json(headers)).hexdigest()
    return MappingProxyType(headers), digest, media_type


def validate_probe_csv(
    payload: bytes, definition: SharadarProbeDefinition
) -> tuple[int, str]:
    """Validate shape only; do not infer completeness or provider semantics."""

    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_SOURCE_BYTES:
        raise ValueError("Sharadar probe payload must be bounded nonempty bytes")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Sharadar probe CSV must be strict UTF-8") from error
    if "\x00" in text:
        raise ValueError("Sharadar probe CSV must not contain NUL bytes")
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    fields = reader.fieldnames
    if fields is None or set(fields) != set(definition.expected_fields):
        raise ValueError("Sharadar probe CSV schema does not match the exact request")
    if len(fields) != len(set(fields)) or any(not field for field in fields):
        raise ValueError("Sharadar probe CSV columns must be unique and nonempty")
    rows = list(reader)
    limit = int(definition.query["limit"])
    if not rows or len(rows) > limit:
        raise ValueError("Sharadar probe CSV rows must be bounded and nonempty")
    for row in rows:
        if None in row or set(row) != set(fields):
            raise ValueError("Sharadar probe CSV row shape is invalid")
        if row.get("ticker") != "AAPL":
            raise ValueError("Sharadar probe CSV ticker does not match the request")
        if definition.table == "fundamentals" and row.get("dimension") != "ARQ":
            raise ValueError("Sharadar fundamentals probe is not as-reported quarterly")
        for name in ("date", "calendardate", "reportperiod", "firstpricedate", "lastpricedate"):
            value = row.get(name)
            if value:
                try:
                    canonical = date.fromisoformat(value).isoformat()
                except ValueError as error:
                    raise ValueError(f"Sharadar {name} must be an ISO date") from error
                if canonical != value:
                    raise ValueError(f"Sharadar {name} must be a canonical ISO date")
    header_sha256 = hashlib.sha256(
        ",".join(fields).encode("utf-8")
    ).hexdigest()
    return len(rows), header_sha256


@dataclass(frozen=True, slots=True)
class SharadarFetchedProbe:
    table: str
    role: str
    request_uri: str
    request_query_canonical: str
    requested_at: str
    retrieved_at: str
    response_status_code: int
    response_headers_sha256: str
    media_type: str
    payload_bytes: bytes = field(repr=False)
    payload_sha256: str
    byte_length: int
    row_count: int
    csv_header_sha256: str
    provider_access: Mapping[str, Any]
    _authority: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._authority is not _CAPTURE_AUTHORITY:
            raise PermissionError("SharadarFetchedProbe must be issued by the client")
        if self.response_status_code != 200:
            raise ValueError("Sharadar fetched probe status is invalid")
        if self.byte_length != len(self.payload_bytes) or not 0 < self.byte_length <= MAX_SOURCE_BYTES:
            raise ValueError("Sharadar fetched probe byte length is invalid")
        if hashlib.sha256(self.payload_bytes).hexdigest() != self.payload_sha256:
            raise ValueError("Sharadar fetched probe payload hash is invalid")
        for value in (self.payload_sha256, self.response_headers_sha256, self.csv_header_sha256):
            if _SHA256_PATTERN.fullmatch(value) is None:
                raise ValueError("Sharadar fetched probe hash is invalid")
        if not 1 <= self.row_count <= MAX_ROWS:
            raise ValueError("Sharadar fetched probe row count is invalid")
        if "api_key" in self.request_query_canonical.lower():
            raise ValueError("Sharadar persisted query must not contain credentials")
        parsed = urlsplit(self.request_uri)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.sharadar.com"
            or parsed.query
            or parsed.fragment
            or parsed.path != f"{API_PATH_PREFIX}{self.table}"
        ):
            raise ValueError("Sharadar fetched probe URI is invalid")
        object.__setattr__(self, "provider_access", MappingProxyType(dict(self.provider_access)))

    def as_record(self) -> dict[str, Any]:
        return {
            "policy_version": POLICY_VERSION,
            "provider_id": PROVIDER_ID,
            "table": self.table,
            "role": self.role,
            "request_uri": self.request_uri,
            "request_query_canonical": self.request_query_canonical,
            "requested_at": self.requested_at,
            "retrieved_at": self.retrieved_at,
            "response_status_code": self.response_status_code,
            "response_headers_sha256": self.response_headers_sha256,
            "media_type": self.media_type,
            "payload_sha256": self.payload_sha256,
            "byte_length": self.byte_length,
            "row_count": self.row_count,
            "csv_header_sha256": self.csv_header_sha256,
            "provider_access": dict(self.provider_access),
            "entitlement_basis": "OPERATOR_ASSERTED_SHARADAR_10_YEAR_BUNDLE_UNAUTHENTICATED",
            "license_restricted": True,
            "raw_response_bytes_retained": True,
            "quarantine_only": True,
            **{name: False for name in SAFETY_FALSE},
        }

    def __reduce__(self) -> None:
        raise TypeError("Sharadar fetched probes are deliberately not pickleable")


class SharadarSampleClient:
    ACCESS_POLICY = ProviderAccessPolicy(
        minimum_interval_seconds=1.0,
        maximum_attempts=1,
    )

    def __init__(
        self,
        api_key: str,
        *,
        session: requests.Session | None = None,
        access: ProviderAccessCoordinator | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._api_key = _validate_key(api_key)
        self._session = session or requests.Session()
        self._access = access or ProviderAccessCoordinator.for_provider(
            "SHARADAR_API_KEY",
            "Sharadar bounded connectivity",
            policy=self.ACCESS_POLICY,
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch(self, definition: SharadarProbeDefinition) -> SharadarFetchedProbe:
        if not any(definition is item for item in PROBE_DEFINITIONS):
            raise ValueError("only the frozen Sharadar connectivity probes are allowed")
        requested_at = _canonical_timestamp(self._clock(), "requested_at")
        params = {**definition.query, "api_key": self._api_key}
        try:
            result = self._access.get(
                self._session,
                definition.request_uri,
                params=params,
                headers={"Accept": "text/csv"},
                timeout=30,
                allow_redirects=False,
            )
        except ProviderAccessError:
            raise SharadarCaptureError("Sharadar connectivity request could not be completed") from None
        response = result.response
        status = getattr(response, "status_code", None)
        if status in {301, 302, 303, 307, 308}:
            raise SharadarCaptureError("Sharadar connectivity redirect was rejected")
        if status != 200:
            raise SharadarCaptureError("Sharadar connectivity request was rejected")
        _, headers_sha256, media_type = _canonical_headers(
            getattr(response, "headers", None)
        )
        payload = getattr(response, "content", None)
        row_count, csv_header_sha256 = validate_probe_csv(payload, definition)
        retrieved_at = _canonical_timestamp(self._clock(), "retrieved_at")
        if retrieved_at < requested_at:
            raise SharadarCaptureError("Sharadar connectivity clock moved backwards")
        provider_access = safe_access_dict(result.metadata)
        if provider_access is None:
            raise SharadarCaptureError("Sharadar connectivity access metadata is invalid")
        return SharadarFetchedProbe(
            table=definition.table,
            role=definition.role,
            request_uri=definition.request_uri,
            request_query_canonical=definition.request_query_canonical,
            requested_at=requested_at,
            retrieved_at=retrieved_at,
            response_status_code=200,
            response_headers_sha256=headers_sha256,
            media_type=media_type,
            payload_bytes=payload,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            byte_length=len(payload),
            row_count=row_count,
            csv_header_sha256=csv_header_sha256,
            provider_access=provider_access,
            _authority=_CAPTURE_AUTHORITY,
        )

    def fetch_bulk_status(self, table: str) -> SharadarBulkStatus:
        if table not in TEN_YEAR_TABLES:
            raise ValueError("Sharadar bulk-status table is unsupported")
        requested_at = _canonical_timestamp(self._clock(), "requested_at")
        url = f"{BASE_URL}{API_PATH_PREFIX}{table}"
        try:
            result = self._access.get(
                self._session,
                url,
                params={"api_key": self._api_key, "status": "True"},
                headers={"Accept": "application/json"},
                timeout=30,
                allow_redirects=False,
            )
        except ProviderAccessError:
            raise SharadarCaptureError("Sharadar bulk-status request could not be completed") from None
        response = result.response
        if getattr(response, "status_code", None) != 200:
            raise SharadarCaptureError("Sharadar bulk-status request was rejected")
        headers = getattr(response, "headers", None)
        if not isinstance(headers, Mapping):
            raise SharadarCaptureError("Sharadar bulk-status response headers are invalid")
        media_type = str(headers.get("Content-Type", headers.get("content-type", ""))).split(";", 1)[0].lower()
        if media_type != "application/json":
            raise SharadarCaptureError("Sharadar bulk-status response media type is invalid")
        payload = getattr(response, "content", None)
        root = _strict_json(payload)
        if (
            len(root) > 50
            or not {"table", "name", "size", "modified"}.issubset(root)
            or any(not isinstance(key, str) or len(key) > 200 for key in root)
        ):
            raise ValueError("Sharadar bulk-status fields are unsupported")
        if root.get("table") != table:
            raise ValueError("Sharadar bulk-status table does not match the request")
        retrieved_at = _canonical_timestamp(self._clock(), "retrieved_at")
        if retrieved_at < requested_at:
            raise SharadarCaptureError("Sharadar bulk-status clock moved backwards")
        provider_access = safe_access_dict(result.metadata)
        if provider_access is None:
            raise SharadarCaptureError("Sharadar bulk-status access metadata is invalid")
        return SharadarBulkStatus(
            table=table,
            name=root.get("name"),
            size=root.get("size"),
            modified=root.get("modified"),
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            requested_at=requested_at,
            retrieved_at=retrieved_at,
            provider_access=provider_access,
        )

    @staticmethod
    def _safe_redirect(value: Any, *, base_url: str) -> tuple[str, str, str]:
        if not isinstance(value, str) or not value or len(value) > 16_384:
            raise SharadarCaptureError("Sharadar bulk redirect is invalid")
        resolved_url = urljoin(base_url, value)
        parsed = urlsplit(resolved_url)
        host = parsed.hostname
        labels = host.split(".") if isinstance(host, str) else []
        s3_host = (
            isinstance(host, str)
            and host.endswith(".amazonaws.com")
            and any(label == "s3" or label.startswith("s3-") for label in labels[:-2])
        )
        allowed = (
            host == "api.sharadar.com"
            or (isinstance(host, str) and host.endswith(".sharadar.com"))
            or s3_host
        )
        if (
            parsed.scheme != "https"
            or not allowed
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or not parsed.path
            or parsed.fragment
            or any(part in {"", ".", ".."} for part in parsed.path.split("/")[1:])
        ):
            safe_host = host if isinstance(host, str) and len(host) <= 253 else "invalid-host"
            raise SharadarCaptureError(
                f"Sharadar bulk redirect target was rejected ({safe_host})"
            )
        return (
            resolved_url,
            host,
            hashlib.sha256(parsed.path.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _download_headers(value: Any, table: str) -> int:
        if not isinstance(value, Mapping) or len(value) > MAX_HEADERS:
            raise SharadarCaptureError("Sharadar bulk response headers are invalid")
        media_type = str(value.get("Content-Type", value.get("content-type", ""))).split(";", 1)[0].lower()
        if media_type not in {
            "application/zip",
            "application/x-zip-compressed",
            "application/octet-stream",
            "binary/octet-stream",
        }:
            raise SharadarCaptureError("Sharadar bulk response media type is invalid")
        raw_length = value.get("Content-Length", value.get("content-length"))
        try:
            length = int(raw_length)
        except (TypeError, ValueError) as error:
            raise SharadarCaptureError("Sharadar bulk content length is required") from error
        if not 0 < length <= MAX_COMPRESSED_BYTES[table]:
            raise SharadarCaptureError("Sharadar bulk content length is outside the safe boundary")
        return length

    @staticmethod
    def _validate_archive(path: Path, table: str) -> tuple[str, int, str]:
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if len(members) != 1:
                    raise ValueError("Sharadar bulk archive must contain exactly one member")
                member = members[0]
                mode = member.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if (
                    member.is_dir()
                    or not member.filename.endswith(".csv")
                    or "/" in member.filename
                    or "\\" in member.filename
                    or member.flag_bits & 0x1
                    or file_type not in {0, stat.S_IFREG}
                    or not 0 < member.file_size <= MAX_UNCOMPRESSED_BYTES[table]
                    or member.compress_size <= 0
                    or (
                        member.file_size > 1024 * 1024
                        and member.file_size > member.compress_size * 200
                    )
                ):
                    raise ValueError("Sharadar bulk archive member is unsafe")
                with archive.open(member) as source:
                    header_bytes = source.readline(1024 * 1024 + 1)
                if not header_bytes or len(header_bytes) > 1024 * 1024:
                    raise ValueError("Sharadar bulk CSV header is invalid")
        except (OSError, zipfile.BadZipFile, RuntimeError) as error:
            raise ValueError("Sharadar bulk response is not a valid ZIP archive") from error
        try:
            header_text = header_bytes.decode("utf-8-sig").rstrip("\r\n")
            fields = next(csv.reader([header_text], strict=True))
        except (UnicodeDecodeError, csv.Error, StopIteration) as error:
            raise ValueError("Sharadar bulk CSV header is invalid") from error
        if (
            not fields
            or len(fields) != len(set(fields))
            or any(not field for field in fields)
            or not BULK_REQUIRED_FIELDS[table].issubset(fields)
        ):
            raise ValueError("Sharadar bulk CSV schema is missing required fields")
        return (
            member.filename,
            member.file_size,
            hashlib.sha256(header_text.encode("utf-8")).hexdigest(),
        )

    def download_ten_year_bulk(
        self,
        *,
        status: SharadarBulkStatus,
        quarantine_root: Path,
    ) -> SharadarBulkCapture:
        if not isinstance(status, SharadarBulkStatus) or status.table not in TEN_YEAR_TABLES:
            raise TypeError("status must be a supported SharadarBulkStatus")
        if not isinstance(quarantine_root, Path):
            raise TypeError("quarantine_root must be a Path")
        _private_directory(quarantine_root)
        requested_at = _canonical_timestamp(self._clock(), "requested_at")
        url = f"{BASE_URL}{API_PATH_PREFIX}{status.table}"
        try:
            result = self._access.get(
                self._session,
                url,
                params={"api_key": self._api_key, "years": "10"},
                headers={"Accept": "application/zip"},
                timeout=30,
                allow_redirects=False,
            )
        except ProviderAccessError:
            raise SharadarCaptureError("Sharadar bulk request could not be completed") from None
        response = result.response
        if getattr(response, "status_code", None) not in {301, 302, 303, 307, 308}:
            raise SharadarCaptureError("Sharadar bulk request did not return the required redirect")
        headers = getattr(response, "headers", None)
        if not isinstance(headers, Mapping):
            raise SharadarCaptureError("Sharadar bulk redirect headers are invalid")
        location = headers.get("Location", headers.get("location"))
        current_url = url
        download = None
        redirect_host = ""
        redirect_path_sha256 = ""
        for _ in range(3):
            resolved_url, redirect_host, redirect_path_sha256 = self._safe_redirect(
                location,
                base_url=current_url,
            )
            try:
                candidate = self._session.get(
                    resolved_url,
                    headers={"Accept": "application/zip"},
                    timeout=(20, 600),
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException:
                raise SharadarCaptureError("Sharadar bulk file could not be downloaded") from None
            candidate_status = getattr(candidate, "status_code", None)
            if candidate_status == 200:
                download = candidate
                break
            if candidate_status not in {301, 302, 303, 307, 308}:
                if callable(getattr(candidate, "close", None)):
                    candidate.close()
                raise SharadarCaptureError("Sharadar bulk file request was rejected")
            candidate_headers = getattr(candidate, "headers", None)
            if not isinstance(candidate_headers, Mapping):
                if callable(getattr(candidate, "close", None)):
                    candidate.close()
                raise SharadarCaptureError("Sharadar bulk redirect headers are invalid")
            location = candidate_headers.get("Location", candidate_headers.get("location"))
            current_url = resolved_url
            if callable(getattr(candidate, "close", None)):
                candidate.close()
        if download is None:
            raise SharadarCaptureError("Sharadar bulk redirect limit was exceeded")
        try:
            observed_size = self._download_headers(
                getattr(download, "headers", None),
                status.table,
            )
        except (SharadarCaptureError, TypeError, ValueError):
            if callable(getattr(download, "close", None)):
                download.close()
            raise
        if shutil.disk_usage(quarantine_root).free < observed_size * 2:
            if callable(getattr(download, "close", None)):
                download.close()
            raise SharadarCaptureError("Sharadar bulk download lacks required disk headroom")

        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=f".{status.table}-",
                suffix=".partial",
                dir=quarantine_root,
            )
        except OSError:
            if callable(getattr(download, "close", None)):
                download.close()
            raise
        partial = Path(raw_path)
        digest = hashlib.sha256()
        total = 0
        try:
            try:
                try:
                    iterator = download.iter_content(1024 * 1024)
                    for chunk in iterator:
                        if not isinstance(chunk, bytes):
                            raise SharadarCaptureError("Sharadar bulk response chunk is invalid")
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > observed_size:
                            raise SharadarCaptureError("Sharadar bulk response exceeded its declared size")
                        digest.update(chunk)
                        _write_all(descriptor, chunk)
                    os.fsync(descriptor)
                except requests.RequestException:
                    raise SharadarCaptureError("Sharadar bulk stream could not be completed") from None
            finally:
                os.close(descriptor)
            if total != observed_size:
                raise SharadarCaptureError("Sharadar bulk response size did not match its declaration")
            payload_sha256 = digest.hexdigest()
            archive_member, archive_member_declared_bytes, csv_header_sha256 = self._validate_archive(
                partial, status.table
            )
            final = quarantine_root / f"{status.table}-10y-{payload_sha256}.csv.zip"
            if final.exists():
                details = final.lstat()
                if (
                    final.is_symlink()
                    or not stat.S_ISREG(details.st_mode)
                    or details.st_uid != os.geteuid()
                    or stat.S_IMODE(details.st_mode) != 0o400
                ):
                    raise ValueError("Sharadar bulk quarantine file is unsafe")
                final_hash, final_size = _file_sha256(
                    final,
                    maximum_bytes=MAX_COMPRESSED_BYTES[status.table],
                )
                if final_size != total or final_hash != payload_sha256:
                    raise ValueError("Sharadar bulk quarantine file failed hash verification")
                partial.unlink()
            else:
                os.chmod(partial, 0o400)
                os.replace(partial, final)
                _fsync_directory(quarantine_root)
            retrieved_at = _canonical_timestamp(self._clock(), "retrieved_at")
            if retrieved_at < requested_at:
                raise SharadarCaptureError("Sharadar bulk download clock moved backwards")
            return SharadarBulkCapture(
                table=status.table,
                years=10,
                requested_at=requested_at,
                retrieved_at=retrieved_at,
                payload_sha256=payload_sha256,
                byte_length=total,
                archive_member=archive_member,
                archive_member_declared_bytes=archive_member_declared_bytes,
                csv_header_sha256=csv_header_sha256,
                redirect_host=redirect_host,
                redirect_path_sha256=redirect_path_sha256,
                status=status,
                _authority=_CAPTURE_AUTHORITY,
            )
        finally:
            if callable(getattr(download, "close", None)):
                download.close()
            if partial.exists():
                partial.unlink()


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Sharadar quarantine write made no progress")
        offset += count


def _file_sha256(path: Path, *, maximum_bytes: int) -> tuple[str, int]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    total = 0
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise ValueError("Sharadar quarantine file is unsafe")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return digest.hexdigest(), total
            total += len(chunk)
            if total > maximum_bytes:
                raise ValueError("Sharadar quarantine file exceeds its safe size limit")
            digest.update(chunk)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _private_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    details = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise ValueError("Sharadar quarantine directory must be owner-only")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if opened.st_dev != details.st_dev or opened.st_ino != details.st_ino:
            raise ValueError("Sharadar quarantine directory changed during verification")
    finally:
        os.close(descriptor)


def _verified_ledger_records(descriptor: int) -> tuple[dict[str, Any], ...]:
    details = os.fstat(descriptor)
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_nlink != 1
        or details.st_size > 4 * 1024 * 1024
    ):
        raise ValueError("Sharadar quarantine ledger is unsafe")
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > 4 * 1024 * 1024:
            raise ValueError("Sharadar quarantine ledger exceeds its safe size limit")
        chunks.append(chunk)
    payload = b"".join(chunks)
    if payload and not payload.endswith(b"\n"):
        raise ValueError("Sharadar quarantine ledger has a partial record")
    previous_hash = GENESIS_HASH
    records: list[dict[str, Any]] = []
    for raw_line in payload.splitlines():
        try:
            record = json.loads(raw_line)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("Sharadar quarantine ledger contains invalid JSON") from error
        if not isinstance(record, dict):
            raise ValueError("Sharadar quarantine ledger records must be objects")
        if record.get("previous_hash") != previous_hash:
            raise ValueError("Sharadar quarantine ledger chain is invalid")
        supplied = record.get("record_hash")
        material = {key: value for key, value in record.items() if key != "record_hash"}
        if (
            not isinstance(supplied, str)
            or hashlib.sha256(_canonical_json(material)).hexdigest() != supplied
        ):
            raise ValueError("Sharadar quarantine ledger hash is invalid")
        previous_hash = supplied
        records.append(record)
    return tuple(records)


def _read_ledger(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        return ()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        return _verified_ledger_records(descriptor)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _append_ledger(
    path: Path,
    material: Mapping[str, Any],
    *,
    duplicate_identity: tuple[str, ...],
) -> Mapping[str, Any]:
    descriptor = os.open(
        path,
        os.O_RDWR
        | os.O_CREAT
        | os.O_APPEND
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        records = _verified_ledger_records(descriptor)
        for existing in records:
            if all(existing.get(name) == material.get(name) for name in duplicate_identity):
                return MappingProxyType(existing)
        previous_hash = records[-1]["record_hash"] if records else GENESIS_HASH
        record_material = {**material, "previous_hash": previous_hash}
        record = {
            **record_material,
            "record_hash": hashlib.sha256(_canonical_json(record_material)).hexdigest(),
        }
        os.lseek(descriptor, 0, os.SEEK_END)
        _write_all(descriptor, _canonical_json(record) + b"\n")
        os.fsync(descriptor)
        return MappingProxyType(record)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        _fsync_directory(path.parent)


def persist_probe(root: Path, probe: SharadarFetchedProbe) -> Mapping[str, Any]:
    """Persist exact licensed bytes and a hash-chained, credential-free record."""

    if not isinstance(root, Path) or not isinstance(probe, SharadarFetchedProbe):
        raise TypeError("root and probe have invalid types")
    _private_directory(root)
    blob_directory = root / "blobs"
    _private_directory(blob_directory)
    blob = blob_directory / f"{probe.payload_sha256}.csv"
    if blob.exists():
        details = blob.lstat()
        if (
            blob.is_symlink()
            or not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) != 0o400
        ):
            raise ValueError("Sharadar quarantine blob is unsafe")
        existing_hash, existing_size = _file_sha256(
            blob,
            maximum_bytes=MAX_SOURCE_BYTES,
        )
        if existing_size != probe.byte_length or existing_hash != probe.payload_sha256:
            raise ValueError("Sharadar quarantine blob failed hash verification")
    else:
        descriptor = os.open(
            blob,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o400,
        )
        try:
            _write_all(descriptor, probe.payload_bytes)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(blob_directory)

    material = {
        **probe.as_record(),
        "blob_relative_path": f"blobs/{probe.payload_sha256}.csv",
    }
    return _append_ledger(
        root / "captures.jsonl",
        material,
        duplicate_identity=("table", "role", "payload_sha256", "requested_at"),
    )


def persist_bulk_capture(
    root: Path, capture: SharadarBulkCapture
) -> Mapping[str, Any]:
    """Append a non-authoritative record for one verified ten-year archive."""

    if not isinstance(root, Path) or not isinstance(capture, SharadarBulkCapture):
        raise TypeError("root and capture have invalid types")
    _private_directory(root)
    archive = root / f"{capture.table}-10y-{capture.payload_sha256}.csv.zip"
    if not archive.exists():
        raise ValueError("Sharadar bulk quarantine archive is missing")
    details = archive.lstat()
    if (
        archive.is_symlink()
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o400
        or details.st_size != capture.byte_length
    ):
        raise ValueError("Sharadar bulk quarantine archive failed verification")
    archive_hash, archive_size = _file_sha256(
        archive,
        maximum_bytes=MAX_COMPRESSED_BYTES[capture.table],
    )
    if archive_size != capture.byte_length or archive_hash != capture.payload_sha256:
        raise ValueError("Sharadar bulk quarantine archive failed verification")
    material = {
        **capture.as_record(),
        "blob_relative_path": archive.name,
    }
    return _append_ledger(
        root / "bulk_captures.jsonl",
        material,
        duplicate_identity=("table", "payload_sha256"),
    )


def _existing_bulk_record(
    root: Path,
    status: SharadarBulkStatus,
) -> Mapping[str, Any] | None:
    for record in reversed(_read_ledger(root / "bulk_captures.jsonl")):
        if (
            record.get("table") != status.table
            or record.get("status_payload_sha256") != status.payload_sha256
        ):
            continue
        payload_sha256 = record.get("payload_sha256")
        byte_length = record.get("byte_length")
        expected_name = f"{status.table}-10y-{payload_sha256}.csv.zip"
        if (
            not isinstance(payload_sha256, str)
            or _SHA256_PATTERN.fullmatch(payload_sha256) is None
            or isinstance(byte_length, bool)
            or not isinstance(byte_length, int)
            or record.get("blob_relative_path") != expected_name
        ):
            raise ValueError("Sharadar bulk quarantine record is invalid")
        archive = root / expected_name
        try:
            details = archive.lstat()
        except FileNotFoundError:
            raise ValueError(
                "Sharadar bulk quarantine archive is missing; explicit quarantine recovery is required"
            ) from None
        if (
            archive.is_symlink()
            or not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) != 0o400
            or details.st_size != byte_length
        ):
            raise ValueError("Sharadar bulk quarantine archive failed verification")
        digest, size = _file_sha256(
            archive,
            maximum_bytes=MAX_COMPRESSED_BYTES[status.table],
        )
        if digest != payload_sha256 or size != byte_length:
            raise ValueError("Sharadar bulk quarantine archive failed verification")
        return MappingProxyType(record)
    return None


def execute_connectivity_capture(
    *,
    repository_root: Path,
    api_key: str,
    session: requests.Session | None = None,
    access: ProviderAccessCoordinator | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Run the three frozen AAPL probes; this does not download bulk history."""

    if not isinstance(repository_root, Path):
        raise TypeError("repository_root must be a Path")
    client = SharadarSampleClient(
        api_key,
        session=session,
        access=access,
        clock=clock,
    )
    target = repository_root / QUARANTINE_RELATIVE_PATH
    return tuple(persist_probe(target, client.fetch(item)) for item in PROBE_DEFINITIONS)


def inspect_ten_year_bulk_status(
    *,
    api_key: str,
    session: requests.Session | None = None,
    access: ProviderAccessCoordinator | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[SharadarBulkStatus, ...]:
    """Inspect exact provider file metadata without downloading bulk bytes."""

    client = SharadarSampleClient(
        api_key,
        session=session,
        access=access,
        clock=clock,
    )
    return tuple(client.fetch_bulk_status(table) for table in TEN_YEAR_TABLES)


def execute_ten_year_bulk_capture(
    *,
    repository_root: Path,
    api_key: str,
    session: requests.Session | None = None,
    access: ProviderAccessCoordinator | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Download the frozen five-table ten-year foundation into quarantine."""

    if not isinstance(repository_root, Path):
        raise TypeError("repository_root must be a Path")
    client = SharadarSampleClient(
        api_key,
        session=session,
        access=access,
        clock=clock,
    )
    target = repository_root / QUARANTINE_RELATIVE_PATH
    records: list[Mapping[str, Any]] = []
    for table in TEN_YEAR_TABLES:
        status = client.fetch_bulk_status(table)
        existing = _existing_bulk_record(target, status)
        if existing is not None:
            records.append(existing)
            continue
        capture = client.download_ten_year_bulk(status=status, quarantine_root=target)
        records.append(persist_bulk_capture(target, capture))
    return tuple(records)
