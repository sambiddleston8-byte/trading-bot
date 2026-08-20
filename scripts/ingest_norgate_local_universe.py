#!/usr/bin/env python3
"""Stage exact local Norgate identity-catalog bytes without admitting them."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.orchestration.norgate_local_export import (
    MAX_UNIVERSE_CATALOG_BYTES,
    NorgateLocalExportSource,
    stage_norgate_local_universe_catalog,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Hash a local Norgate S&P 500 Current & Past identity catalog "
            "without authenticating, admitting, replaying, or trading."
        )
    )
    parser.add_argument("--catalog-file", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    try:
        path = arguments.catalog_file.expanduser().resolve()
        if not path.is_file():
            raise ValueError("catalog file is not a file")
        size = path.stat().st_size
        if not 0 < size <= MAX_UNIVERSE_CATALOG_BYTES:
            raise ValueError("catalog file is outside the byte boundary")
        payload = path.read_bytes()
        if len(payload) != size:
            raise OSError("catalog file changed while being read")
        source = NorgateLocalExportSource(
            retrieved_at=datetime.now(timezone.utc),
            payload_bytes=payload,
            receipt_timestamp_basis="SYSTEM_CLOCK_AT_FILE_READ_UNQUALIFIED",
        )
        evidence = stage_norgate_local_universe_catalog(source)
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"Norgate local universe ingest failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(evidence.as_dict(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
