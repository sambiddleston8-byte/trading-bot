#!/usr/bin/env python3
"""Stage exact local Norgate identity-catalog bytes without admitting them."""

from __future__ import annotations

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
    parser.add_argument("--expected-source-sha256", required=True)
    return parser.parse_args()


def _provider_file_outside_repository(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise ValueError("provider catalog must be read outside the repository")
    return resolved


def _canonical_sha256(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("expected source SHA-256 must be 64 lowercase hex characters")
    return value


def main() -> int:
    arguments = _arguments()
    try:
        path = _provider_file_outside_repository(arguments.catalog_file)
        expected_source_sha256 = _canonical_sha256(
            arguments.expected_source_sha256
        )
        if not path.is_file():
            raise ValueError("catalog file is not a file")
        size = path.stat().st_size
        if not 0 < size <= MAX_UNIVERSE_CATALOG_BYTES:
            raise ValueError("catalog file is outside the byte boundary")
        payload = path.read_bytes()
        if len(payload) != size:
            raise OSError("catalog file changed while being read")
        source_payload_sha256 = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(source_payload_sha256, expected_source_sha256):
            raise ValueError("catalog file does not match expected source SHA-256")
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
