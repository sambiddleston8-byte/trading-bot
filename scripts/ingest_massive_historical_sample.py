#!/usr/bin/env python3
from __future__ import annotations

"""Stage one Massive daily-bar API response or local sample file safely."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.orchestration.massive_historical_adapter import (
    JSON_FORMAT,
    MAX_SOURCE_BYTES,
    MassiveHistoricalAdapter,
    MassiveHistoricalSampleClient,
    MassiveHistoricalSource,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage a bounded Massive custom-bars sample; never runs a replay or trading action."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--sample-file", type=Path)
    source.add_argument("--api", action="store_true")
    parser.add_argument("--format", choices=("JSON", "CSV"))
    parser.add_argument("--symbol")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--decision-at")
    return parser.parse_args()


def _local_source(
    path: Path,
    transport_format: str | None,
) -> MassiveHistoricalSource:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError("sample file does not exist or is not a regular file")
    if resolved.stat().st_size <= 0 or resolved.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("sample file size is outside the accepted boundary")
    selected = transport_format or ("CSV" if resolved.suffix.lower() == ".csv" else JSON_FORMAT)
    return MassiveHistoricalSource(
        transport_format=selected,
        retrieved_at=datetime.now(timezone.utc),
        payload_bytes=resolved.read_bytes(),
    )


def _api_key(path: Path | None) -> str:
    environment_key = os.getenv("MASSIVE_API_KEY")
    if path is not None and environment_key:
        raise ValueError("use either an API-key environment variable or --api-key-file")
    if path is None:
        if not environment_key:
            raise ValueError("set MASSIVE_API_KEY or provide --api-key-file")
        return environment_key
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or not 0 < resolved.stat().st_size <= 502:
        raise ValueError("API-key file is missing or has an invalid size")
    try:
        value = resolved.read_text(encoding="ascii").rstrip("\r\n")
    except UnicodeError as error:
        raise ValueError("API-key file must contain ASCII text") from error
    if "\n" in value or "\r" in value:
        raise ValueError("API-key file must contain exactly one line")
    return value


def main() -> int:
    arguments = _arguments()
    try:
        if arguments.api:
            if arguments.format is not None:
                raise ValueError("--format is only valid with --sample-file")
            if not all((arguments.symbol, arguments.start, arguments.end)):
                raise ValueError("--api requires --symbol, --start and --end")
            fetched = MassiveHistoricalSampleClient(
                _api_key(arguments.api_key_file)
            ).fetch_daily_bars(
                symbol=arguments.symbol,
                start=arguments.start,
                end=arguments.end,
            )
            source = MassiveHistoricalSource(
                transport_format=JSON_FORMAT,
                retrieved_at=fetched.retrieved_at,
                payload_bytes=fetched.payload_bytes,
            )
        else:
            if any(
                (
                    arguments.symbol,
                    arguments.start,
                    arguments.end,
                    arguments.api_key_file,
                )
            ):
                raise ValueError("API key, symbol and date options are only valid with --api")
            source = _local_source(
                arguments.sample_file,
                arguments.format,
            )
        decision_at = arguments.decision_at or source.retrieved_at
        result = MassiveHistoricalAdapter().normalize(
            source=source,
            decision_at=decision_at,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Massive sample ingest failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result.as_dict(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
