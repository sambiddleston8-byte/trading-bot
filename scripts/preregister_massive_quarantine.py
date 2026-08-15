#!/usr/bin/env python3
from __future__ import annotations

"""Append one immutable Massive quarantine plan from a local JSON definition."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.orchestration.historical_quarantine_preregistration import (
    HistoricalQuarantinePreregistrationLedger,
)


MAX_DEFINITION_BYTES = 200_000
DEFINITION_FIELDS = {
    "registered_by",
    "acquisition_start",
    "acquisition_end",
    "splits",
    "strategy_entrypoint",
    "strategy_source_path",
    "strategy_version",
    "parameter_space",
    "evaluation_protocol",
    "entitlement_metadata",
}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preregister a Massive quarantine plan before provider access."
    )
    parser.add_argument("--definition", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    return parser.parse_args()


def _definition(path: Path) -> dict:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or not 0 < resolved.stat().st_size <= MAX_DEFINITION_BYTES:
        raise ValueError("definition file is missing or outside the size boundary")
    try:
        value = json.loads(resolved.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("definition file must contain one UTF-8 JSON object") from error
    if not isinstance(value, dict) or set(value) != DEFINITION_FIELDS:
        raise ValueError("definition file has missing or unsupported fields")
    return value


def main() -> int:
    arguments = _arguments()
    try:
        value = _definition(arguments.definition)
        record = HistoricalQuarantinePreregistrationLedger(
            arguments.ledger.expanduser().resolve(),
            repository_root=ROOT,
        ).preregister(**value)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Massive quarantine preregistration failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "preregistration_id": record["preregistration_id"],
                "record_hash": record["record_hash"],
                "registered_at": record["registered_at"],
                "data_access_not_before": record["data_access_not_before"],
                "target_basket": record["target_basket"],
                "acquisition_start": record["acquisition_start"],
                "acquisition_end": record["acquisition_end"],
                "quarantine_only": True,
                "dataset_admitted": False,
                "replay_executed": False,
                "broker_connection_allowed": False,
                "orders_submitted": False,
                "live_trading_enabled": False,
            },
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
