#!/usr/bin/env python3
from __future__ import annotations

"""Assemble exact local Norgate export shards into a non-authoritative manifest."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.orchestration.norgate_local_export import (
    MAX_CAPTURE_BYTES,
    MAX_CAPTURE_SHARDS,
    MAX_CAPTURE_SYMBOLS,
    MAX_SOURCE_BYTES,
    assemble_norgate_sharded_capture_manifest,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bind same-vintage local Norgate export shards to an exact symbol "
            "partition; never authenticates, admits, replays, or trades."
        )
    )
    parser.add_argument("--export-file", action="append", required=True, type=Path)
    parser.add_argument("--symbols-file", required=True, type=Path)
    return parser.parse_args()


def _bounded_file(path: Path, name: str, maximum: int) -> bytes:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"{name} is not a file")
    size = resolved.stat().st_size
    if not 0 < size <= maximum:
        raise ValueError(f"{name} is outside the byte boundary")
    payload = resolved.read_bytes()
    if len(payload) != size:
        raise OSError(f"{name} changed while being read")
    return payload


def main() -> int:
    arguments = _arguments()
    try:
        export_paths = tuple(path.expanduser().resolve() for path in arguments.export_file)
        if (
            not 2 <= len(export_paths) <= MAX_CAPTURE_SHARDS
            or len(export_paths) != len(set(export_paths))
        ):
            raise ValueError("export files must be 2-100 distinct paths")
        symbol_bytes = _bounded_file(
            arguments.symbols_file,
            "symbols file",
            1_000_000,
        )
        symbols_text = symbol_bytes.decode("utf-8")
        if "\x00" in symbols_text:
            raise ValueError("symbols file must not contain NUL bytes")
        symbol_lines = symbols_text.splitlines()
        if not symbol_lines or any(
            not line or line != line.strip() for line in symbol_lines
        ):
            raise ValueError("symbols file must contain canonical nonblank lines")
        expected_symbols = tuple(symbol_lines)
        if len(expected_symbols) > MAX_CAPTURE_SYMBOLS:
            raise ValueError("symbols file exceeds the symbol boundary")
        payloads: list[bytes] = []
        total_bytes = 0
        for ordinal, path in enumerate(export_paths):
            payload = _bounded_file(path, f"export file {ordinal}", MAX_SOURCE_BYTES)
            total_bytes += len(payload)
            if total_bytes > MAX_CAPTURE_BYTES:
                raise ValueError("export files exceed the aggregate byte boundary")
            payloads.append(payload)
        manifest = assemble_norgate_sharded_capture_manifest(
            payloads,
            expected_symbols=expected_symbols,
        )
    except (OSError, UnicodeError, TypeError, ValueError, RuntimeError) as error:
        print(f"Norgate sharded capture failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(manifest.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
