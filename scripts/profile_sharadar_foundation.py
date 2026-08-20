#!/usr/bin/env python3
from __future__ import annotations

"""Build an offline, non-admitting profile of the frozen Sharadar archives."""

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.orchestration.sharadar_foundation import persist_foundation_profile


def main() -> int:
    try:
        profile = persist_foundation_profile(ROOT)
        print(
            json.dumps(
                {
                    "status": profile["status"],
                    "profile_sha256": profile["profile_sha256"],
                    "table_row_counts": {
                        table: details["row_count"]
                        for table, details in profile["tables"].items()
                    },
                    "stock_date_range": [
                        profile["tables"]["stocks"]["min_date"],
                        profile["tables"]["stocks"]["max_date"],
                    ],
                    "structural_identity_gap_count": profile[
                        "structural_identity_gap_count"
                    ],
                    "observed_stock_date_span_days": profile[
                        "observed_stock_date_span_days"
                    ],
                    "dataset_admitted": profile["dataset_admitted"],
                    "performance_claim_allowed": profile[
                        "performance_claim_allowed"
                    ],
                    "validation_access_authorized": profile[
                        "validation_access_authorized"
                    ],
                    "test_access_authorized": profile["test_access_authorized"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except (OSError, TypeError, ValueError) as error:
        print(f"Sharadar foundation profile failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
