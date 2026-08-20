#!/usr/bin/env python3
"""Export the fixed local Norgate S&P 500 current-and-past identity catalog."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.export_norgate_local_sample import (
    _install_windows_fcntl_guard,
    write_verified_export,
)


@dataclass(frozen=True)
class _UniverseContract:
    contract: str
    dataset_id: str
    maximum_bytes: int
    maximum_entries: int
    parse: Callable[[bytes], Any]
    provider_id: str
    watchlist_name: str


def _universe_contract() -> _UniverseContract:
    _install_windows_fcntl_guard()
    from core.orchestration.norgate_local_export import (
        DATASET_ID,
        MAX_UNIVERSE_CATALOG_BYTES,
        MAX_UNIVERSE_CATALOG_ENTRIES,
        PROVIDER_ID,
        UNIVERSE_CATALOG_CONTRACT,
        UNIVERSE_WATCHLIST_NAME,
        parse_norgate_local_universe_catalog,
    )

    return _UniverseContract(
        contract=UNIVERSE_CATALOG_CONTRACT,
        dataset_id=DATASET_ID,
        maximum_bytes=MAX_UNIVERSE_CATALOG_BYTES,
        maximum_entries=MAX_UNIVERSE_CATALOG_ENTRIES,
        parse=parse_norgate_local_universe_catalog,
        provider_id=PROVIDER_ID,
        watchlist_name=UNIVERSE_WATCHLIST_NAME,
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export the fixed provider-named S&P 500 Current & Past identity "
            "watchlist from the installed local updater; never replays or trades."
        )
    )
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def _canonical_text(value: Any, name: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or not value.isprintable()
    ):
        raise ValueError(f"{name} must be nonempty canonical text")
    return value


def build_universe_catalog(
    *,
    norgatedata: Any,
    database_name: str,
    exported_at: datetime,
) -> bytes:
    """Build canonical catalog bytes from an injected local Norgate package."""

    contract = _universe_contract()
    database = _canonical_text(database_name, "database_name", 100)
    if exported_at.tzinfo is None or exported_at.utcoffset() is None:
        raise ValueError("exported_at must be timezone-aware")
    database_update_at = norgatedata.last_database_update_time(database)
    if (
        not isinstance(database_update_at, datetime)
        or database_update_at.tzinfo is None
        or database_update_at.utcoffset() is None
    ):
        raise ValueError("Norgate database update time must be timezone-aware")
    values = norgatedata.watchlist(contract.watchlist_name)
    if (
        not isinstance(values, list)
        or not values
        or len(values) > contract.maximum_entries
    ):
        raise ValueError("Norgate watchlist must return a bounded nonempty list")
    entries: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("Norgate watchlist entry must be a mapping")
        asset_id = value.get("assetid")
        if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id <= 0:
            raise ValueError("Norgate watchlist assetid must be a positive integer")
        entries.append(
            {
                "asset_id": asset_id,
                "symbol": _canonical_text(value.get("symbol"), "symbol", 32),
                "security_name": _canonical_text(
                    value.get("securityname"), "securityname", 300
                ),
            }
        )
    entries.sort(key=lambda item: item["asset_id"])
    asset_ids = [item["asset_id"] for item in entries]
    if len(asset_ids) != len(set(asset_ids)):
        raise ValueError("Norgate watchlist repeats a stable assetid")
    assets_by_symbol: dict[str, set[int]] = {}
    for entry in entries:
        assets_by_symbol.setdefault(entry["symbol"], set()).add(entry["asset_id"])
    reused_symbols = sorted(
        symbol for symbol, asset_ids in assets_by_symbol.items() if len(asset_ids) > 1
    )
    entries_sha256 = hashlib.sha256(
        json.dumps(
            entries,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    payload = {
        "schema_version": "1.0",
        "export_contract": contract.contract,
        "provider_id": contract.provider_id,
        "provider_dataset_id": contract.dataset_id,
        "norgatedata_package_version": str(norgatedata.__version__),
        "database_name": database,
        "database_update_at": database_update_at.astimezone(timezone.utc).isoformat(
            timespec="microseconds"
        ),
        "watchlist_name": contract.watchlist_name,
        "watchlist_semantics_basis": (
            "PROVIDER_NAMED_CURRENT_AND_PAST_WATCHLIST_UNQUALIFIED"
        ),
        "exported_at": exported_at.astimezone(timezone.utc).isoformat(
            timespec="microseconds"
        ),
        "license_restricted_provider_data": True,
        "source_code_repository_storage_allowed": False,
        "entry_count": len(entries),
        "entries_sha256": entries_sha256,
        "reused_symbols": reused_symbols,
        "entries": entries,
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
    if len(encoded) > contract.maximum_bytes:
        raise ValueError("Norgate universe catalog exceeds the byte boundary")
    contract.parse(encoded)
    return encoded


def main() -> int:
    arguments = _arguments()
    try:
        if platform.system() != "Windows":
            raise ValueError("Norgate universe export must run inside the Windows VM")
        try:
            import norgatedata
        except ImportError as error:
            raise ValueError(
                "install the official norgatedata package inside the Windows VM"
            ) from error
        payload = build_universe_catalog(
            norgatedata=norgatedata,
            database_name=arguments.database_name,
            exported_at=datetime.now(timezone.utc),
        )
        output = arguments.output.expanduser().resolve()
        source_payload_sha256 = write_verified_export(output, payload)
        entry_count = len(json.loads(payload)["entries"])
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"Norgate local universe export failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "output": str(output),
                "byte_length": len(payload),
                "entry_count": entry_count,
                "source_payload_sha256": source_payload_sha256,
                "source_only": True,
                "security_master_admission_allowed": False,
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
