#!/usr/bin/env python3
from __future__ import annotations

"""Inspect or download the frozen five-table Sharadar ten-year bundle."""

import argparse
import json
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.orchestration.sharadar_quarantine import (
    QUARANTINE_RELATIVE_PATH,
    execute_ten_year_bulk_capture,
    inspect_ten_year_bulk_status,
)
from scripts._sharadar_keychain import load as load_key


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description=(
            "Inspect licensed Sharadar bulk-file sizes or download the exact "
            "ten-year Stage 1 foundation into owner-local quarantine."
        )
    )
    modes = value.add_mutually_exclusive_group(required=True)
    modes.add_argument("--status", action="store_true")
    modes.add_argument("--download", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        key = load_key()
        if arguments.status:
            statuses = inspect_ten_year_bulk_status(api_key=key)
            required = sum(item.size for item in statuses)
            destination = ROOT / QUARANTINE_RELATIVE_PATH
            destination.parent.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(destination.parent).free
            print(
                json.dumps(
                    {
                        "tables": [item.as_dict() for item in statuses],
                        "compressed_bytes_required": required,
                        "free_disk_bytes": free,
                        "download_fits_with_double_space_margin": free >= required * 2,
                        "quarantine_only": True,
                        "dataset_admitted": False,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            records = execute_ten_year_bulk_capture(
                repository_root=ROOT,
                api_key=key,
            )
            print(
                json.dumps(
                    [
                        {
                            "table": record["table"],
                            "byte_length": record["byte_length"],
                            "payload_sha256": record["payload_sha256"],
                            "quarantine_only": record["quarantine_only"],
                            "dataset_admitted": record["dataset_admitted"],
                        }
                        for record in records
                    ],
                    indent=2,
                    sort_keys=True,
                )
            )
        return 0
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"Sharadar bulk capture failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
