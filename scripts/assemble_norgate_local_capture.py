#!/usr/bin/env python3
from __future__ import annotations

"""Assemble exact local Norgate export shards into a non-authoritative manifest."""

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.orchestration.norgate_local_export import (
    MAX_CAPTURE_BYTES,
    MAX_CAPTURE_SHARDS,
    MAX_SOURCE_BYTES,
    MAX_UNIVERSE_CATALOG_BYTES,
    NorgateLocalExportSource,
    assemble_norgate_sharded_capture_manifest,
    stage_norgate_local_universe_catalog,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bind same-vintage local Norgate export shards to one exact staged "
            "catalog; never authenticates semantics, admits, replays, or trades."
        )
    )
    parser.add_argument("--export-file", action="append", required=True, type=Path)
    parser.add_argument("--catalog-file", required=True, type=Path)
    parser.add_argument("--expected-catalog-source-sha256", required=True)
    return parser.parse_args()


def _bounded_file(path: Path, name: str, maximum: int) -> bytes:
    resolved = path.expanduser().resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise ValueError(f"{name} must remain outside the repository")
    if not resolved.is_file():
        raise ValueError(f"{name} is not a file")
    size = resolved.stat().st_size
    if not 0 < size <= maximum:
        raise ValueError(f"{name} is outside the byte boundary")
    payload = resolved.read_bytes()
    if len(payload) != size:
        raise OSError(f"{name} changed while being read")
    return payload


def _canonical_sha256(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("catalog source SHA-256 must be 64 lowercase hex characters")
    return value


def main() -> int:
    arguments = _arguments()
    try:
        export_paths = tuple(path.expanduser().resolve() for path in arguments.export_file)
        if (
            not 2 <= len(export_paths) <= MAX_CAPTURE_SHARDS
            or len(export_paths) != len(set(export_paths))
        ):
            raise ValueError("export files must be 2-100 distinct paths")
        catalog_payload = _bounded_file(
            arguments.catalog_file,
            "catalog file",
            MAX_UNIVERSE_CATALOG_BYTES,
        )
        expected_catalog_sha256 = _canonical_sha256(
            arguments.expected_catalog_source_sha256
        )
        if not hmac.compare_digest(
            hashlib.sha256(catalog_payload).hexdigest(),
            expected_catalog_sha256,
        ):
            raise ValueError("catalog file does not match expected source SHA-256")
        catalog_evidence = stage_norgate_local_universe_catalog(
            NorgateLocalExportSource(
                retrieved_at=datetime.now(timezone.utc),
                payload_bytes=catalog_payload,
                receipt_timestamp_basis="SYSTEM_CLOCK_AT_FILE_READ_UNQUALIFIED",
            )
        )
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
            catalog_evidence=catalog_evidence,
        )
    except (OSError, UnicodeError, TypeError, ValueError, RuntimeError) as error:
        print(f"Norgate sharded capture failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(manifest.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
