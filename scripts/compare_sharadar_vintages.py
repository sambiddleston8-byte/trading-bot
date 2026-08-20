#!/usr/bin/env python3
from __future__ import annotations

"""Seed or compare owner-local Sharadar foundation observations offline."""

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.orchestration.sharadar_quarantine import (
    ensure_foundation_baseline_observation,
)
from core.orchestration.sharadar_vintages import (
    persist_foundation_vintage_comparison,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description=(
            "Create an offline baseline observation from already-captured bytes, "
            "or compare two exact later-ordered observation hashes."
        )
    )
    value.add_argument("--seed-baseline", action="store_true")
    value.add_argument("--baseline")
    value.add_argument("--candidate")
    return value


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.seed_baseline:
            if arguments.baseline or arguments.candidate:
                raise ValueError("baseline seeding does not accept comparison hashes")
            observation = ensure_foundation_baseline_observation(ROOT)
            output = {
                "record_type": observation["record_type"],
                "observation_hash": observation["record_hash"],
                "origin": observation["origin"],
                "quarantine_only": observation["quarantine_only"],
                "dataset_admitted": observation["dataset_admitted"],
            }
        else:
            if not arguments.baseline or not arguments.candidate:
                raise ValueError("both --baseline and --candidate are required")
            comparison = persist_foundation_vintage_comparison(
                ROOT,
                baseline_observation_hash=arguments.baseline,
                candidate_observation_hash=arguments.candidate,
            )
            output = {
                "status": comparison["status"],
                "comparison_sha256": comparison["comparison_sha256"],
                "observation_interval_microseconds": comparison[
                    "observation_interval_microseconds"
                ],
                "historical_row_churn_count": comparison[
                    "historical_row_churn_count"
                ],
                "undated_ticker_master_churn_count": comparison[
                    "undated_ticker_master_churn_count"
                ],
                "historical_availability_qualified": comparison[
                    "historical_availability_qualified"
                ],
                "dataset_admitted": comparison["dataset_admitted"],
            }
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    except (OSError, TypeError, ValueError) as error:
        print(f"Sharadar vintage comparison failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
