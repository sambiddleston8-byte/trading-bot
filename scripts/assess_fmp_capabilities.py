from __future__ import annotations

"""Read-only, secret-safe FMP account capability probe."""

import json
from pathlib import Path
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.data_sources.optional_provider_sources import (
    FinancialModelingPrepSource,
    OptionalProviderError,
)


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
    payload = result.get("payload")
    if isinstance(payload, list):
        fields = sorted(payload[0].keys()) if payload and isinstance(payload[0], dict) else []
        records = len(payload)
    elif isinstance(payload, dict):
        fields = sorted(payload.keys())
        records = 1
    else:
        fields, records = [], 0
    return {
        "capability": name,
        "status": "AVAILABLE" if records else "EMPTY_RESPONSE",
        "sample_record_count": records,
        "sample_field_names": fields,
    }


def assess(source: FinancialModelingPrepSource | None = None) -> dict[str, Any]:
    source = source or FinancialModelingPrepSource()
    checks = [
        _probe("HISTORICAL_SP500_MEMBERSHIP_CHANGES", source.historical_sp500_constituent_changes),
        _probe("HISTORICAL_NASDAQ_MEMBERSHIP_CHANGES", source.historical_nasdaq_constituent_changes),
        _probe("DELISTED_SECURITIES", lambda: source.delisted_companies(limit=5)),
        _probe("POINT_IN_TIME_AS_REPORTED_FINANCIALS", lambda: source.as_reported_financials("AAPL")),
        _probe(
            "DIVIDEND_ADJUSTED_DAILY_PRICES",
            lambda: source.dividend_adjusted_prices(
                "AAPL", start="2024-01-01", end="2024-01-10"
            ),
        ),
        _probe("STOCK_SPLITS", lambda: source.splits("AAPL")),
        _probe("SYMBOL_CHANGES", source.symbol_changes),
    ]
    by_name = {item["capability"]: item["status"] for item in checks}
    critical = {
        "HISTORICAL_SP500_MEMBERSHIP_CHANGES",
        "HISTORICAL_NASDAQ_MEMBERSHIP_CHANGES",
        "DELISTED_SECURITIES",
        "DIVIDEND_ADJUSTED_DAILY_PRICES",
        "SYMBOL_CHANGES",
    }
    passed = all(by_name.get(name) == "AVAILABLE" for name in critical)
    return {
        "assessment_version": "fmp-secret-safe-capability-probe-v1",
        "provider": "Financial Modeling Prep",
        "status": "CANDIDATE_CAPABILITIES_AVAILABLE" if passed else "NOT_REPLAY_QUALIFIED",
        "checks": checks,
        "critical_probe_passed": passed,
        "provider_qualified": False,
        "dataset_admitted": False,
        "replay_executed": False,
        "performance_claim_allowed": False,
        "live_trading_enabled": False,
        "disclaimer": (
            "Availability is not semantic qualification. Terms, coverage, corrections, "
            "terminal outcomes, stable identifiers and representative samples still require review."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(assess(), indent=2, sort_keys=True))
