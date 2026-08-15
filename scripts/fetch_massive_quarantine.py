#!/usr/bin/env python3
from __future__ import annotations

"""Fetch only a verified preregistered Massive plan into quarantine."""

import argparse
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.orchestration.historical_quarantine_preregistration import (
    HistoricalQuarantinePreregistrationLedger,
)
from core.orchestration.massive_historical_adapter import MassiveHistoricalSampleClient
from core.orchestration.massive_historical_quarantine import (
    MassiveHistoricalQuarantineFetcher,
    MassiveHistoricalQuarantineStore,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch a preregistered Massive plan into isolated raw quarantine storage."
    )
    parser.add_argument("--prereg-ledger", type=Path, required=True)
    parser.add_argument("--preregistration-id", required=True)
    parser.add_argument("--quarantine-root", type=Path, required=True)
    parser.add_argument(
        "--admitted-store-root",
        type=Path,
        required=True,
        action="append",
    )
    parser.add_argument("--api-key-file", type=Path)
    return parser.parse_args()


def _api_key(path: Path | None) -> str:
    environment_key = os.getenv("MASSIVE_API_KEY")
    if path is not None and environment_key:
        raise ValueError("use either an API-key environment variable or --api-key-file")
    if path is None:
        if not environment_key:
            raise ValueError("set MASSIVE_API_KEY or provide --api-key-file")
        value = environment_key
        if (
            value.strip() != value
            or not value
            or len(value) > 500
            or any(not 33 <= ord(character) <= 126 for character in value)
        ):
            raise ValueError("MASSIVE_API_KEY has an invalid format")
        return value
    resolved = path.expanduser().absolute()
    descriptor = os.open(
        resolved,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_nlink != 1
            or not 0 < details.st_size <= 502
        ):
            raise ValueError("API-key file must be an owner-only regular file")
        raw = os.read(descriptor, 503)
    finally:
        os.close(descriptor)
    try:
        value = raw.decode("ascii").rstrip("\r\n")
    except UnicodeDecodeError as error:
        raise ValueError("API-key file must contain ASCII text") from error
    if "\n" in value or "\r" in value:
        raise ValueError("API-key file must contain exactly one line")
    if not value or len(value) > 500 or any(
        not 33 <= ord(character) <= 126 for character in value
    ):
        raise ValueError("API-key file has an invalid key format")
    return value


def main() -> int:
    arguments = _arguments()
    try:
        ledger = HistoricalQuarantinePreregistrationLedger(
            arguments.prereg_ledger.expanduser().resolve(),
            repository_root=ROOT,
        )
        store = MassiveHistoricalQuarantineStore(
            arguments.quarantine_root.expanduser().resolve(),
            preregistration_ledger=ledger,
            admitted_store_roots=[
                path.expanduser().resolve() for path in arguments.admitted_store_root
            ],
        )
        result = MassiveHistoricalQuarantineFetcher(
            store=store,
            client=MassiveHistoricalSampleClient(_api_key(arguments.api_key_file)),
        ).fetch(arguments.preregistration_id)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Massive quarantine fetch failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
