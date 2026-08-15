from __future__ import annotations

"""Fail-closed Massive custom-bar sample ingestion.

The adapter accepts the documented Massive custom-bars JSON shape or an exact
CSV projection of that shape.  It stages only unadjusted daily session bars and
passes every staged observation through the canonical historical-role cutoff
validator.  Because Massive's custom-bar response does not document a
historical publication timestamp or stable row identifier, this pilot uses the
local receipt timestamp as both ``effective_at`` and ``available_at`` and marks
those conservative bases explicitly in the payload.  It therefore cannot make
the sample available to an earlier replay decision or qualify provider payload
semantics.

The optional fetch client makes one bounded HTTPS GET to the fixed Massive API
host, puts the key only in an Authorization header, rejects redirects and never
touches any broker or execution surface.
"""

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import hashlib
import io
import json
import math
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

import requests

from core.data_sources.provider_access import (
    ProviderAccessCoordinator,
    ProviderAccessError,
    ProviderAccessPolicy,
    safe_access_dict,
)
from core.decision_ledger import canonical_timestamp
from core.orchestration.historical_role_cutoff import (
    SCHEMA_VERSION as OBSERVATION_SCHEMA_VERSION,
    normalized_payload_sha256,
    validate_historical_role_cutoff_observations,
)


PROVIDER_ID = "MASSIVE"
DATASET_ID = "MASSIVE_STOCKS_CUSTOM_BARS_V2"
ROLE = "RAW_DAILY_SESSION_BARS"
JSON_FORMAT = "JSON"
CSV_FORMAT = "CSV"
SUPPORTED_FORMATS = frozenset({JSON_FORMAT, CSV_FORMAT})
MAX_SOURCE_BYTES = 5 * 1024 * 1024
MAX_RECORDS = 120
MAX_RANGE_DAYS = 31
CSV_FIELDS = ("ticker", "adjusted", "o", "h", "l", "c", "v", "t", "n", "vw", "otc")
ROOT_REQUIRED_FIELDS = frozenset({"ticker", "adjusted", "status", "results"})
ROOT_OPTIONAL_FIELDS = frozenset(
    {"queryCount", "resultsCount", "count", "request_id", "next_url"}
)
BAR_REQUIRED_FIELDS = frozenset({"o", "h", "l", "c", "v", "t"})
BAR_OPTIONAL_FIELDS = frozenset({"n", "vw", "otc"})
_SYMBOL_PATTERN = re.compile(r"[A-Z][A-Z0-9.\-]{0,14}")
_STAGING_AUTHORITY = object()
SAFETY_FLAG_NAMES = (
    "provider_payload_semantics_qualified",
    "source_bytes_authenticated",
    "observation_selection_validated",
    "role_coverage_validated",
    "engine_input_ready",
    "replay_executed",
    "broker_connection_allowed",
    "orders_submitted",
    "live_trading_enabled",
)


class MassiveHistoricalIngestError(RuntimeError):
    """A stable, secret-free Massive sample failure."""


def _canonical_timestamp(value: Any, name: str) -> str:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(
            timezone.utc
        ).isoformat(timespec="microseconds")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _strict_json(payload: bytes) -> Any:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Massive sample must be strict UTF-8") from error
    if "\x00" in text:
        raise ValueError("Massive sample must not contain NUL bytes")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant is unsupported: {value}")

    def unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON object repeats field: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, ValueError, RecursionError) as error:
        raise ValueError("Massive sample must be strict JSON with unique fields") from error


def _symbol(value: Any) -> str:
    if not isinstance(value, str) or not _SYMBOL_PATTERN.fullmatch(value):
        raise ValueError("ticker must be a canonical uppercase U.S. stock symbol")
    return value


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    try:
        resolved = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be finite") from error
    if not math.isfinite(resolved) or (positive and resolved <= 0):
        raise ValueError(f"{name} must be finite{' and positive' if positive else ''}")
    return resolved


def _integer(value: Any, name: str, *, nonnegative: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if nonnegative and value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def _parse_bar(value: Mapping[str, Any], *, ticker: str, adjusted: bool) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("every Massive result must be an object")
    fields = set(value)
    if not BAR_REQUIRED_FIELDS.issubset(fields) or fields - BAR_REQUIRED_FIELDS - BAR_OPTIONAL_FIELDS:
        raise ValueError("Massive bar has missing or unsupported fields")
    if adjusted:
        raise ValueError("adjusted Massive bars cannot satisfy RAW_DAILY_SESSION_BARS")
    opened = _number(value.get("o"), "o", positive=True)
    high = _number(value.get("h"), "h", positive=True)
    low = _number(value.get("l"), "l", positive=True)
    closed = _number(value.get("c"), "c", positive=True)
    volume = _number(value.get("v"), "v")
    if float(volume) < 0:
        raise ValueError("v must be nonnegative")
    if float(high) < max(float(opened), float(low), float(closed)) or float(low) > min(
        float(opened), float(high), float(closed)
    ):
        raise ValueError("Massive OHLC values are inconsistent")
    timestamp_ms = _integer(value.get("t"), "t", nonnegative=True)
    try:
        window_start = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError("t is outside the supported timestamp range") from error

    payload: dict[str, Any] = {
        "ticker": ticker,
        "bar_window_start_at": window_start.isoformat(timespec="milliseconds"),
        "open": opened,
        "high": high,
        "low": low,
        "close": closed,
        "volume": volume,
        "adjusted": False,
        "effective_at_basis": "LOCAL_RETRIEVAL_ONLY_UNQUALIFIED",
        "available_at_basis": "LOCAL_RETRIEVAL_ONLY_UNQUALIFIED",
        "provider_record_identity_basis": "DERIVED_TICKER_AND_WINDOW_START_UNQUALIFIED",
    }
    if "n" in value and value.get("n") is not None:
        payload["transaction_count"] = _integer(value.get("n"), "n", nonnegative=True)
    if "vw" in value and value.get("vw") is not None:
        payload["volume_weighted_average"] = _number(value.get("vw"), "vw", positive=True)
    if "otc" in value and value.get("otc") is not None:
        payload["otc"] = _boolean(value.get("otc"), "otc")
    return {
        "timestamp_ms": timestamp_ms,
        "window_start": window_start,
        "payload": payload,
    }


def _json_bars(payload: bytes) -> list[dict[str, Any]]:
    root = _strict_json(payload)
    if not isinstance(root, Mapping):
        raise ValueError("Massive JSON sample must be an object")
    fields = set(root)
    if not ROOT_REQUIRED_FIELDS.issubset(fields) or fields - ROOT_REQUIRED_FIELDS - ROOT_OPTIONAL_FIELDS:
        raise ValueError("Massive response has missing or unsupported fields")
    if root.get("status") != "OK":
        raise ValueError("Massive response status is not OK")
    if root.get("next_url") is not None:
        raise ValueError("paginated Massive samples are incomplete and unsupported")
    ticker = _symbol(root.get("ticker"))
    adjusted = _boolean(root.get("adjusted"), "adjusted")
    results = root.get("results")
    if not isinstance(results, list) or not results or len(results) > MAX_RECORDS:
        raise ValueError("Massive results must be a bounded nonempty list")
    for name in ("queryCount", "resultsCount", "count"):
        if name in root and root.get(name) is not None:
            _integer(root.get(name), name, nonnegative=True)
    if "resultsCount" in root and root.get("resultsCount") != len(results):
        raise ValueError("Massive resultsCount does not match results")
    return [_parse_bar(item, ticker=ticker, adjusted=adjusted) for item in results]


def _csv_value(value: str, name: str) -> Any:
    if name in {"o", "h", "l", "c", "v", "vw"}:
        if value == "" and name == "vw":
            return None
        try:
            return float(value)
        except ValueError as error:
            raise ValueError(f"CSV {name} must be numeric") from error
    if name in {"t", "n"}:
        if value == "" and name == "n":
            return None
        try:
            return int(value)
        except ValueError as error:
            raise ValueError(f"CSV {name} must be an integer") from error
    if name in {"adjusted", "otc"}:
        if value == "" and name == "otc":
            return None
        if value not in {"true", "false"}:
            raise ValueError(f"CSV {name} must be true or false")
        return value == "true"
    return value


def _csv_bars(payload: bytes) -> list[dict[str, Any]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Massive sample must be strict UTF-8") from error
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ValueError("Massive CSV must use strict RFC-style quoting") from error
    if not rows or tuple(rows[0]) != CSV_FIELDS:
        raise ValueError("Massive CSV header must match the documented projection")
    body = rows[1:]
    if not body or len(body) > MAX_RECORDS:
        raise ValueError("Massive CSV must contain a bounded nonempty body")
    parsed: list[dict[str, Any]] = []
    expected_ticker: str | None = None
    for row in body:
        if len(row) != len(CSV_FIELDS):
            raise ValueError("Massive CSV row has the wrong field count")
        value = {name: _csv_value(item, name) for name, item in zip(CSV_FIELDS, row)}
        ticker = _symbol(value.pop("ticker"))
        if expected_ticker is None:
            expected_ticker = ticker
        elif ticker != expected_ticker:
            raise ValueError("Massive CSV cannot mix tickers")
        adjusted = _boolean(value.pop("adjusted"), "adjusted")
        parsed.append(_parse_bar(value, ticker=ticker, adjusted=adjusted))
    return parsed


@dataclass(frozen=True, slots=True)
class MassiveHistoricalSource:
    transport_format: str
    retrieved_at: str | datetime
    payload_bytes: bytes

    def __post_init__(self) -> None:
        transport = str(self.transport_format).upper()
        if transport not in SUPPORTED_FORMATS:
            raise ValueError("transport_format must be JSON or CSV")
        payload = self.payload_bytes
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_SOURCE_BYTES:
            raise ValueError("payload_bytes must contain bounded nonempty bytes")
        object.__setattr__(self, "transport_format", transport)
        object.__setattr__(self, "retrieved_at", _canonical_timestamp(self.retrieved_at, "retrieved_at"))


@dataclass(frozen=True, slots=True)
class MassiveHistoricalStagingBatch:
    decision_at: str
    retrieved_at: str
    observations: tuple[Mapping[str, Any], ...]
    source_payload_sha256: str
    staging_sha256: str
    provider_payload_semantics_qualified: bool = False
    source_bytes_authenticated: bool = False
    observation_selection_validated: bool = False
    role_coverage_validated: bool = False
    engine_input_ready: bool = False
    replay_executed: bool = False
    broker_connection_allowed: bool = False
    orders_submitted: bool = False
    live_trading_enabled: bool = False
    _authority: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._authority is not _STAGING_AUTHORITY:
            raise PermissionError("MassiveHistoricalStagingBatch must be issued by the adapter")
        object.__setattr__(self, "_authority", None)
        if any(getattr(self, name) is not False for name in SAFETY_FLAG_NAMES):
            raise ValueError("Massive staging safety flags were altered")

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": PROVIDER_ID,
            "provider_dataset_id": DATASET_ID,
            "role": ROLE,
            "decision_at": self.decision_at,
            "retrieved_at": self.retrieved_at,
            "record_count": len(self.observations),
            "source_payload_sha256": self.source_payload_sha256,
            "staging_sha256": self.staging_sha256,
            "provider_payload_semantics_qualified": False,
            "source_bytes_authenticated": False,
            "observation_selection_validated": False,
            "role_coverage_validated": False,
            "engine_input_ready": False,
            "replay_executed": False,
            "broker_connection_allowed": False,
            "orders_submitted": False,
            "live_trading_enabled": False,
        }


class MassiveHistoricalAdapter:
    def normalize(
        self,
        *,
        source: MassiveHistoricalSource,
        decision_at: str | datetime,
    ) -> MassiveHistoricalStagingBatch:
        if not isinstance(source, MassiveHistoricalSource):
            raise ValueError("source must be a MassiveHistoricalSource")
        cutoff = _canonical_timestamp(decision_at, "decision_at")
        bars = _json_bars(source.payload_bytes) if source.transport_format == JSON_FORMAT else _csv_bars(source.payload_bytes)
        source_hash = hashlib.sha256(source.payload_bytes).hexdigest()
        observations: list[dict[str, Any]] = []
        retrieved = datetime.fromisoformat(str(source.retrieved_at))
        for bar in bars:
            if bar["window_start"] >= retrieved:
                raise ValueError("Massive bar window cannot start at or after local retrieval")
            record_id = f"{DATASET_ID}:{bar['payload']['ticker']}:{bar['timestamp_ms']}"
            observations.append(
                {
                    "schema_version": OBSERVATION_SCHEMA_VERSION,
                    "role": ROLE,
                    "provider_id": PROVIDER_ID,
                    "provider_dataset_id": DATASET_ID,
                    "provider_record_id": record_id,
                    "effective_at": source.retrieved_at,
                    "available_at": source.retrieved_at,
                    "retrieved_at": source.retrieved_at,
                    "observation_cutoff_at": cutoff,
                    "source_payload_sha256": source_hash,
                    "normalized_payload_sha256": normalized_payload_sha256(bar["payload"]),
                    "payload": bar["payload"],
                }
            )
        observations.sort(key=lambda item: item["provider_record_id"])
        validated = validate_historical_role_cutoff_observations(
            role=ROLE,
            decision_at=cutoff,
            observations=observations,
        )
        staging_material = {
            "provider_id": PROVIDER_ID,
            "provider_dataset_id": DATASET_ID,
            "role": ROLE,
            "decision_at": cutoff,
            "retrieved_at": source.retrieved_at,
            "source_payload_sha256": source_hash,
            "observation_hashes": [item["normalized_payload_sha256"] for item in validated],
            "safety_flags": {name: False for name in SAFETY_FLAG_NAMES},
        }
        staging_hash = hashlib.sha256(
            json.dumps(staging_material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return MassiveHistoricalStagingBatch(
            decision_at=cutoff,
            retrieved_at=str(source.retrieved_at),
            observations=tuple(MappingProxyType(dict(item)) for item in validated),
            source_payload_sha256=source_hash,
            staging_sha256=staging_hash,
            _authority=_STAGING_AUTHORITY,
        )


@dataclass(frozen=True, slots=True)
class MassiveFetchedPayload:
    payload_bytes: bytes
    retrieved_at: str
    provider_access: Mapping[str, Any]


class MassiveHistoricalSampleClient:
    BASE_URL = "https://api.massive.com"
    ACCESS_POLICY = ProviderAccessPolicy(maximum_attempts=1)

    def __init__(
        self,
        api_key: str,
        *,
        session: requests.Session | None = None,
        access: ProviderAccessCoordinator | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key or api_key.strip() != api_key:
            raise ValueError("Massive API key must be nonempty canonical text")
        if any(not 33 <= ord(character) <= 126 for character in api_key) or len(api_key) > 500:
            raise ValueError("Massive API key has an invalid format")
        self._api_key = api_key
        self._session = session or requests.Session()
        self._access = access or ProviderAccessCoordinator.for_provider(
            "POLYGON_API_KEY",
            "Massive historical sample",
            policy=self.ACCESS_POLICY,
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch_daily_bars(self, *, symbol: str, start: str, end: str) -> MassiveFetchedPayload:
        ticker = _symbol(symbol)
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except (TypeError, ValueError) as error:
            raise ValueError("start and end must be ISO dates") from error
        if end_date < start_date or (end_date - start_date).days >= MAX_RANGE_DAYS:
            raise ValueError("Massive sample date range must be ordered and at most 31 days")
        requested_at = datetime.fromisoformat(
            _canonical_timestamp(self._clock(), "request clock")
        )
        if end_date >= requested_at.date():
            raise ValueError("Massive historical sample must end before the request date")
        url = f"{self.BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{start_date.isoformat()}/{end_date.isoformat()}"
        try:
            result = self._access.get(
                self._session,
                url,
                params={"adjusted": "false", "sort": "asc", "limit": MAX_RECORDS},
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=20,
                allow_redirects=False,
            )
        except ProviderAccessError:
            raise MassiveHistoricalIngestError("Massive sample request could not be completed") from None
        response = result.response
        status = getattr(response, "status_code", None)
        if status in {301, 302, 303, 307, 308}:
            raise MassiveHistoricalIngestError("Massive sample redirect was rejected")
        if status != 200:
            raise MassiveHistoricalIngestError("Massive sample request was rejected")
        payload = getattr(response, "content", None)
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_SOURCE_BYTES:
            raise MassiveHistoricalIngestError("Massive sample response size is invalid")
        retrieved_at = _canonical_timestamp(self._clock(), "retrieved_at")
        access_metadata = safe_access_dict(result.metadata)
        if access_metadata is None:
            raise MassiveHistoricalIngestError("Massive access metadata is invalid")
        return MassiveFetchedPayload(
            payload_bytes=payload,
            retrieved_at=retrieved_at,
            provider_access=MappingProxyType(access_metadata),
        )
