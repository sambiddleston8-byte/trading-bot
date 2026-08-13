from __future__ import annotations

"""Read-only, secret-safe EODHD historical-universe capability probe."""

import json
from pathlib import Path
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.data_sources.optional_provider_sources import EODHDSource, OptionalProviderError


def _probe(name: str, call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        result = call()
    except OptionalProviderError as error:
        return {"capability": name, "status": "UNAVAILABLE", "reason": str(error)}
    if result.get("status") != "COMPLETE":
        return {
            "capability": name,
            "status": result.get("status", "UNAVAILABLE"),
            "reason": "Provider capability is not configured.",
        }
    return {
        "capability": name,
        "status": "AVAILABLE" if result["sample_record_count"] else "EMPTY_RESPONSE",
        "sample_record_count": result["sample_record_count"],
        "sample_field_names": result["sample_field_names"],
    }


def assess(source: EODHDSource | None = None) -> dict[str, Any]:
    source = source or EODHDSource()
    checks = [
        _probe(
            "HISTORICAL_SP500_MEMBERSHIP",
            source.historical_sp500_membership_summary,
        ),
        _probe("DELISTED_SYMBOL_EOD_HISTORY", source.delisted_symbol_eod_capability),
    ]
    passed = all(item["status"] == "AVAILABLE" for item in checks)
    return {
        "assessment_version": "eodhd-capability-probe-v1",
        "provider": "EODHD",
        "status": "CANDIDATE_CAPABILITIES_AVAILABLE" if passed else "NOT_AVAILABLE",
        "checks": checks,
        "capability_probe_passed": passed,
        "historical_nasdaq_membership_provided": False,
        "complete_terminal_outcomes_provided": False,
        "corrections_and_revisions_provided": False,
        "terms_approved": False,
        "provider_qualified": False,
        "dataset_admitted": False,
        "replay_executed": False,
        "performance_claim_allowed": False,
        "live_trading_enabled": False,
        "disclaimer": (
            "Availability is only a capability signal. It does not prove Nasdaq membership, "
            "complete terminal outcomes, corrections, acceptable terms, provider qualification, "
            "dataset admission, replay performance or live-trading authority."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(assess(), indent=2, sort_keys=True))
