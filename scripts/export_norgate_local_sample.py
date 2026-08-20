#!/usr/bin/env python3
from __future__ import annotations

"""Export deterministic Norgate rows from the Windows updater database.

This script is intentionally local-only: the Norgate Python package reads the
installed updater database and this code makes no HTTP request.  Output is an
exclusive-create canonical JSON file for later fail-closed staging on macOS.
"""

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
from importlib.machinery import ModuleSpec
import json
import math
import os
from pathlib import Path
import platform
import sys
from types import ModuleType
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class _NorgateContract:
    dataset_id: str
    export_contract: str
    index_name: str
    max_records: int
    max_source_bytes: int
    provider_id: str
    parse_export: Callable[[bytes], Any]


def _install_windows_fcntl_guard(system_name: str | None = None) -> None:
    """Permit read-only imports on Windows while keeping file locking fail-closed."""

    if (system_name or platform.system()) != "Windows" or "fcntl" in sys.modules:
        return
    guard = ModuleType("fcntl")
    guard.__spec__ = ModuleSpec("fcntl", loader=None)
    for name, value in {
        "LOCK_SH": 1,
        "LOCK_EX": 2,
        "LOCK_NB": 4,
        "LOCK_UN": 8,
    }.items():
        setattr(guard, name, value)

    def unavailable(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("POSIX file locking is unavailable in the Windows extraction VM")

    guard.flock = unavailable  # type: ignore[attr-defined]

    def unavailable_attribute(_name: str) -> Any:
        if _name.startswith("__") and _name.endswith("__"):
            raise AttributeError(_name)
        raise OSError("POSIX file locking is unavailable in the Windows extraction VM")

    guard.__getattr__ = unavailable_attribute  # type: ignore[attr-defined]
    sys.modules["fcntl"] = guard


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _norgate_contract() -> _NorgateContract:
    """Load the repository contract only when an export is actually built."""

    _install_windows_fcntl_guard()
    from core.orchestration.norgate_local_export import (
        CAPTURE_EXPORT_CONTRACT,
        DATASET_ID,
        INDEX_NAME,
        MAX_RECORDS,
        MAX_SOURCE_BYTES,
        PROVIDER_ID,
        parse_norgate_local_export,
    )

    return _NorgateContract(
        dataset_id=DATASET_ID,
        export_contract=CAPTURE_EXPORT_CONTRACT,
        index_name=INDEX_NAME,
        max_records=MAX_RECORDS,
        max_source_bytes=MAX_SOURCE_BYTES,
        provider_id=PROVIDER_ID,
        parse_export=parse_norgate_local_export,
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export an unadjusted, unpadded local Norgate price and S&P 500 "
            "membership sample; never runs a replay or trading action."
        )
    )
    symbols = parser.add_mutually_exclusive_group(required=True)
    symbols.add_argument("--symbol", action="append")
    symbols.add_argument("--symbols-file", type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def _iso_date(value: str, name: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an ISO date") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be a canonical ISO date")
    return parsed


def _symbols(arguments: argparse.Namespace) -> tuple[str, ...]:
    if arguments.symbol:
        values: Iterable[str] = arguments.symbol
    else:
        path = arguments.symbols_file.expanduser().resolve()
        if not path.is_file() or not 0 < path.stat().st_size <= 1_000_000:
            raise ValueError("symbols file is missing or outside the size boundary")
        values = path.read_text(encoding="utf-8").splitlines()
    resolved = tuple(value.strip() for value in values if value.strip())
    if not resolved or len(resolved) > 100:
        raise ValueError("symbols must contain between 1 and 100 entries per export")
    if len(resolved) != len(set(resolved)):
        raise ValueError("symbols must not repeat")
    return resolved


def _session_date(value: Any) -> str:
    if hasattr(value, "date"):
        value = value.date()
    try:
        resolved = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except ValueError as error:
        raise ValueError("Norgate returned an invalid session date") from error
    return resolved.isoformat()


def _finite(value: Any, name: str, *, nonnegative: bool = False) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Norgate {name} is not numeric") from error
    if not math.isfinite(resolved) or (nonnegative and resolved < 0):
        raise ValueError(f"Norgate {name} is outside the supported range")
    return resolved


def _membership_values(frame: Any) -> dict[str, bool]:
    if hasattr(frame, "columns"):
        columns = list(frame.columns)
        if "Index Constituent" not in columns:
            raise ValueError(
                "Norgate membership result is missing Index Constituent"
            )
        series = frame["Index Constituent"]
    else:
        series = frame
    if not hasattr(series, "items"):
        raise ValueError("Norgate membership result is not an indexed series")
    result: dict[str, bool] = {}
    for index, value in series.items():
        if value is True or value == 1:
            member = True
        elif value is False or value == 0:
            member = False
        else:
            raise ValueError("Norgate membership value must be boolean")
        session = _session_date(index)
        if session in result:
            raise ValueError("Norgate membership result repeats a session")
        result[session] = member
    return result


def _price_rows(frame: Any) -> list[tuple[str, Any]]:
    required = {
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "Unadjusted Close",
        "Dividend",
    }
    if not hasattr(frame, "columns") or not hasattr(frame, "iterrows"):
        raise ValueError("Norgate price result is not an indexed data frame")
    if not required.issubset(set(frame.columns)):
        raise ValueError("Norgate price result is missing required stock columns")
    return [(_session_date(index), row) for index, row in frame.iterrows()]


def build_export(
    *,
    norgatedata: Any,
    symbols: Iterable[str],
    database_name: str,
    start: date,
    end: date,
    exported_at: datetime,
) -> bytes:
    """Build canonical bytes from an injected local Norgate package instance."""

    contract = _norgate_contract()

    if end < start:
        raise ValueError("requested date range is reversed")
    if exported_at.tzinfo is None or exported_at.utcoffset() is None:
        raise ValueError("exported_at must be timezone-aware")
    requested_symbols = tuple(symbols)
    if (
        not requested_symbols
        or len(requested_symbols) > 100
        or len(requested_symbols) != len(set(requested_symbols))
    ):
        raise ValueError("symbols must contain between 1 and 100 entries per export")
    if (
        not isinstance(database_name, str)
        or not database_name.strip()
        or database_name != database_name.strip()
        or len(database_name) > 100
        or not database_name.isprintable()
    ):
        raise ValueError("database_name must be nonempty canonical text")
    database_update_at = norgatedata.last_database_update_time(database_name)
    if (
        not isinstance(database_update_at, datetime)
        or database_update_at.tzinfo is None
        or database_update_at.utcoffset() is None
    ):
        raise ValueError("Norgate database update time must be timezone-aware")
    rows: list[dict[str, Any]] = []
    asset_dispositions: list[dict[str, Any]] = []
    for symbol in requested_symbols:
        asset_id = norgatedata.assetid(symbol)
        if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id <= 0:
            raise ValueError("Norgate assetid must be a positive integer")
        resolved_symbol = norgatedata.symbol(asset_id)
        if not isinstance(resolved_symbol, str) or not resolved_symbol.strip():
            raise ValueError("Norgate symbol must be nonempty text")
        security_name = norgatedata.security_name(asset_id)
        if not isinstance(security_name, str) or not security_name.strip():
            raise ValueError("Norgate security_name must be nonempty text")
        prices = norgatedata.price_timeseries(
            asset_id,
            stock_price_adjustment_setting=norgatedata.StockPriceAdjustmentType.NONE,
            padding_setting=norgatedata.PaddingType.NONE,
            start_date=start,
            end_date=end,
            timeseriesformat="pandas-dataframe",
            interval="D",
        )
        raw_price_rows = _price_rows(prices)
        if not raw_price_rows:
            asset_dispositions.append(
                {
                    "asset_id": asset_id,
                    "requested_symbol": symbol,
                    "symbol": resolved_symbol.strip(),
                    "security_name": security_name.strip(),
                    "status": "NO_ROWS_IN_REQUESTED_WINDOW",
                    "row_count": 0,
                }
            )
            continue
        membership = norgatedata.index_constituent_timeseries(
            asset_id,
            contract.index_name,
            padding_setting=norgatedata.PaddingType.NONE,
            pandas_dataframe=prices,
            timeseriesformat="pandas-dataframe",
        )
        membership_by_date = _membership_values(membership)
        price_rows = _price_rows(membership)
        for session, row in price_rows:
            rows.append(
                {
                    "asset_id": asset_id,
                    "requested_symbol": symbol,
                    "symbol": resolved_symbol.strip(),
                    "security_name": security_name.strip(),
                    "session_date": session,
                    "open": _finite(row["Open"], "Open"),
                    "high": _finite(row["High"], "High"),
                    "low": _finite(row["Low"], "Low"),
                    "close": _finite(row["Close"], "Close"),
                    "volume": _finite(row["Volume"], "Volume", nonnegative=True),
                    "unadjusted_close": _finite(
                        row["Unadjusted Close"], "Unadjusted Close"
                    ),
                    "dividend": _finite(
                        row["Dividend"], "Dividend", nonnegative=True
                    ),
                    "sp500_constituent": membership_by_date[session],
                }
            )
            if len(rows) > contract.max_records:
                raise ValueError("Norgate export exceeds the record boundary")
        asset_dispositions.append(
            {
                "asset_id": asset_id,
                "requested_symbol": symbol,
                "symbol": resolved_symbol.strip(),
                "security_name": security_name.strip(),
                "status": "ROWS_PRESENT",
                "row_count": len(price_rows),
            }
        )
    rows.sort(key=lambda item: (item["asset_id"], item["session_date"]))
    asset_dispositions.sort(key=lambda item: item["asset_id"])
    if tuple(item["requested_symbol"] for item in asset_dispositions) != (
        requested_symbols
    ):
        raise ValueError("requested symbols must be ordered by stable asset ID")
    assets_by_symbol: dict[str, set[int]] = {}
    for disposition in asset_dispositions:
        assets_by_symbol.setdefault(disposition["symbol"], set()).add(
            disposition["asset_id"]
        )
    reused_symbols = sorted(
        symbol for symbol, asset_ids in assets_by_symbol.items() if len(asset_ids) > 1
    )
    requested_symbols_sha256 = hashlib.sha256(
        json.dumps(requested_symbols, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    asset_dispositions_sha256 = hashlib.sha256(
        json.dumps(
            asset_dispositions,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    payload = {
        "schema_version": "2.0",
        "export_contract": contract.export_contract,
        "provider_id": contract.provider_id,
        "provider_dataset_id": contract.dataset_id,
        "norgatedata_package_version": str(norgatedata.__version__),
        "database_name": database_name,
        "database_update_at": database_update_at.astimezone(timezone.utc).isoformat(
            timespec="microseconds"
        ),
        "universe_selection_basis": "OPERATOR_SUPPLIED_SYMBOLS_UNQUALIFIED",
        "requested_symbols": list(requested_symbols),
        "requested_symbols_sha256": requested_symbols_sha256,
        "asset_dispositions": asset_dispositions,
        "asset_dispositions_sha256": asset_dispositions_sha256,
        "reused_symbols": reused_symbols,
        "license_restricted_provider_data": True,
        "source_code_repository_storage_allowed": False,
        "exported_at": exported_at.astimezone(timezone.utc).isoformat(
            timespec="microseconds"
        ),
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "frequency": "DAILY",
        "stock_price_adjustment": "NONE",
        "padding": "NONE",
        "membership_dataset": contract.index_name,
        "rows": rows,
    }
    encoded = (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if len(encoded) > contract.max_source_bytes:
        raise ValueError("Norgate export exceeds the byte boundary")
    contract.parse_export(encoded)
    return encoded


def write_verified_export(output: Path, payload: bytes) -> str:
    """Exclusively create one export and verify the bytes that reached disk."""

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as target:
        written = target.write(payload)
        target.flush()
        os.fsync(target.fileno())
    source_payload_sha256 = hashlib.sha256(payload).hexdigest()
    if (
        written != len(payload)
        or hashlib.sha256(output.read_bytes()).hexdigest() != source_payload_sha256
    ):
        raise OSError("Norgate export could not be verified after writing")
    return source_payload_sha256


def main() -> int:
    arguments = _arguments()
    try:
        if platform.system() != "Windows":
            raise ValueError("Norgate export must run inside the Windows extraction VM")
        start = _iso_date(arguments.start, "start")
        end = _iso_date(arguments.end, "end")
        symbols = _symbols(arguments)
        try:
            import norgatedata
        except ImportError as error:
            raise ValueError(
                "install the official norgatedata package inside the Windows VM"
            ) from error
        payload = build_export(
            norgatedata=norgatedata,
            symbols=symbols,
            database_name=arguments.database_name,
            start=start,
            end=end,
            exported_at=datetime.now(timezone.utc),
        )
        output = arguments.output.expanduser().resolve()
        source_payload_sha256 = write_verified_export(output, payload)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Norgate local export failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "output": str(output),
                "byte_length": len(payload),
                "source_payload_sha256": source_payload_sha256,
                "source_only": True,
                "performance_use_allowed": False,
                "validation_accessed": False,
                "test_accessed": False,
                "broker_connection_allowed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
