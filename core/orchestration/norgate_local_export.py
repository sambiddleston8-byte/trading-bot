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
CAPTURE_EXPORT_CONTRACT = "NORGATE_LOCAL_EXPORT_V2"
UNIVERSE_CATALOG_CONTRACT = "NORGATE_LOCAL_UNIVERSE_CATALOG_V1"
BAR_ROLE = "RAW_DAILY_SESSION_BARS"
MEMBERSHIP_ROLE = "POINT_IN_TIME_SUPPLEMENTAL_PROVIDER_EVIDENCE"
INDEX_NAME = "S&P 500"
UNIVERSE_WATCHLIST_NAME = "S&P 500 Current & Past"
UNIVERSE_DATABASE_NAME = "US Equities"
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 200_000
MAX_UNIVERSE_CATALOG_BYTES = 8 * 1024 * 1024
MAX_UNIVERSE_CATALOG_ENTRIES = 5_000
MAX_CAPTURE_SHARDS = 100
MAX_CAPTURE_SYMBOLS = 10_000
MAX_CAPTURE_BYTES = 256 * 1024 * 1024
MAX_CAPTURE_RECORDS = 500_000
_STAGING_AUTHORITY = object()
_DETERMINISM_AUTHORITY = object()
_CAPTURE_MANIFEST_AUTHORITY = object()
_UNIVERSE_CATALOG_AUTHORITY = object()
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
_CAPTURE_TOP_LEVEL_FIELDS = _TOP_LEVEL_FIELDS | {
    "asset_dispositions",
    "asset_dispositions_sha256",
}
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
_ASSET_DISPOSITION_FIELDS = frozenset(
    {
        "asset_id",
        "requested_symbol",
        "symbol",
        "security_name",
        "status",
        "row_count",
    }
)
CAPTURE_DISPOSITION_STATUSES = frozenset(
    {"ROWS_PRESENT", "NO_ROWS_IN_REQUESTED_WINDOW"}
)
_UNIVERSE_CATALOG_FIELDS = frozenset(
    {
        "schema_version",
        "export_contract",
        "provider_id",
        "provider_dataset_id",
        "norgatedata_package_version",
        "database_name",
        "database_update_at",
        "watchlist_name",
        "watchlist_semantics_basis",
        "exported_at",
        "license_restricted_provider_data",
        "source_code_repository_storage_allowed",
        "entry_count",
        "entries_sha256",
        "reused_symbols",
        "entries",
    }
)
_UNIVERSE_ENTRY_FIELDS = frozenset({"asset_id", "symbol", "security_name"})
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
UNIVERSE_CATALOG_SAFETY_FLAG_NAMES = SAFETY_FLAG_NAMES + (
    "provider_watchlist_semantics_qualified",
    "provider_watchlist_completeness_proven",
    "security_master_admission_allowed",
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


def _parse_asset_disposition(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _ASSET_DISPOSITION_FIELDS:
        raise ValueError("Norgate asset disposition fields are unsupported")
    asset_id = value.get("asset_id")
    if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id <= 0:
        raise ValueError("asset disposition asset_id must be a positive integer")
    requested_symbol = _text(
        value.get("requested_symbol"), "disposition requested_symbol", maximum=32
    )
    symbol = _text(value.get("symbol"), "disposition symbol", maximum=32)
    if (
        _SYMBOL_PATTERN.fullmatch(requested_symbol) is None
        or _SYMBOL_PATTERN.fullmatch(symbol) is None
    ):
        raise ValueError("asset disposition symbols must be canonical")
    security_name = _text(
        value.get("security_name"), "disposition security_name", maximum=300
    )
    status = value.get("status")
    if status not in CAPTURE_DISPOSITION_STATUSES:
        raise ValueError("asset disposition status is unsupported")
    row_count = value.get("row_count")
    if (
        isinstance(row_count, bool)
        or not isinstance(row_count, int)
        or row_count < 0
        or (status == "ROWS_PRESENT" and row_count == 0)
        or (status == "NO_ROWS_IN_REQUESTED_WINDOW" and row_count != 0)
    ):
        raise ValueError("asset disposition row_count contradicts its status")
    return {
        "asset_id": asset_id,
        "requested_symbol": requested_symbol,
        "symbol": symbol,
        "security_name": security_name,
        "status": status,
        "row_count": row_count,
    }


def _parse_export(
    payload: bytes,
) -> tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...]]:
    root = _strict_json(payload)
    capture_v2 = (
        isinstance(root, Mapping)
        and root.get("export_contract") == CAPTURE_EXPORT_CONTRACT
    )
    required_fields = _CAPTURE_TOP_LEVEL_FIELDS if capture_v2 else _TOP_LEVEL_FIELDS
    if not isinstance(root, Mapping) or set(root) != required_fields:
        raise ValueError("Norgate export has missing or unsupported top-level fields")
    expected = {
        "schema_version": "2.0" if capture_v2 else "1.0",
        "export_contract": (
            CAPTURE_EXPORT_CONTRACT if capture_v2 else EXPORT_CONTRACT
        ),
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
    if not isinstance(rows, list) or len(rows) > MAX_RECORDS:
        raise ValueError("Norgate rows must be a bounded list")
    if not capture_v2 and not rows:
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
    dispositions: tuple[dict[str, Any], ...] = ()
    if capture_v2:
        disposition_values = root.get("asset_dispositions")
        if (
            not isinstance(disposition_values, list)
            or not disposition_values
            or len(disposition_values) != len(requested_symbols)
        ):
            raise ValueError(
                "Norgate asset dispositions must cover every requested symbol"
            )
        dispositions = tuple(
            _parse_asset_disposition(value) for value in disposition_values
        )
        asset_ids = tuple(item["asset_id"] for item in dispositions)
        disposition_requested = tuple(
            item["requested_symbol"] for item in dispositions
        )
        if (
            asset_ids != tuple(sorted(asset_ids))
            or len(asset_ids) != len(set(asset_ids))
            or disposition_requested != tuple(requested_symbols)
        ):
            raise ValueError(
                "Norgate asset dispositions must be unique asset-ID-ordered requests"
            )
        canonical_dispositions = [dict(item) for item in dispositions]
        expected_dispositions_sha256 = hashlib.sha256(
            json.dumps(
                canonical_dispositions,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        if root.get("asset_dispositions_sha256") != expected_dispositions_sha256:
            raise ValueError("Norgate asset disposition hash does not match")
        disposition_by_requested = {
            item["requested_symbol"]: item for item in dispositions
        }
        rows_by_requested: dict[str, list[Mapping[str, Any]]] = {}
        for row in parsed:
            rows_by_requested.setdefault(row["requested_symbol"], []).append(row)
        for requested_symbol, disposition in disposition_by_requested.items():
            disposition_rows = rows_by_requested.get(requested_symbol, [])
            if len(disposition_rows) != disposition["row_count"]:
                raise ValueError("Norgate asset disposition row_count does not match rows")
            if any(
                (
                    row["asset_id"],
                    row["symbol"],
                    row["security_name"],
                )
                != (
                    disposition["asset_id"],
                    disposition["symbol"],
                    disposition["security_name"],
                )
                for row in disposition_rows
            ):
                raise ValueError("Norgate rows do not match their asset disposition")
        expected_observed = {
            item["requested_symbol"]
            for item in dispositions
            if item["status"] == "ROWS_PRESENT"
        }
        if observed_requested_symbols != expected_observed:
            raise ValueError("Norgate rows contradict asset disposition statuses")
    elif observed_requested_symbols != set(requested_symbols):
        raise ValueError("Norgate export does not cover every requested symbol")
    if capture_v2:
        assets_by_symbol = {}
        for disposition in dispositions:
            assets_by_symbol.setdefault(disposition["symbol"], set()).add(
                disposition["asset_id"]
            )
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


def _parse_universe_catalog(
    payload: bytes,
) -> tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...]]:
    if (
        not isinstance(payload, bytes)
        or not payload
        or len(payload) > MAX_UNIVERSE_CATALOG_BYTES
    ):
        raise ValueError("Norgate universe catalog must contain bounded nonempty bytes")
    root = _strict_json(payload)
    if not isinstance(root, Mapping) or set(root) != _UNIVERSE_CATALOG_FIELDS:
        raise ValueError(
            "Norgate universe catalog has missing or unsupported top-level fields"
        )
    expected = {
        "schema_version": "1.0",
        "export_contract": UNIVERSE_CATALOG_CONTRACT,
        "provider_id": PROVIDER_ID,
        "provider_dataset_id": DATASET_ID,
        "database_name": UNIVERSE_DATABASE_NAME,
        "watchlist_name": UNIVERSE_WATCHLIST_NAME,
        "watchlist_semantics_basis": (
            "PROVIDER_NAMED_CURRENT_AND_PAST_WATCHLIST_UNQUALIFIED"
        ),
    }
    for name, required in expected.items():
        if root.get(name) != required:
            raise ValueError(f"Norgate universe catalog {name} is unsupported")
    package_version = _text(
        root.get("norgatedata_package_version"),
        "norgatedata_package_version",
        maximum=30,
    )
    if _VERSION_PATTERN.fullmatch(package_version) is None:
        raise ValueError("Norgate package version must be canonical")
    database_update_at = _canonical_timestamp(
        root.get("database_update_at"), "database_update_at"
    )
    exported_at = _canonical_timestamp(root.get("exported_at"), "exported_at")
    if database_update_at > exported_at:
        raise ValueError("Norgate database update cannot postdate catalog export")
    if root.get("license_restricted_provider_data") is not True:
        raise ValueError("Norgate provider data must remain license restricted")
    if root.get("source_code_repository_storage_allowed") is not False:
        raise ValueError("Norgate universe entries cannot be stored in source control")
    entries = root.get("entries")
    entry_count = root.get("entry_count")
    if (
        not isinstance(entries, list)
        or not entries
        or len(entries) > MAX_UNIVERSE_CATALOG_ENTRIES
        or isinstance(entry_count, bool)
        or not isinstance(entry_count, int)
        or entry_count != len(entries)
    ):
        raise ValueError("Norgate universe entries must be a bounded counted list")
    parsed: list[Mapping[str, Any]] = []
    for value in entries:
        if not isinstance(value, Mapping) or set(value) != _UNIVERSE_ENTRY_FIELDS:
            raise ValueError("Norgate universe entry fields are unsupported")
        asset_id = value.get("asset_id")
        if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id <= 0:
            raise ValueError("universe asset_id must be a positive stable identifier")
        symbol = _text(value.get("symbol"), "universe symbol", maximum=32)
        if _SYMBOL_PATTERN.fullmatch(symbol) is None:
            raise ValueError("universe symbol must be a canonical U.S. stock symbol")
        security_name = _text(
            value.get("security_name"), "universe security_name", maximum=300
        )
        parsed.append(
            MappingProxyType(
                {
                    "asset_id": asset_id,
                    "symbol": symbol,
                    "security_name": security_name,
                }
            )
        )
    asset_ids = [entry["asset_id"] for entry in parsed]
    if asset_ids != sorted(asset_ids) or len(asset_ids) != len(set(asset_ids)):
        raise ValueError("Norgate universe entries must have ordered unique asset IDs")
    assets_by_symbol: dict[str, set[int]] = {}
    for entry in parsed:
        assets_by_symbol.setdefault(entry["symbol"], set()).add(entry["asset_id"])
    expected_reused = sorted(
        symbol for symbol, values in assets_by_symbol.items() if len(values) > 1
    )
    if root.get("reused_symbols") != expected_reused:
        raise ValueError("Norgate universe reused_symbols does not match entries")
    canonical_entries = [dict(entry) for entry in parsed]
    entries_sha256 = hashlib.sha256(
        json.dumps(
            canonical_entries,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if root.get("entries_sha256") != entries_sha256:
        raise ValueError("Norgate universe entry hash does not match exact entries")
    return MappingProxyType(dict(root)), tuple(parsed)


def parse_norgate_local_universe_catalog(
    payload: bytes,
) -> tuple[Mapping[str, Any], ...]:
    """Parse a provider-named identity catalog without qualifying its semantics."""

    _, entries = _parse_universe_catalog(payload)
    return entries


@dataclass(frozen=True, slots=True)
class NorgateSameVintageDeterminismCheck:
    baseline_source_payload_sha256: str
    repeat_source_payload_sha256: str
    invariant_payload_sha256: str
    baseline_exported_at: str
    repeat_exported_at: str
    database_update_at: str
    requested_start: str
    requested_end: str
    requested_symbols: tuple[str, ...]
    same_vintage_invariant_match: bool = True
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
        if self._authority is not _DETERMINISM_AUTHORITY:
            raise PermissionError(
                "NorgateSameVintageDeterminismCheck must be issued by the comparator"
            )
        object.__setattr__(self, "_authority", None)
        if self.same_vintage_invariant_match is not True:
            raise ValueError("same-vintage invariant content must match")
        if any(getattr(self, name) is not False for name in SAFETY_FLAG_NAMES):
            raise ValueError("Norgate determinism safety flags were altered")

    def __reduce__(self) -> Any:
        raise TypeError("Norgate determinism checks are deliberately not pickleable")

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": PROVIDER_ID,
            "provider_dataset_id": DATASET_ID,
            "comparison_scope": (
                "SAME_DATABASE_VINTAGE_EXCLUDING_EXPORTED_AT_ONLY"
            ),
            "baseline_source_payload_sha256": self.baseline_source_payload_sha256,
            "repeat_source_payload_sha256": self.repeat_source_payload_sha256,
            "invariant_payload_sha256": self.invariant_payload_sha256,
            "baseline_exported_at": self.baseline_exported_at,
            "repeat_exported_at": self.repeat_exported_at,
            "database_update_at": self.database_update_at,
            "requested_start": self.requested_start,
            "requested_end": self.requested_end,
            "requested_symbols": list(self.requested_symbols),
            "same_vintage_invariant_match": self.same_vintage_invariant_match,
            **{name: getattr(self, name) for name in SAFETY_FLAG_NAMES},
        }


def _same_vintage_roots(
    baseline_payload: bytes,
    repeat_payload: bytes,
) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:

    for name, payload in (
        ("baseline_payload", baseline_payload),
        ("repeat_payload", repeat_payload),
    ):
        if (
            not isinstance(payload, bytes)
            or not payload
            or len(payload) > MAX_SOURCE_BYTES
        ):
            raise ValueError(f"{name} must contain bounded nonempty bytes")
    baseline_root, _ = _parse_export(baseline_payload)
    repeat_root, _ = _parse_export(repeat_payload)
    for name, root in (("baseline", baseline_root), ("repeat", repeat_root)):
        if frozenset(root) not in {_TOP_LEVEL_FIELDS, _CAPTURE_TOP_LEVEL_FIELDS}:
            raise ValueError(f"{name} export root fields are not exactly pinned")
    if frozenset(baseline_root) != frozenset(repeat_root):
        raise ValueError("same-vintage exports mix contract versions")
    baseline_exported_at = _canonical_timestamp(
        baseline_root["exported_at"], "baseline exported_at"
    )
    return baseline_root, repeat_root, baseline_exported_at


@dataclass(frozen=True, slots=True)
class NorgateShardedCaptureManifest:
    manifest_sha256: str
    catalog_source_payload_sha256: str
    catalog_entries_sha256: str
    catalog_evidence_sha256: str
    catalog_entry_count: int
    catalog_exported_at: str
    catalog_retrieved_at: str
    catalog_receipt_timestamp_basis: str
    database_name: str
    database_update_at: str
    norgatedata_package_version: str
    requested_start: str
    requested_end: str
    requested_symbols: tuple[str, ...] = field(repr=False)
    requested_symbols_sha256: str
    shard_source_payload_sha256: tuple[str, ...]
    shard_requested_symbols_sha256: tuple[str, ...]
    shard_asset_dispositions_sha256: tuple[str, ...]
    shard_exported_at: tuple[str, ...]
    shard_symbol_counts: tuple[int, ...]
    shard_captured_asset_counts: tuple[int, ...]
    shard_zero_row_asset_counts: tuple[int, ...]
    shard_row_counts: tuple[int, ...]
    aggregate_reused_symbols: tuple[str, ...] = field(repr=False)
    asset_count: int
    captured_asset_count: int
    zero_row_asset_count: int
    row_count: int
    license_restricted_provider_data: bool = True
    source_code_repository_storage_allowed: bool = False
    same_vintage_shard_contract_match: bool = True
    requested_symbol_partition_match: bool = True
    cross_shard_row_identity_unique: bool = True
    catalog_vintage_match: bool = True
    catalog_asset_identity_match: bool = True
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
        if self._authority is not _CAPTURE_MANIFEST_AUTHORITY:
            raise PermissionError(
                "NorgateShardedCaptureManifest must be issued by the assembler"
            )
        object.__setattr__(self, "_authority", None)
        hashes = (
            self.manifest_sha256,
            self.catalog_source_payload_sha256,
            self.catalog_entries_sha256,
            self.catalog_evidence_sha256,
            self.requested_symbols_sha256,
            *self.shard_source_payload_sha256,
            *self.shard_requested_symbols_sha256,
            *self.shard_asset_dispositions_sha256,
        )
        if any(
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in hashes
        ):
            raise ValueError("Norgate sharded-capture hashes are not canonical")
        timestamp_fields = (
            (self.catalog_exported_at, "catalog_exported_at"),
            (self.catalog_retrieved_at, "catalog_retrieved_at"),
            (self.database_update_at, "database_update_at"),
            *((value, "shard_exported_at") for value in self.shard_exported_at),
        )
        if any(
            value != _canonical_timestamp(value, name)
            for value, name in timestamp_fields
        ):
            raise ValueError("Norgate sharded-capture timestamps are not canonical")
        if self.catalog_receipt_timestamp_basis not in {
            "SYSTEM_CLOCK_AT_FILE_READ_UNQUALIFIED",
            "CALLER_SUPPLIED_UNQUALIFIED",
        }:
            raise ValueError("Norgate sharded-capture receipt basis is unsupported")
        assertions = (
            self.same_vintage_shard_contract_match,
            self.requested_symbol_partition_match,
            self.cross_shard_row_identity_unique,
            self.catalog_vintage_match,
            self.catalog_asset_identity_match,
        )
        if any(value is not True for value in assertions):
            raise ValueError("Norgate sharded-capture assertions were altered")
        if (
            self.license_restricted_provider_data is not True
            or self.source_code_repository_storage_allowed is not False
        ):
            raise ValueError("Norgate sharded-capture license markings were altered")
        if (
            self.catalog_entry_count != len(self.requested_symbols)
            or self.catalog_entry_count != self.asset_count
            or self.captured_asset_count + self.zero_row_asset_count
            != self.asset_count
            or sum(self.shard_captured_asset_counts) != self.captured_asset_count
            or sum(self.shard_zero_row_asset_counts) != self.zero_row_asset_count
            or sum(self.shard_symbol_counts) != self.asset_count
            or sum(self.shard_row_counts) != self.row_count
            or any(
                captured + zero != symbols
                for captured, zero, symbols in zip(
                    self.shard_captured_asset_counts,
                    self.shard_zero_row_asset_counts,
                    self.shard_symbol_counts,
                    strict=True,
                )
            )
            or self.aggregate_reused_symbols
        ):
            raise ValueError("Norgate catalog-bound capture evidence is inconsistent")
        shard_count = len(self.shard_source_payload_sha256)
        if not 2 <= shard_count <= MAX_CAPTURE_SHARDS or any(
            len(values) != shard_count
            for values in (
                self.shard_requested_symbols_sha256,
                self.shard_asset_dispositions_sha256,
                self.shard_exported_at,
                self.shard_symbol_counts,
                self.shard_captured_asset_counts,
                self.shard_zero_row_asset_counts,
                self.shard_row_counts,
            )
        ):
            raise ValueError("Norgate sharded-capture evidence is inconsistent")
        if any(getattr(self, name) is not False for name in SAFETY_FLAG_NAMES):
            raise ValueError("Norgate sharded-capture safety flags were altered")

    def __reduce__(self) -> Any:
        raise TypeError(
            "Norgate sharded-capture manifests are deliberately not pickleable"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": PROVIDER_ID,
            "provider_dataset_id": DATASET_ID,
            "manifest_scope": (
                "CATALOG_BOUND_SAME_VINTAGE_SHARDED_QUARANTINE_CAPTURE_ONLY"
            ),
            "manifest_sha256": self.manifest_sha256,
            "catalog_source_payload_sha256": self.catalog_source_payload_sha256,
            "catalog_entries_sha256": self.catalog_entries_sha256,
            "catalog_evidence_sha256": self.catalog_evidence_sha256,
            "catalog_entry_count": self.catalog_entry_count,
            "catalog_exported_at": self.catalog_exported_at,
            "catalog_retrieved_at": self.catalog_retrieved_at,
            "catalog_receipt_timestamp_basis": (
                self.catalog_receipt_timestamp_basis
            ),
            "database_name": self.database_name,
            "database_update_at": self.database_update_at,
            "norgatedata_package_version": self.norgatedata_package_version,
            "requested_start": self.requested_start,
            "requested_end": self.requested_end,
            "requested_symbol_count": len(self.requested_symbols),
            "requested_symbols_sha256": self.requested_symbols_sha256,
            "shards": [
                {
                    "ordinal": ordinal,
                    "source_payload_sha256": self.shard_source_payload_sha256[
                        ordinal
                    ],
                    "requested_symbols_sha256": (
                        self.shard_requested_symbols_sha256[ordinal]
                    ),
                    "asset_dispositions_sha256": (
                        self.shard_asset_dispositions_sha256[ordinal]
                    ),
                    "exported_at": self.shard_exported_at[ordinal],
                    "symbol_count": self.shard_symbol_counts[ordinal],
                    "captured_asset_count": self.shard_captured_asset_counts[
                        ordinal
                    ],
                    "zero_row_asset_count": self.shard_zero_row_asset_counts[
                        ordinal
                    ],
                    "row_count": self.shard_row_counts[ordinal],
                }
                for ordinal in range(len(self.shard_source_payload_sha256))
            ],
            "asset_count": self.asset_count,
            "captured_asset_count": self.captured_asset_count,
            "zero_row_asset_count": self.zero_row_asset_count,
            "row_count": self.row_count,
            "license_restricted_provider_data": (
                self.license_restricted_provider_data
            ),
            "source_code_repository_storage_allowed": (
                self.source_code_repository_storage_allowed
            ),
            "same_vintage_shard_contract_match": (
                self.same_vintage_shard_contract_match
            ),
            "requested_symbol_partition_match": (
                self.requested_symbol_partition_match
            ),
            "cross_shard_row_identity_unique": (
                self.cross_shard_row_identity_unique
            ),
            "catalog_vintage_match": self.catalog_vintage_match,
            "catalog_asset_identity_match": self.catalog_asset_identity_match,
            **{name: getattr(self, name) for name in SAFETY_FLAG_NAMES},
        }


def assemble_norgate_sharded_capture_manifest(
    payloads: Sequence[bytes],
    *,
    catalog_evidence: NorgateLocalUniverseCatalogEvidence,
) -> NorgateShardedCaptureManifest:
    """Bind shards to one exact unqualified catalog without admitting semantics."""

    if not isinstance(payloads, Sequence) or isinstance(
        payloads, (bytes, bytearray, str)
    ):
        raise ValueError("payloads must be a bounded sequence of export bytes")
    if not 2 <= len(payloads) <= MAX_CAPTURE_SHARDS:
        raise ValueError("payloads must contain between 2 and 100 export shards")
    shards = tuple(payloads)
    if not isinstance(catalog_evidence, NorgateLocalUniverseCatalogEvidence):
        raise ValueError("catalog_evidence must be staged catalog evidence")
    catalog_entries = catalog_evidence.entries
    catalog_asset_ids = tuple(int(entry["asset_id"]) for entry in catalog_entries)
    if catalog_asset_ids != tuple(sorted(catalog_asset_ids)):
        raise ValueError("staged catalog entries are not ordered by stable asset ID")
    expected = tuple(str(entry["symbol"]) for entry in catalog_entries)
    if (
        not expected
        or len(expected) > MAX_CAPTURE_SYMBOLS
        or len(expected) != len(set(expected))
        or catalog_evidence.reused_symbols
        or any(
            not isinstance(symbol, str) or _SYMBOL_PATTERN.fullmatch(symbol) is None
            for symbol in expected
        )
    ):
        raise ValueError("catalog symbols must be bounded, canonical, and unambiguous")
    catalog_identity_by_asset = {
        int(entry["asset_id"]): (str(entry["symbol"]), str(entry["security_name"]))
        for entry in catalog_entries
    }
    if len(catalog_identity_by_asset) != len(catalog_entries):
        raise ValueError("catalog repeats a stable asset identity")

    shard_variant_fields = {
        "exported_at",
        "requested_symbols",
        "requested_symbols_sha256",
        "asset_dispositions",
        "asset_dispositions_sha256",
        "reused_symbols",
        "rows",
    }
    invariant_fields: frozenset[str] = frozenset()
    capture_root_fields: frozenset[str] | None = None
    total_bytes = 0
    total_records = 0
    baseline_root: dict[str, Any] | None = None
    source_hashes: list[str] = []
    seen_source_hashes: set[str] = set()
    flattened_symbols: list[str] = []
    shard_requested_hashes: list[str] = []
    shard_disposition_hashes: list[str] = []
    shard_exported_at: list[str] = []
    shard_symbol_counts: list[int] = []
    shard_captured_asset_counts: list[int] = []
    shard_zero_row_asset_counts: list[int] = []
    shard_row_counts: list[int] = []
    row_identities: set[tuple[int, str]] = set()
    identity_by_asset: dict[int, tuple[str, str, str]] = {}
    disposition_identity_by_asset: dict[int, tuple[str, str, str]] = {}
    assets_by_resolved_symbol: dict[str, set[int]] = {}
    for ordinal, payload in enumerate(shards):
        if (
            not isinstance(payload, bytes)
            or not payload
            or len(payload) > MAX_SOURCE_BYTES
        ):
            raise ValueError(f"payloads[{ordinal}] must contain bounded nonempty bytes")
        total_bytes += len(payload)
        if total_bytes > MAX_CAPTURE_BYTES:
            raise ValueError("sharded capture exceeds the aggregate byte boundary")
        source_hash = hashlib.sha256(payload).hexdigest()
        if source_hash in seen_source_hashes:
            raise ValueError("sharded capture repeats exact source payload bytes")
        seen_source_hashes.add(source_hash)
        source_hashes.append(source_hash)
        root, rows = _parse_export(payload)
        root_fields = frozenset(root)
        if root_fields not in {_TOP_LEVEL_FIELDS, _CAPTURE_TOP_LEVEL_FIELDS}:
            raise ValueError(f"payloads[{ordinal}] root fields are not exactly pinned")
        if capture_root_fields is None:
            capture_root_fields = root_fields
            invariant_fields = capture_root_fields - shard_variant_fields
        elif root_fields != capture_root_fields:
            raise ValueError("sharded capture mixes export contract versions")
        lightweight_root = {name: root[name] for name in root_fields - {"rows"}}
        if baseline_root is None:
            baseline_root = lightweight_root
        else:
            changed = sorted(
                name
                for name in invariant_fields
                if root[name] != baseline_root[name]
            )
            if changed:
                raise ValueError(
                    f"payloads[{ordinal}] does not share the capture contract: "
                    + ", ".join(changed)
                )
        requested = tuple(root["requested_symbols"])
        requested_hash = hashlib.sha256(
            json.dumps(requested, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if requested_hash != root["requested_symbols_sha256"]:
            raise ValueError(
                f"payloads[{ordinal}] requested symbol hash is not self-consistent"
            )
        flattened_symbols.extend(requested)
        shard_requested_hashes.append(requested_hash)
        shard_exported_at.append(
            _canonical_timestamp(root["exported_at"], "exported_at")
        )
        shard_symbol_counts.append(len(requested))
        shard_row_counts.append(len(rows))
        if root_fields == _CAPTURE_TOP_LEVEL_FIELDS:
            dispositions = tuple(root["asset_dispositions"])
            disposition_hash = str(root["asset_dispositions_sha256"])
        else:
            rows_by_asset: dict[int, list[Mapping[str, Any]]] = {}
            for row in rows:
                rows_by_asset.setdefault(row["asset_id"], []).append(row)
            dispositions = tuple(
                {
                    "asset_id": asset_rows[0]["asset_id"],
                    "requested_symbol": asset_rows[0]["requested_symbol"],
                    "symbol": asset_rows[0]["symbol"],
                    "security_name": asset_rows[0]["security_name"],
                    "status": "ROWS_PRESENT",
                    "row_count": len(asset_rows),
                }
                for _, asset_rows in sorted(rows_by_asset.items())
            )
            disposition_hash = hashlib.sha256(
                json.dumps(
                    dispositions,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
        shard_disposition_hashes.append(disposition_hash)
        captured_in_shard = sum(
            item["status"] == "ROWS_PRESENT" for item in dispositions
        )
        zero_row_in_shard = sum(
            item["status"] == "NO_ROWS_IN_REQUESTED_WINDOW"
            for item in dispositions
        )
        shard_captured_asset_counts.append(captured_in_shard)
        shard_zero_row_asset_counts.append(zero_row_in_shard)
        for disposition in dispositions:
            disposition_identity = (
                disposition["requested_symbol"],
                disposition["symbol"],
                disposition["security_name"],
            )
            prior_disposition = disposition_identity_by_asset.setdefault(
                disposition["asset_id"], disposition_identity
            )
            if prior_disposition != disposition_identity:
                raise ValueError("sharded capture changes an asset disposition identity")
            catalog_identity = catalog_identity_by_asset.get(disposition["asset_id"])
            if catalog_identity != (
                disposition["symbol"],
                disposition["security_name"],
            ):
                raise ValueError(
                    f"payloads[{ordinal}] disposition does not match the pinned catalog identity"
                )
            if disposition["requested_symbol"] != catalog_identity[0]:
                raise ValueError(
                    f"payloads[{ordinal}] disposition request does not match the catalog"
                )
        total_records += len(rows)
        if total_records > MAX_CAPTURE_RECORDS:
            raise ValueError("sharded capture exceeds the aggregate record boundary")
        for row in rows:
            row_identity = (row["asset_id"], row["session_date"])
            if row_identity in row_identities:
                raise ValueError(
                    "sharded capture repeats an asset/date identity across shards"
                )
            row_identities.add(row_identity)
            identity = (
                row["requested_symbol"],
                row["symbol"],
                row["security_name"],
            )
            prior = identity_by_asset.setdefault(row["asset_id"], identity)
            if prior != identity:
                raise ValueError(
                    f"payloads[{ordinal}] changes a Norgate asset identity across shards"
                )
            catalog_identity = catalog_identity_by_asset.get(row["asset_id"])
            if catalog_identity != (row["symbol"], row["security_name"]):
                raise ValueError(
                    f"payloads[{ordinal}] does not match the pinned catalog identity"
                )
            if row["requested_symbol"] != catalog_identity[0]:
                raise ValueError(
                    f"payloads[{ordinal}] requested symbol does not match the catalog"
                )
            assets_by_resolved_symbol.setdefault(row["symbol"], set()).add(
                row["asset_id"]
            )
        del root, rows

    if baseline_root is None:
        raise ValueError("sharded capture did not produce a baseline contract")
    flattened = tuple(flattened_symbols)
    if len(flattened) != len(set(flattened)):
        raise ValueError("sharded capture repeats a requested symbol across shards")
    if flattened != expected:
        raise ValueError(
            "sharded capture does not exactly match the catalog symbol partition"
        )
    if set(disposition_identity_by_asset) != set(catalog_identity_by_asset):
        raise ValueError("sharded capture does not dispose every catalog asset identity")
    catalog_vintage = (
        catalog_evidence.database_name,
        catalog_evidence.database_update_at,
        catalog_evidence.norgatedata_package_version,
    )
    shard_vintage = (
        baseline_root["database_name"],
        _canonical_timestamp(
            baseline_root["database_update_at"], "database_update_at"
        ),
        baseline_root["norgatedata_package_version"],
    )
    if shard_vintage != catalog_vintage:
        raise ValueError("sharded capture does not match the pinned catalog vintage")

    aggregate_reused_symbols = tuple(
        sorted(
            symbol
            for symbol, asset_ids in assets_by_resolved_symbol.items()
            if len(asset_ids) > 1
        )
    )
    requested_symbols_sha256 = hashlib.sha256(
        json.dumps(expected, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest_material = {
        "provider_id": PROVIDER_ID,
        "provider_dataset_id": DATASET_ID,
        "manifest_scope": (
            "CATALOG_BOUND_SAME_VINTAGE_SHARDED_QUARANTINE_CAPTURE_ONLY"
        ),
        "catalog_source_payload_sha256": catalog_evidence.source_payload_sha256,
        "catalog_entries_sha256": catalog_evidence.entries_sha256,
        "catalog_evidence_sha256": catalog_evidence.catalog_evidence_sha256,
        "catalog_entry_count": len(catalog_entries),
        "catalog_exported_at": catalog_evidence.exported_at,
        "catalog_retrieved_at": catalog_evidence.retrieved_at,
        "catalog_receipt_timestamp_basis": (
            catalog_evidence.receipt_timestamp_basis
        ),
        "database_name": baseline_root["database_name"],
        "database_update_at": _canonical_timestamp(
            baseline_root["database_update_at"], "database_update_at"
        ),
        "norgatedata_package_version": baseline_root[
            "norgatedata_package_version"
        ],
        "requested_start": baseline_root["requested_start"],
        "requested_end": baseline_root["requested_end"],
        "requested_symbols": list(expected),
        "requested_symbols_sha256": requested_symbols_sha256,
        "shard_source_payload_sha256": source_hashes,
        "shard_requested_symbols_sha256": list(shard_requested_hashes),
        "shard_asset_dispositions_sha256": list(shard_disposition_hashes),
        "shard_exported_at": list(shard_exported_at),
        "shard_symbol_counts": list(shard_symbol_counts),
        "shard_captured_asset_counts": list(shard_captured_asset_counts),
        "shard_zero_row_asset_counts": list(shard_zero_row_asset_counts),
        "shard_row_counts": list(shard_row_counts),
        "aggregate_reused_symbols": list(aggregate_reused_symbols),
        "asset_count": len(disposition_identity_by_asset),
        "captured_asset_count": sum(shard_captured_asset_counts),
        "zero_row_asset_count": sum(shard_zero_row_asset_counts),
        "row_count": len(row_identities),
        "license_restricted_provider_data": baseline_root[
            "license_restricted_provider_data"
        ],
        "source_code_repository_storage_allowed": baseline_root[
            "source_code_repository_storage_allowed"
        ],
        "same_vintage_shard_contract_match": True,
        "requested_symbol_partition_match": True,
        "cross_shard_row_identity_unique": True,
        "catalog_vintage_match": True,
        "catalog_asset_identity_match": True,
        "safety_flags": {name: False for name in SAFETY_FLAG_NAMES},
    }
    manifest_sha256 = hashlib.sha256(
        json.dumps(
            manifest_material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return NorgateShardedCaptureManifest(
        manifest_sha256=manifest_sha256,
        catalog_source_payload_sha256=catalog_evidence.source_payload_sha256,
        catalog_entries_sha256=catalog_evidence.entries_sha256,
        catalog_evidence_sha256=catalog_evidence.catalog_evidence_sha256,
        catalog_entry_count=len(catalog_entries),
        catalog_exported_at=catalog_evidence.exported_at,
        catalog_retrieved_at=catalog_evidence.retrieved_at,
        catalog_receipt_timestamp_basis=catalog_evidence.receipt_timestamp_basis,
        database_name=str(baseline_root["database_name"]),
        database_update_at=str(manifest_material["database_update_at"]),
        norgatedata_package_version=str(
            baseline_root["norgatedata_package_version"]
        ),
        requested_start=str(baseline_root["requested_start"]),
        requested_end=str(baseline_root["requested_end"]),
        requested_symbols=expected,
        requested_symbols_sha256=requested_symbols_sha256,
        shard_source_payload_sha256=tuple(source_hashes),
        shard_requested_symbols_sha256=tuple(shard_requested_hashes),
        shard_asset_dispositions_sha256=tuple(shard_disposition_hashes),
        shard_exported_at=tuple(shard_exported_at),
        shard_symbol_counts=tuple(shard_symbol_counts),
        shard_captured_asset_counts=tuple(shard_captured_asset_counts),
        shard_zero_row_asset_counts=tuple(shard_zero_row_asset_counts),
        shard_row_counts=tuple(shard_row_counts),
        aggregate_reused_symbols=aggregate_reused_symbols,
        asset_count=len(disposition_identity_by_asset),
        captured_asset_count=sum(shard_captured_asset_counts),
        zero_row_asset_count=sum(shard_zero_row_asset_counts),
        row_count=len(row_identities),
        license_restricted_provider_data=baseline_root[
            "license_restricted_provider_data"
        ],
        source_code_repository_storage_allowed=baseline_root[
            "source_code_repository_storage_allowed"
        ],
        _authority=_CAPTURE_MANIFEST_AUTHORITY,
    )


def compare_norgate_same_vintage_exports(
    baseline_payload: bytes,
    repeat_payload: bytes,
) -> NorgateSameVintageDeterminismCheck:
    """Require two independently exported payloads to match at one DB vintage."""

    baseline_root, repeat_root, baseline_exported_at = _same_vintage_roots(
        baseline_payload,
        repeat_payload,
    )
    repeat_exported_at = _canonical_timestamp(
        repeat_root["exported_at"], "repeat exported_at"
    )
    if repeat_exported_at <= baseline_exported_at:
        raise ValueError("repeat export must be a later independent observation")
    invariant_bytes_by_label: dict[str, bytes] = {}
    for label, root in (("baseline", baseline_root), ("repeat", repeat_root)):
        invariant = dict(root)
        invariant.pop("exported_at")
        invariant_bytes_by_label[label] = (
            json.dumps(
                invariant,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    if invariant_bytes_by_label["baseline"] != invariant_bytes_by_label["repeat"]:
        changed = sorted(
            name
            for name in frozenset(baseline_root) - {"exported_at"}
            if baseline_root[name] != repeat_root[name]
        )
        detail = ", ".join(changed) if changed else "canonical invariant bytes"
        raise ValueError("same-vintage Norgate invariant content changed: " + detail)
    return NorgateSameVintageDeterminismCheck(
        baseline_source_payload_sha256=hashlib.sha256(baseline_payload).hexdigest(),
        repeat_source_payload_sha256=hashlib.sha256(repeat_payload).hexdigest(),
        invariant_payload_sha256=hashlib.sha256(
            invariant_bytes_by_label["baseline"]
        ).hexdigest(),
        baseline_exported_at=baseline_exported_at,
        repeat_exported_at=repeat_exported_at,
        database_update_at=_canonical_timestamp(
            baseline_root["database_update_at"], "database_update_at"
        ),
        requested_start=str(baseline_root["requested_start"]),
        requested_end=str(baseline_root["requested_end"]),
        requested_symbols=tuple(baseline_root["requested_symbols"]),
        _authority=_DETERMINISM_AUTHORITY,
    )


@dataclass(frozen=True, slots=True)
class NorgateLocalUniverseCatalogEvidence:
    retrieved_at: str
    receipt_timestamp_basis: str
    exported_at: str
    database_name: str
    database_update_at: str
    norgatedata_package_version: str
    entries: tuple[Mapping[str, Any], ...] = field(repr=False)
    source_payload_sha256: str
    entries_sha256: str
    catalog_evidence_sha256: str
    reused_symbols: tuple[str, ...] = field(repr=False)
    license_restricted_provider_data: bool = True
    source_code_repository_storage_allowed: bool = False
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
    provider_watchlist_semantics_qualified: bool = False
    provider_watchlist_completeness_proven: bool = False
    security_master_admission_allowed: bool = False
    _authority: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._authority is not _UNIVERSE_CATALOG_AUTHORITY:
            raise PermissionError(
                "NorgateLocalUniverseCatalogEvidence must be issued by the stager"
            )
        object.__setattr__(self, "_authority", None)
        if not self.entries:
            raise ValueError("Norgate universe catalog evidence cannot be empty")
        if self.receipt_timestamp_basis not in {
            "SYSTEM_CLOCK_AT_FILE_READ_UNQUALIFIED",
            "CALLER_SUPPLIED_UNQUALIFIED",
        }:
            raise ValueError("Norgate universe catalog receipt basis is unsupported")
        if (
            self.license_restricted_provider_data is not True
            or self.source_code_repository_storage_allowed is not False
        ):
            raise ValueError("Norgate universe catalog license markings were altered")
        if any(
            getattr(self, name) is not False
            for name in UNIVERSE_CATALOG_SAFETY_FLAG_NAMES
        ):
            raise ValueError("Norgate universe catalog safety flags were altered")

    def __reduce__(self) -> Any:
        raise TypeError("Norgate universe catalog evidence is deliberately not pickleable")

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": PROVIDER_ID,
            "provider_dataset_id": DATASET_ID,
            "catalog_scope": (
                "CURRENT_DATABASE_VINTAGE_PROVIDER_NAMED_WATCHLIST_UNQUALIFIED"
            ),
            "watchlist_name": UNIVERSE_WATCHLIST_NAME,
            "watchlist_semantics_basis": (
                "PROVIDER_NAMED_CURRENT_AND_PAST_WATCHLIST_UNQUALIFIED"
            ),
            "retrieved_at": self.retrieved_at,
            "receipt_timestamp_basis": self.receipt_timestamp_basis,
            "exported_at": self.exported_at,
            "database_name": self.database_name,
            "database_update_at": self.database_update_at,
            "norgatedata_package_version": self.norgatedata_package_version,
            "entry_count": len(self.entries),
            "asset_id_min": min(entry["asset_id"] for entry in self.entries),
            "asset_id_max": max(entry["asset_id"] for entry in self.entries),
            "reused_symbol_count": len(self.reused_symbols),
            "source_payload_sha256": self.source_payload_sha256,
            "entries_sha256": self.entries_sha256,
            "catalog_evidence_sha256": self.catalog_evidence_sha256,
            "license_restricted_provider_data": (
                self.license_restricted_provider_data
            ),
            "source_code_repository_storage_allowed": (
                self.source_code_repository_storage_allowed
            ),
            **{
                name: getattr(self, name)
                for name in UNIVERSE_CATALOG_SAFETY_FLAG_NAMES
            },
        }


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


def stage_norgate_local_universe_catalog(
    source: NorgateLocalExportSource,
) -> NorgateLocalUniverseCatalogEvidence:
    """Hash a local watchlist catalog while withholding every admission authority."""

    if not isinstance(source, NorgateLocalExportSource):
        raise ValueError("source must be a NorgateLocalExportSource")
    root, entries = _parse_universe_catalog(source.payload_bytes)
    exported_at = _canonical_timestamp(root["exported_at"], "exported_at")
    if exported_at > source.retrieved_at:
        raise ValueError("Norgate universe catalog cannot postdate local retrieval")
    source_payload_sha256 = hashlib.sha256(source.payload_bytes).hexdigest()
    entries_sha256 = str(root["entries_sha256"])
    evidence_material = {
        "provider_id": PROVIDER_ID,
        "provider_dataset_id": DATASET_ID,
        "export_contract": UNIVERSE_CATALOG_CONTRACT,
        "watchlist_name": UNIVERSE_WATCHLIST_NAME,
        "watchlist_semantics_basis": root["watchlist_semantics_basis"],
        "retrieved_at": source.retrieved_at,
        "receipt_timestamp_basis": source.receipt_timestamp_basis,
        "exported_at": exported_at,
        "database_name": root["database_name"],
        "database_update_at": _canonical_timestamp(
            root["database_update_at"], "database_update_at"
        ),
        "norgatedata_package_version": root["norgatedata_package_version"],
        "entry_count": len(entries),
        "source_payload_sha256": source_payload_sha256,
        "entries_sha256": entries_sha256,
        "reused_symbols": list(root["reused_symbols"]),
        "license_restricted_provider_data": True,
        "source_code_repository_storage_allowed": False,
        "safety_flags": {
            name: False for name in UNIVERSE_CATALOG_SAFETY_FLAG_NAMES
        },
    }
    catalog_evidence_sha256 = hashlib.sha256(
        json.dumps(
            evidence_material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return NorgateLocalUniverseCatalogEvidence(
        retrieved_at=str(source.retrieved_at),
        receipt_timestamp_basis=source.receipt_timestamp_basis,
        exported_at=exported_at,
        database_name=str(root["database_name"]),
        database_update_at=str(evidence_material["database_update_at"]),
        norgatedata_package_version=str(root["norgatedata_package_version"]),
        entries=entries,
        source_payload_sha256=source_payload_sha256,
        entries_sha256=entries_sha256,
        catalog_evidence_sha256=catalog_evidence_sha256,
        reused_symbols=tuple(root["reused_symbols"]),
        _authority=_UNIVERSE_CATALOG_AUTHORITY,
    )


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
