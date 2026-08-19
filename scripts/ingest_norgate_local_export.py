#!/usr/bin/env python3
from __future__ import annotations

"""Stage exact bytes copied from the Windows Norgate extraction VM."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.orchestration.norgate_local_export import (
    MAX_SOURCE_BYTES,
    NorgateLocalExportAdapter,
    NorgateLocalExportSource,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stage a local Norgate export without admitting it to replay, "
            "performance evaluation, VALIDATION, TEST, or trading."
        )
    )
    parser.add_argument("--export-file", required=True, type=Path)
    parser.add_argument("--decision-at")
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    try:
        path = arguments.export_file.expanduser().resolve()
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_SOURCE_BYTES:
            raise ValueError("export file is missing or outside the size boundary")
        retrieved_at = datetime.now(timezone.utc)
        source = NorgateLocalExportSource(
            retrieved_at=retrieved_at,
            payload_bytes=path.read_bytes(),
            receipt_timestamp_basis="SYSTEM_CLOCK_AT_FILE_READ_UNQUALIFIED",
        )
        result = NorgateLocalExportAdapter().normalize(
            source=source,
            decision_at=arguments.decision_at or source.retrieved_at,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Norgate local ingest failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result.as_dict(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
