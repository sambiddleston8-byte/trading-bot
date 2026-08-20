#!/usr/bin/env python3
from __future__ import annotations

"""Compare two quarantined local Norgate exports at one database vintage."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.orchestration.norgate_local_export import (
    MAX_SOURCE_BYTES,
    compare_norgate_same_vintage_exports,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fail closed unless two independently captured local Norgate exports "
            "differ only in their export observation timestamp."
        )
    )
    parser.add_argument("--baseline-file", required=True, type=Path)
    parser.add_argument("--repeat-file", required=True, type=Path)
    return parser.parse_args()


def _payload(path: Path, name: str) -> bytes:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or not 0 < resolved.stat().st_size <= MAX_SOURCE_BYTES:
        raise ValueError(f"{name} is missing or outside the size boundary")
    return resolved.read_bytes()


def main() -> int:
    arguments = _arguments()
    try:
        baseline_path = arguments.baseline_file.expanduser().resolve()
        repeat_path = arguments.repeat_file.expanduser().resolve()
        if baseline_path == repeat_path:
            raise ValueError("baseline and repeat must be distinct files")
        result = compare_norgate_same_vintage_exports(
            _payload(baseline_path, "baseline file"),
            _payload(repeat_path, "repeat file"),
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Norgate local comparison failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result.as_dict(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
