from __future__ import annotations

"""Expose optional portfolio data providers without exposing credentials."""

from core.data_sources.optional_provider_sources import (
    AlphaVantageSource,
    FinancialModelingPrepSource,
    FREDSource,
    PolygonSource,
)


class PortfolioDataProviderRegistry:
    PROVIDERS = (
        ("transcripts", AlphaVantageSource),
        ("independent_estimates", FinancialModelingPrepSource),
        ("market_history", PolygonSource),
        ("macro", FREDSource),
    )

    @classmethod
    def status(cls):
        return {
            role: {
                "source": provider.SOURCE_NAME,
                "configured": provider().configured,
                "required_environment_variable": provider.KEY_ENV,
            }
            for role, provider in cls.PROVIDERS
        }
