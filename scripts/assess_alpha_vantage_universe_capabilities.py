from __future__ import annotations

"""Read-only, secret-safe Alpha Vantage historical-universe capability probe."""

import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.data_sources.optional_provider_sources import (
    AlphaVantageSource,
    OptionalProviderError,
)


def _probe(source: AlphaVantageSource, *, state: str) -> dict[str, Any]:
    capability = f"HISTORICAL_{state.upper()}_US_LISTINGS"
    try:
        result = source.listing_status_summary(as_of="2020-01-02", state=state)
    except OptionalProviderError as error:
        return {"capability": capability, "status": "UNAVAILABLE", "reason": str(error)}
    if result.get("status") != "COMPLETE":
        return {
            "capability": capability,
            "status": result.get("status", "UNAVAILABLE"),
            "reason": "Provider capability is not configured.",
        }
    return {
        "capability": capability,
        "status": "AVAILABLE" if result["sample_record_count"] else "EMPTY_RESPONSE",
        "as_of": result["as_of"],
        "sample_record_count": result["sample_record_count"],
        "sample_field_names": result["sample_field_names"],
    }


def assess(source: AlphaVantageSource | None = None) -> dict[str, Any]:
    source = source or AlphaVantageSource()
    checks = [_probe(source, state=state) for state in ("active", "delisted")]
    passed = all(item["status"] == "AVAILABLE" for item in checks)
    return {
        "assessment_version": "alpha-vantage-universe-capability-probe-v1",
        "provider": "Alpha Vantage",
        "status": "PARTIAL_UNIVERSE_CAPABILITY_AVAILABLE" if passed else "NOT_AVAILABLE",
        "checks": checks,
        "historical_listing_probe_passed": passed,
        "historical_index_membership_provided": False,
        "provider_qualified": False,
        "dataset_admitted": False,
        "replay_executed": False,
        "performance_claim_allowed": False,
        "live_trading_enabled": False,
        "disclaimer": (
            "Listing availability is only one survivorship-control input. It does not prove "
            "historical index membership, complete terminal outcomes, terms, corrections, "
            "stable identifiers or semantic dataset quality."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(assess(), indent=2, sort_keys=True))
