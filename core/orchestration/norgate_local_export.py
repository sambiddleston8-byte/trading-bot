from __future__ import annotations

"""Fail-closed staging for local Norgate Data exports.

Norgate's updater and Python package run inside the Windows extraction VM.  This
module deliberately has no Norgate, network, credential, broker, or execution
dependency: it accepts only exact JSON bytes copied out of that VM.  The ingest
CLI stamps the system clock at file read; programmatic callers carry an explicit
unqualified receipt basis. Historical publication/correction timing,
ticker-history, adjustment and padding semantics are not yet qualified, so
every downstream authority flag remains false. Provider rows are
license-restricted and must not be stored in the source repository.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from core.decision_ledger import canonical_timestamp
from core.orchestration.historical_role_cutoff import (
    SCHEMA_VERSION as OBSERVATION_SCHEMA_VERSION,
    normalized_payload_sha256,
    validate_historical_role_cutoff_observations,
)


PROVIDER_ID = "NORGATE"
DATASET_ID = "NORGATE_US_STOCKS_PLATINUM_LOCAL_V1"
EXPORT_CONTRACT = "NORGATE_LOCAL_EXPORT_V1"
BAR_ROLE = "RAW_DAILY_SESSION_BARS"
MEMBERSHIP_ROLE = "POINT_IN_TIME_SUPPLEMENTAL_PROVIDER_EVIDENCE"
INDEX_NAME = "S&P 500"
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 200_000
_STAGING_AUTHORITY = object()
_SYMBOL_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9.\-/]{0,31}")
_VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "export_contract",
        "provider_id",
        "provider_dataset_id",
        "norgatedata_package_version",
        "database_name",
        "database_update_at",
        "universe_selection_basis",
        "requested_symbols",
        "requested_symbols_sha256",
        "reused_symbols",
        "license_restricted_provider_data",
        "source_code_repository_storage_allowed",
        "exported_at",
        "requested_start",
        "requested_end",
        "frequency",
        "stock_price_adjustment",
        "padding",
        "membership_dataset",
        "rows",
    }
)
_ROW_FIELDS = frozenset(
    {
        "asset_id",
        "requested_symbol",
        "symbol",
        "security_name",
        "session_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "unadjusted_close",
        "dividend",
        "sp500_constituent",
    }
)
SAFETY_FLAG_NAMES = (
    "quarantine_capture_bound",
    "provider_payload_semantics_qualified",
    "source_bytes_authenticated",
    "historical_ticker_history_qualified",
    "historical_availability_qualified",
    "coverage_completeness_proven",
    "observation_selection_validated",
    "role_coverage_validated",
    "engine_input_ready",
    "performance_use_allowed",
    "replay_executed",
    "validation_accessed",
    "test_accessed",
    "broker_connection_allowed",
    "orders_submitted",
    "live_trading_enabled",
)


def _canonical_timestamp(value: Any, name: str) -> str:
    try:
        resolved = datetime.fromisoformat(canonical_timestamp(value))
        if resolved.tzinfo is None or resolved.utcoffset() is None:
            raise ValueError
        return resolved.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _strict_json(payload: bytes) -> Any:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Norgate export must be strict UTF-8") from error
    if "\x00" in text:
        raise ValueError("Norgate export must not contain NUL bytes")

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
        raise ValueError(
            "Norgate export must be strict JSON with unique fields"
        ) from error


def _text(value: Any, name: str, *, maximum: int = 300) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    if (
        not value
        or value != value.strip()
        or len(value) > maximum
        or not value.isprintable()
    ):
        raise ValueError(f"{name} must be nonempty canonical text")
    return value


def _iso_date(value: Any, name: str) -> date:
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a canonical ISO date") from error


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


def _parse_row(value: Any, *, start: date, end: date) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _ROW_FIELDS:
        raise ValueError("Norgate row has missing or unsupported fields")
    asset_id = value.get("asset_id")
    if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id <= 0:
        raise ValueError("asset_id must be a positive stable numerical identifier")
    requested_symbol = _text(
        value.get("requested_symbol"), "requested_symbol", maximum=32
    )
    symbol = _text(value.get("symbol"), "symbol", maximum=32)
    if not _SYMBOL_PATTERN.fullmatch(requested_symbol):
        raise ValueError("requested_symbol must be a canonical uppercase U.S. stock symbol")
    if not _SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError("symbol must be a canonical uppercase U.S. stock symbol")
    security_name = _text(value.get("security_name"), "security_name", maximum=300)
    session = _iso_date(value.get("session_date"), "session_date")
    if not start <= session <= end:
        raise ValueError("Norgate row lies outside the requested date range")
    opened = _number(value.get("open"), "open", positive=True)
    high = _number(value.get("high"), "high", positive=True)
    low = _number(value.get("low"), "low", positive=True)
    close = _number(value.get("close"), "close", positive=True)
    unadjusted_close = _number(
        value.get("unadjusted_close"), "unadjusted_close", positive=True
    )
    volume = _number(value.get("volume"), "volume")
    dividend = _number(value.get("dividend"), "dividend")
    if volume < 0 or dividend < 0:
        raise ValueError("volume and dividend must be nonnegative")
    if high < max(opened, low, close) or low > min(opened, high, close):
        raise ValueError("Norgate OHLC values are inconsistent")
    if not math.isclose(close, unadjusted_close, rel_tol=0.0, abs_tol=1e-10):
        raise ValueError("NONE-adjusted close must equal unadjusted close")
    member = value.get("sp500_constituent")
    if not isinstance(member, bool):
        raise ValueError("sp500_constituent must be boolean")
    return {
        "asset_id": asset_id,
        "requested_symbol": requested_symbol,
        "symbol": symbol,
        "security_name": security_name,
        "session_date": session.isoformat(),
        "open": opened,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "unadjusted_close": unadjusted_close,
        "dividend": dividend,
        "sp500_constituent": member,
    }


def _parse_export(
    payload: bytes,
) -> tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...]]:
    root = _strict_json(payload)
    if not isinstance(root, Mapping) or set(root) != _TOP_LEVEL_FIELDS:
        raise ValueError("Norgate export has missing or unsupported top-level fields")
    expected = {
        "schema_version": "1.0",
        "export_contract": EXPORT_CONTRACT,
        "provider_id": PROVIDER_ID,
        "provider_dataset_id": DATASET_ID,
        "frequency": "DAILY",
        "stock_price_adjustment": "NONE",
        "padding": "NONE",
        "membership_dataset": INDEX_NAME,
    }
    for name, required in expected.items():
        if root.get(name) != required:
            raise ValueError(f"Norgate export {name} is unsupported")
    package_version = _text(
        root.get("norgatedata_package_version"),
        "norgatedata_package_version",
        maximum=30,
    )
    if not _VERSION_PATTERN.fullmatch(package_version):
        raise ValueError("Norgate package version must be canonical")
    database_name = _text(root.get("database_name"), "database_name", maximum=100)
    database_update_at = _canonical_timestamp(
        root.get("database_update_at"), "database_update_at"
    )
    exported_at = _canonical_timestamp(root.get("exported_at"), "exported_at")
    if database_update_at > exported_at:
        raise ValueError("Norgate database update cannot postdate export")
    if root.get("universe_selection_basis") != "OPERATOR_SUPPLIED_SYMBOLS_UNQUALIFIED":
        raise ValueError("Norgate universe selection basis is unsupported")
    requested_symbols = root.get("requested_symbols")
    if (
        not isinstance(requested_symbols, list)
        or not requested_symbols
        or len(requested_symbols) > 100
        or len(requested_symbols) != len(set(requested_symbols))
    ):
        raise ValueError("Norgate requested_symbols must be a bounded unique list")
    for symbol in requested_symbols:
        if not isinstance(symbol, str) or not _SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError("Norgate requested_symbols contains an invalid symbol")
    requested_hash = root.get("requested_symbols_sha256")
    expected_requested_hash = hashlib.sha256(
        json.dumps(requested_symbols, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if requested_hash != expected_requested_hash:
        raise ValueError("Norgate requested symbol hash does not match the list")
    reused_symbols = root.get("reused_symbols")
    if (
        not isinstance(reused_symbols, list)
        or reused_symbols != sorted(set(reused_symbols))
        or any(
            not isinstance(symbol, str) or not _SYMBOL_PATTERN.fullmatch(symbol)
            for symbol in reused_symbols
        )
    ):
        raise ValueError("Norgate reused_symbols must be a canonical unique list")
    if root.get("license_restricted_provider_data") is not True:
        raise ValueError("Norgate provider data must remain license restricted")
    if root.get("source_code_repository_storage_allowed") is not False:
        raise ValueError("Norgate provider rows cannot be stored in the source repository")
    start = _iso_date(root.get("requested_start"), "requested_start")
    end = _iso_date(root.get("requested_end"), "requested_end")
    if end < start:
        raise ValueError("Norgate requested date range is reversed")
    rows = root.get("rows")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_RECORDS:
        raise ValueError("Norgate rows must be a bounded nonempty list")
    parsed = tuple(_parse_row(item, start=start, end=end) for item in rows)
    identities = [(item["asset_id"], item["session_date"]) for item in parsed]
    if identities != sorted(identities):
        raise ValueError("Norgate rows must be ordered by asset_id then session_date")
    if len(identities) != len(set(identities)):
        raise ValueError("Norgate rows repeat an asset/date identity")
    symbol_by_asset: dict[int, tuple[str, str, str]] = {}
    assets_by_symbol: dict[str, set[int]] = {}
    observed_requested_symbols: set[str] = set()
    for row in parsed:
        identity = (row["requested_symbol"], row["symbol"], row["security_name"])
        prior = symbol_by_asset.setdefault(row["asset_id"], identity)
        if prior != identity:
            raise ValueError("one Norgate asset_id cannot change identity within an export")
        assets_by_symbol.setdefault(row["symbol"], set()).add(row["asset_id"])
        observed_requested_symbols.add(row["requested_symbol"])
    if observed_requested_symbols != set(requested_symbols):
        raise ValueError("Norgate export does not cover every requested symbol")
    expected_reused = sorted(
        symbol for symbol, asset_ids in assets_by_symbol.items() if len(asset_ids) > 1
    )
    if reused_symbols != expected_reused:
        raise ValueError("Norgate reused_symbols does not match row identities")
    return MappingProxyType(dict(root)), tuple(MappingProxyType(row) for row in parsed)


def parse_norgate_local_export(payload: bytes) -> tuple[Mapping[str, Any], ...]:
    """Parse exact provider-shaped bytes without granting admission authority."""

    _, rows = _parse_export(payload)
    return rows


@dataclass(frozen=True, slots=True)
class NorgateLocalExportSource:
    retrieved_at: str | datetime
    payload_bytes: bytes
    receipt_timestamp_basis: str = "CALLER_SUPPLIED_UNQUALIFIED"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.payload_bytes, bytes)
            or not self.payload_bytes
            or len(self.payload_bytes) > MAX_SOURCE_BYTES
        ):
            raise ValueError("payload_bytes must contain bounded nonempty bytes")
        object.__setattr__(
            self,
            "retrieved_at",
            _canonical_timestamp(self.retrieved_at, "retrieved_at"),
        )
        if self.receipt_timestamp_basis not in {
            "SYSTEM_CLOCK_AT_FILE_READ_UNQUALIFIED",
            "CALLER_SUPPLIED_UNQUALIFIED",
        }:
            raise ValueError("receipt_timestamp_basis is unsupported")


@dataclass(frozen=True, slots=True)
class NorgateLocalStagingBatch:
    decision_at: str
    retrieved_at: str
    observations_by_role: Mapping[str, tuple[Mapping[str, Any], ...]]
    source_payload_sha256: str
    staging_sha256: str
    quarantine_capture_bound: bool = False
    provider_payload_semantics_qualified: bool = False
    source_bytes_authenticated: bool = False
    historical_ticker_history_qualified: bool = False
    historical_availability_qualified: bool = False
    coverage_completeness_proven: bool = False
    observation_selection_validated: bool = False
    role_coverage_validated: bool = False
    engine_input_ready: bool = False
    performance_use_allowed: bool = False
    replay_executed: bool = False
    validation_accessed: bool = False
    test_accessed: bool = False
    broker_connection_allowed: bool = False
    orders_submitted: bool = False
    live_trading_enabled: bool = False
    _authority: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._authority is not _STAGING_AUTHORITY:
            raise PermissionError("NorgateLocalStagingBatch must be issued by the adapter")
        object.__setattr__(self, "_authority", None)
        if any(getattr(self, name) is not False for name in SAFETY_FLAG_NAMES):
            raise ValueError("Norgate staging safety flags were altered")

    def __reduce__(self) -> Any:
        raise TypeError("Norgate staging batches are deliberately not pickleable")

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": PROVIDER_ID,
            "provider_dataset_id": DATASET_ID,
            "roles": [BAR_ROLE, MEMBERSHIP_ROLE],
            "source_evidence_kinds": [
                BAR_ROLE,
                "CURRENT_VINTAGE_UNQUALIFIED_UNIVERSE_MEMBERSHIP",
            ],
            "membership_evidence_admitted": False,
            "decision_at": self.decision_at,
            "retrieved_at": self.retrieved_at,
            "record_counts": {
                role: len(self.observations_by_role.get(role, ()))
                for role in (BAR_ROLE, MEMBERSHIP_ROLE)
            },
            "source_payload_sha256": self.source_payload_sha256,
            "staging_sha256": self.staging_sha256,
            **{name: getattr(self, name) for name in SAFETY_FLAG_NAMES},
        }


class NorgateLocalExportAdapter:
    def normalize(
        self,
        *,
        source: NorgateLocalExportSource,
        decision_at: str | datetime,
    ) -> NorgateLocalStagingBatch:
        if not isinstance(source, NorgateLocalExportSource):
            raise ValueError("source must be a NorgateLocalExportSource")
        cutoff = _canonical_timestamp(decision_at, "decision_at")
        root, rows = _parse_export(source.payload_bytes)
        package_version = str(root["norgatedata_package_version"])
        exported_at = _canonical_timestamp(root.get("exported_at"), "exported_at")
        if exported_at > source.retrieved_at:
            raise ValueError("Norgate export cannot postdate local retrieval")
        retrieved = datetime.fromisoformat(str(source.retrieved_at))
        if any(date.fromisoformat(row["session_date"]) >= retrieved.date() for row in rows):
            raise ValueError("Norgate export must contain completed prior sessions only")
        source_hash = hashlib.sha256(source.payload_bytes).hexdigest()
        observations: dict[str, list[dict[str, Any]]] = {
            BAR_ROLE: [],
            MEMBERSHIP_ROLE: [],
        }
        for row in rows:
            identity = f"{row['asset_id']}:{row['session_date']}"
            session_effective_at = datetime.combine(
                date.fromisoformat(row["session_date"]), time.max, tzinfo=timezone.utc
            ).isoformat(timespec="microseconds")
            common = {
                "asset_id": row["asset_id"],
                "permanent_security_id": f"NORGATE-{row['asset_id']}",
                "requested_symbol": row["requested_symbol"],
                "symbol": row["symbol"],
                "security_name": row["security_name"],
                "norgatedata_package_version": package_version,
                "database_name": root["database_name"],
                "database_update_at": _canonical_timestamp(
                    root["database_update_at"], "database_update_at"
                ),
                "universe_selection_basis": root["universe_selection_basis"],
                "requested_symbols_sha256": root["requested_symbols_sha256"],
                "symbol_reused_within_export": row["symbol"] in root["reused_symbols"],
                "license_restricted_provider_data": True,
                "source_code_repository_storage_allowed": False,
                "session_date": row["session_date"],
                "effective_at_basis": "SESSION_DATE_END_UTC_UNQUALIFIED",
                "historical_availability_basis": (
                    "CURRENT_LOCAL_RECEIPT_ONLY_UNQUALIFIED"
                ),
                "receipt_timestamp_basis": source.receipt_timestamp_basis,
                "provider_vintage_basis": (
                    "CURRENT_LOCAL_EXPORT_DATABASE_VINTAGE_UNQUALIFIED"
                ),
                "ticker_history_basis": "STATIC_EXPORT_SYMBOL_UNQUALIFIED",
            }
            bar_payload = {
                **common,
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "unadjusted_close": row["unadjusted_close"],
                "dividend_on_session": row["dividend"],
                "dividend_effective_basis": "SESSION_EX_DATE_UNQUALIFIED",
                "stock_price_adjustment": "NONE",
                "padding": "NONE",
            }
            membership_payload = {
                **common,
                "source_evidence_kind": (
                    "CURRENT_VINTAGE_UNQUALIFIED_UNIVERSE_MEMBERSHIP"
                ),
                "supplemental_evidence_only": True,
                "index_name": INDEX_NAME,
                "is_constituent": row["sp500_constituent"],
                "padding": "NONE",
            }
            for role, role_payload in (
                (BAR_ROLE, bar_payload),
                (MEMBERSHIP_ROLE, membership_payload),
            ):
                observations[role].append(
                    {
                        "schema_version": OBSERVATION_SCHEMA_VERSION,
                        "role": role,
                        "provider_id": PROVIDER_ID,
                        "provider_dataset_id": DATASET_ID,
                        "provider_record_id": (
                            f"{DATASET_ID}:{role}:{row['asset_id']:020d}:"
                            f"{row['session_date']}"
                        ),
                        "effective_at": session_effective_at,
                        "available_at": source.retrieved_at,
                        "retrieved_at": source.retrieved_at,
                        "observation_cutoff_at": cutoff,
                        "source_payload_sha256": source_hash,
                        "normalized_payload_sha256": normalized_payload_sha256(
                            role_payload
                        ),
                        "payload": role_payload,
                    }
                )
        validated: dict[str, tuple[Mapping[str, Any], ...]] = {}
        for role in (BAR_ROLE, MEMBERSHIP_ROLE):
            observations[role].sort(key=lambda item: item["provider_record_id"])
            validated[role] = validate_historical_role_cutoff_observations(
                role=role,
                decision_at=cutoff,
                observations=observations[role],
            )
        staging_material = {
            "provider_id": PROVIDER_ID,
            "provider_dataset_id": DATASET_ID,
            "decision_at": cutoff,
            "retrieved_at": source.retrieved_at,
            "source_payload_sha256": source_hash,
            "observation_hashes_by_role": {
                role: [item["normalized_payload_sha256"] for item in validated[role]]
                for role in (BAR_ROLE, MEMBERSHIP_ROLE)
            },
            "safety_flags": {name: False for name in SAFETY_FLAG_NAMES},
        }
        staging_hash = hashlib.sha256(
            json.dumps(
                staging_material, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        return NorgateLocalStagingBatch(
            decision_at=cutoff,
            retrieved_at=str(source.retrieved_at),
            observations_by_role=MappingProxyType(validated),
            source_payload_sha256=source_hash,
            staging_sha256=staging_hash,
            _authority=_STAGING_AUTHORITY,
        )
