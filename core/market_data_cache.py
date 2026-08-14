from __future__ import annotations

"""Non-authoritative, immutable Parquet cache for legacy Yahoo workflows."""

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import pandas as pd
import yfinance as yf


SCHEMA_VERSION = "1.0"
FORMAT_DIRECTORY = "parquet-v1"
MANIFEST_KEYS = {
    "schema_version",
    "record_type",
    "provider",
    "symbol",
    "requested_start",
    "requested_end",
    "retrieved_at",
    "content_sha256",
    "parquet_file",
    "row_count",
    "first_index",
    "last_index",
    "index_name",
    "columns",
    "dtypes",
    "prices_are_back_adjusted",
    "point_in_time",
    "survivorship_safe",
    "admissible_as_replay_evidence",
}
SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _requested(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    if not result:
        raise ValueError("market-data date bounds cannot be blank")
    return result


def _atomic_write_once(path: Path, payload: bytes) -> None:
    """Publish new immutable bytes without replacing an existing snapshot."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ValueError("immutable market-data snapshot path already has different bytes")
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class MarketDataCache:
    """Cache legacy adjusted Yahoo frames without treating them as replay evidence."""

    def __init__(self, directory: str | Path = "data/market_cache") -> None:
        self.directory = Path(directory)
        self.snapshot_root = self.directory / FORMAT_DIRECTORY
        self.snapshot_root.mkdir(parents=True, exist_ok=True)

    def _symbol_directory(self, symbol: str) -> Path:
        resolved = str(symbol).strip()
        if not resolved:
            raise ValueError("symbol is required")
        readable = SAFE_COMPONENT.sub("_", resolved).strip("._-") or "symbol"
        identity = sha256(resolved.encode("utf-8")).hexdigest()[:12]
        return self.snapshot_root / f"{readable[:48]}-{identity}"

    def _legacy_path(self, symbol: str) -> Path:
        safe_symbol = str(symbol).replace("^", "_").replace("/", "_")
        return self.directory / f"{safe_symbol}.pkl"

    def _query(self, symbol: str, start: Any, end: Any) -> tuple[dict[str, Any], str]:
        query = {
            "provider": "YAHOO_FINANCE_VIA_YFINANCE",
            "symbol": str(symbol).strip(),
            "requested_start": _requested(start),
            "requested_end": _requested(end),
            "auto_adjust": True,
        }
        identifier = sha256(_canonical_json(query).encode("utf-8")).hexdigest()
        return query, identifier

    def _fetch(self, symbol: str, start: Any, end: Any) -> pd.DataFrame:
        return yf.Ticker(symbol).history(start=start, end=end, auto_adjust=True)

    @staticmethod
    def _validate_frame(data: Any) -> pd.DataFrame:
        if not isinstance(data, pd.DataFrame) or data.empty:
            raise ValueError("market-data snapshot must be a nonempty pandas DataFrame")
        if not isinstance(data.index, pd.DatetimeIndex):
            raise ValueError("market-data snapshot requires a DatetimeIndex")
        if not data.index.is_monotonic_increasing or not data.index.is_unique:
            raise ValueError("market-data snapshot index must be sorted and unique")
        return data

    def download(self, symbol: str, start: Any = "2018-01-01", end: Any = None) -> pd.DataFrame:
        print(f"Downloading {symbol}...")
        data = self._validate_frame(self._fetch(symbol, start, end))
        query, query_id = self._query(symbol, start, end)

        buffer = BytesIO()
        data.to_parquet(buffer, engine="pyarrow", index=True)
        payload = buffer.getvalue()
        content_hash = sha256(payload).hexdigest()
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        version = f"{retrieved_at.replace(':', '').replace('+', '_')}-{content_hash[:16]}"
        query_directory = self._symbol_directory(symbol) / query_id
        parquet_name = f"{version}.parquet"
        parquet_path = query_directory / parquet_name
        manifest_path = query_directory / f"{version}.json"

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "NON_AUTHORITATIVE_LEGACY_MARKET_CACHE_SNAPSHOT",
            "provider": query["provider"],
            "symbol": query["symbol"],
            "requested_start": query["requested_start"],
            "requested_end": query["requested_end"],
            "retrieved_at": retrieved_at,
            "content_sha256": content_hash,
            "parquet_file": parquet_name,
            "row_count": len(data),
            "first_index": data.index[0].isoformat(),
            "last_index": data.index[-1].isoformat(),
            "index_name": data.index.name,
            "columns": [str(column) for column in data.columns],
            "dtypes": [str(dtype) for dtype in data.dtypes],
            "prices_are_back_adjusted": True,
            "point_in_time": False,
            "survivorship_safe": False,
            "admissible_as_replay_evidence": False,
        }
        _atomic_write_once(parquet_path, payload)
        _atomic_write_once(manifest_path, (_canonical_json(manifest) + "\n").encode("utf-8"))
        return data

    def _load_latest_snapshot(self, symbol: str, start: Any, end: Any) -> pd.DataFrame | None:
        _, query_id = self._query(symbol, start, end)
        query_directory = self._symbol_directory(symbol) / query_id
        manifests = sorted(query_directory.glob("*.json"))
        if not manifests:
            return None

        validated: list[tuple[datetime, dict[str, Any], Path]] = []
        for path in manifests:
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError(f"invalid market-data cache manifest: {path.name}") from error
            if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
                raise ValueError(f"market-data cache manifest has an invalid schema: {path.name}")
            try:
                retrieved = datetime.fromisoformat(manifest["retrieved_at"])
            except (TypeError, ValueError) as error:
                raise ValueError("market-data cache retrieved_at is invalid") from error
            if retrieved.tzinfo is None or retrieved > datetime.now(timezone.utc):
                raise ValueError("market-data cache retrieved_at must be a non-future aware time")
            validated.append((retrieved, manifest, path))

        _, manifest, manifest_path = max(validated, key=lambda item: (item[0], item[2].name))
        expected_query, _ = self._query(symbol, start, end)
        boundary = (
            manifest["schema_version"] == SCHEMA_VERSION
            and manifest["record_type"] == "NON_AUTHORITATIVE_LEGACY_MARKET_CACHE_SNAPSHOT"
            and manifest["provider"] == expected_query["provider"]
            and manifest["symbol"] == expected_query["symbol"]
            and manifest["requested_start"] == expected_query["requested_start"]
            and manifest["requested_end"] == expected_query["requested_end"]
            and manifest["prices_are_back_adjusted"] is True
            and manifest["point_in_time"] is False
            and manifest["survivorship_safe"] is False
            and manifest["admissible_as_replay_evidence"] is False
        )
        if not boundary:
            raise ValueError("market-data cache manifest violates its non-authoritative boundary")

        parquet_path = manifest_path.parent / str(manifest["parquet_file"])
        if parquet_path.parent != manifest_path.parent or parquet_path.suffix != ".parquet":
            raise ValueError("market-data cache manifest contains an unsafe parquet path")
        try:
            payload = parquet_path.read_bytes()
        except OSError as error:
            raise ValueError("market-data cache parquet payload is missing") from error
        if sha256(payload).hexdigest() != manifest["content_sha256"]:
            raise ValueError("market-data cache parquet integrity check failed")
        try:
            data = self._validate_frame(pd.read_parquet(BytesIO(payload), engine="pyarrow"))
        except Exception as error:
            raise ValueError("market-data cache parquet payload is invalid") from error
        if (
            len(data) != manifest["row_count"]
            or data.index[0].isoformat() != manifest["first_index"]
            or data.index[-1].isoformat() != manifest["last_index"]
            or data.index.name != manifest["index_name"]
            or [str(column) for column in data.columns] != manifest["columns"]
            or [str(dtype) for dtype in data.dtypes] != manifest["dtypes"]
        ):
            raise ValueError("market-data cache parquet metadata does not reconcile")
        return data

    @staticmethod
    def _bound(index: pd.DatetimeIndex, value: Any) -> pd.Timestamp:
        bound = pd.Timestamp(value)
        if index.tz is not None:
            bound = bound.tz_localize(index.tz) if bound.tzinfo is None else bound.tz_convert(index.tz)
        elif bound.tzinfo is not None:
            bound = bound.tz_convert(timezone.utc).tz_localize(None)
        return bound

    def load(self, symbol: str, start: Any = "2018-01-01", end: Any = None) -> pd.DataFrame:
        data = self._load_latest_snapshot(symbol, start, end)
        if data is None:
            if self._legacy_path(symbol).exists():
                print(f"Ignoring unsafe legacy pickle cache for {symbol}; downloading Parquet snapshot.")
            data = self.download(symbol, start, end)
        else:
            print(f"Using cached {symbol}")

        if start:
            data = data[data.index >= self._bound(data.index, start)]
        if end:
            data = data[data.index < self._bound(data.index, end)]
        return data

    def load_universe(
        self,
        symbols: Any,
        start: Any = "2018-01-01",
        end: Any = None,
        allow_partial: bool = False,
    ) -> dict[str, pd.DataFrame]:
        data: dict[str, pd.DataFrame] = {}
        failures: dict[str, Exception] = {}
        for symbol in symbols:
            try:
                data[symbol] = self.load(symbol, start, end)
            except Exception as error:
                failures[symbol] = error
                print(f"{symbol} failed: {error}")
        if failures and not allow_partial:
            failed_symbols = ", ".join(sorted(failures))
            raise RuntimeError(
                "Market data is incomplete; refusing to run a biased partial "
                f"universe. Failed symbols: {failed_symbols}"
            )
        return data


if __name__ == "__main__":
    cache = MarketDataCache()
    symbols = ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "AAPL", "^GSPC"]
    data = cache.load_universe(symbols)
    print()
    for symbol, prices in data.items():
        print(f"{symbol}: {len(prices)} rows")
