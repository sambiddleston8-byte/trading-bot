from __future__ import annotations

"""Optional live-data providers for the portfolio research pipeline.

Keys are read from the environment only.  A missing key produces a clear
``NOT_CONFIGURED`` result rather than a failed research run or a fabricated
value.  Primary SEC filings remain the authoritative source for reported
financial statements; these providers add independent estimates, transcripts,
market history and macro context.
"""

import os
from datetime import datetime, timezone
from typing import Any

import requests

from core.data_sources.provider_configuration import ProviderConfiguration


class OptionalProviderError(RuntimeError):
    """A provider failure that intentionally contains no request URL or key."""


class OptionalHTTPSource:
    KEY_ENV = ""
    SOURCE_NAME = "Optional provider"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        ProviderConfiguration.load_local_environment()
        return bool(os.getenv(self.KEY_ENV))

    def unavailable(self) -> dict[str, Any]:
        return {
            "status": "NOT_CONFIGURED",
            "source": self.SOURCE_NAME,
            "required_environment_variable": self.KEY_ENV,
        }

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            return self.unavailable()
        try:
            response = self.session.get(
                url,
                params=params,
                headers=headers,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise OptionalProviderError(
                f"{self.SOURCE_NAME} could not be reached."
            ) from exc

        if not response.ok:
            if response.status_code in {401, 402, 403}:
                raise OptionalProviderError(
                    f"{self.SOURCE_NAME} capability is unavailable for the configured account "
                    f"(HTTP {response.status_code})."
                )
            raise OptionalProviderError(
                f"{self.SOURCE_NAME} rejected the request (HTTP {response.status_code})."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise OptionalProviderError(
                f"{self.SOURCE_NAME} returned an unreadable response."
            ) from exc

        # Some market-data providers report an invalid key or a quota problem
        # in a successful HTTP response.  Do not mistake that response for
        # usable evidence.
        if isinstance(payload, dict) and any(
            key in payload
            for key in ("Error Message", "Information", "Note")
        ):
            raise OptionalProviderError(
                f"{self.SOURCE_NAME} rejected the request or quota."
            )

        return {
            "status": "COMPLETE",
            "source": self.SOURCE_NAME,
            "payload": payload,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }


class AlphaVantageSource(OptionalHTTPSource):
    KEY_ENV = "ALPHAVANTAGE_API_KEY"
    SOURCE_NAME = "Alpha Vantage"
    BASE_URL = "https://www.alphavantage.co/query"

    def query(self, function: str, symbol: str, **parameters: Any) -> dict[str, Any]:
        params = {
            "function": function,
            "symbol": str(symbol).upper(),
            "apikey": os.getenv(self.KEY_ENV),
            **parameters,
        }
        return self.get_json(self.BASE_URL, params=params)

    def earnings_transcript(self, symbol: str, quarter: str) -> dict[str, Any]:
        return self.query("EARNINGS_CALL_TRANSCRIPT", symbol, quarter=quarter)

    def income_statement(self, symbol: str) -> dict[str, Any]:
        return self.query("INCOME_STATEMENT", symbol)


class FinancialModelingPrepSource(OptionalHTTPSource):
    KEY_ENV = "FMP_API_KEY"
    SOURCE_NAME = "Financial Modeling Prep"
    BASE_URL = "https://financialmodelingprep.com/stable"

    def authenticated_headers(self) -> dict[str, str]:
        # FMP accepts either a header or query parameter.  The header keeps the
        # credential out of URLs, proxy logs and HTTP exception messages.
        return {"apikey": str(os.getenv(self.KEY_ENV) or "")}

    def query(
        self,
        endpoint: str,
        symbol: str | None = None,
        **parameters: Any,
    ) -> dict[str, Any]:
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        params = dict(parameters)
        if symbol is not None:
            params["symbol"] = str(symbol).upper()
        return self.get_json(
            url,
            params=params,
            headers=self.authenticated_headers(),
        )

    def analyst_estimates(self, symbol: str) -> dict[str, Any]:
        return self.query("analyst-estimates", symbol, period="annual", page=0, limit=10)

    def as_reported_financials(self, symbol: str) -> dict[str, Any]:
        return self.query("financial-statement-full-as-reported", symbol)

    def ratings_snapshot(self, symbol: str) -> dict[str, Any]:
        """Return the provider's current company-rating snapshot as evidence.

        This is deliberately supplementary: it is useful for corroborating
        quality signals, but never replaces a filed figure or an audit gate.
        """
        return self.query("ratings-snapshot", symbol)

    def price_target_consensus(self, symbol: str) -> dict[str, Any]:
        """Return external analyst expectations for comparison only."""
        return self.query("price-target-consensus", symbol)

    def historical_sp500_constituent_changes(self) -> dict[str, Any]:
        return self.query("historical-sp500-constituent")

    def historical_nasdaq_constituent_changes(self) -> dict[str, Any]:
        return self.query("historical-nasdaq-constituent")

    def delisted_companies(self, *, page: int = 0, limit: int = 100) -> dict[str, Any]:
        return self.query("delisted-companies", page=int(page), limit=int(limit))

    def dividend_adjusted_prices(
        self, symbol: str, *, start: str, end: str
    ) -> dict[str, Any]:
        return self.query(
            "historical-price-eod/dividend-adjusted",
            symbol,
            **{"from": str(start), "to": str(end)},
        )

    def splits(self, symbol: str) -> dict[str, Any]:
        return self.query("splits", symbol)

    def symbol_changes(self) -> dict[str, Any]:
        return self.query("symbol-change")


class PolygonSource(OptionalHTTPSource):
    KEY_ENV = "POLYGON_API_KEY"
    # Polygon's market-data service is now called Massive.  The project keeps
    # the existing environment-variable and class names so existing local
    # setups continue to work, but sends new requests to the current API host.
    SOURCE_NAME = "Massive (formerly Polygon)"
    BASE_URL = "https://api.massive.com"

    def authenticated_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {os.getenv(self.KEY_ENV)}",
        }

    def snapshot(self, symbol: str) -> dict[str, Any]:
        url = f"{self.BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers/{str(symbol).upper()}"
        return self.get_json(url, headers=self.authenticated_headers())

    def daily_bars(self, symbol: str, start: str, end: str) -> dict[str, Any]:
        url = f"{self.BASE_URL}/v2/aggs/ticker/{str(symbol).upper()}/range/1/day/{start}/{end}"
        return self.get_json(
            url,
            params={"adjusted": "true"},
            headers=self.authenticated_headers(),
        )

    def company_news(self, symbol: str, limit: int = 10) -> dict[str, Any]:
        """Return recent company news from Massive's reference-news feed."""
        return self.get_json(
            f"{self.BASE_URL}/v2/reference/news",
            params={"ticker": str(symbol).upper(), "limit": int(limit)},
            headers=self.authenticated_headers(),
        )


class FREDSource(OptionalHTTPSource):
    KEY_ENV = "FRED_API_KEY"
    SOURCE_NAME = "FRED"
    BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

    def observations(self, series_id: str, **parameters: Any) -> dict[str, Any]:
        params = {
            "series_id": series_id,
            "api_key": os.getenv(self.KEY_ENV),
            "file_type": "json",
            **parameters,
        }
        return self.get_json(self.BASE_URL, params=params)
