"""Inert operator-requested capture specification; grants no provider authority."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from core.research.conservative_baseline_campaign_v2_revision_2_proposal import (
    ACQUISITION_END,
    ACQUISITION_START,
)


def capture_specification() -> dict[str, Any]:
    material = {
        "schema_version": "campaign-v2r2-capture-spec-v1",
        "symbols": ["AAPL", "MSFT", "SPY"],
        "requested_window": {"start": "2024-09-01", "end": "2025-07-31"},
        "registered_window": {"start": ACQUISITION_START, "end": ACQUISITION_END},
        "datasets": [
            {"name": "DAILY_BARS", "path_template": "/v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}", "adjusted": False},
            {"name": "DIVIDENDS", "path": "/stocks/v1/dividends", "event_date": "ex_dividend_date"},
            {"name": "STOCK_SPLITS", "path": "/stocks/v1/splits", "event_date": "execution_date"},
        ],
        "mode": "SYNTHETIC_FIXTURES_ONLY",
        "block_reasons": [
            "REQUESTED_START_DIFFERS_FROM_REGISTERED_REVISION_2_WINDOW",
            "ACCOUNT_ENDPOINT_ENTITLEMENT_UNRESOLVED",
            "NO_EFFECTIVE_CAPTURE_ACTIVATION_RECORD",
        ],
        "provider_use_authorized": False,
        "provider_request_allowed": False,
        "credential_request_allowed": False,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {**material, "specification_sha256": hashlib.sha256(encoded).hexdigest()}
