#!/usr/bin/env python3
from __future__ import annotations

"""Capture three tiny Sharadar responses into owner-local quarantine.

The key is read from macOS Keychain.  It is never accepted on the command
line, printed, written into the repository, or included in capture metadata.
"""

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.orchestration.sharadar_quarantine import execute_connectivity_capture
from scripts._sharadar_keychain import load as load_key


def main() -> int:
    try:
        key = load_key()
        records = execute_connectivity_capture(repository_root=ROOT, api_key=key)
        print(
            json.dumps(
                [
                    {
                        "table": record["table"],
                        "role": record["role"],
                        "row_count": record["row_count"],
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
        print(f"Sharadar connectivity capture failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
