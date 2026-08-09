from __future__ import annotations

"""Collect optional independent evidence without letting it bypass audit gates."""

from datetime import date, timedelta
from typing import Any

from core.data_quality.source_registry import SourceRegistry
from core.data_sources.optional_provider_sources import (
    AlphaVantageSource,
    FinancialModelingPrepSource,
    FREDSource,
    PolygonSource,
)


class SupplementalProviderEvidenceService:
    VERSION = "1.4-timestamped-provider-evidence"

    SOURCE_REGISTRY_KEYS = {
        "alpha_vantage": "ALPHA_VANTAGE",
        "financial_modeling_prep": "FMP",
        "massive": "POLYGON",
        "fred": "FRED",
    }

    # Multiple endpoints from the same provider provide broader coverage but
    # do not pretend to be independent corroboration of one another.
    EVIDENCE_FIELDS = {
        "alpha_vantage_income_statement": {
            "role": "independent_financials",
            "provider": "alpha_vantage",
            "company_specific": True,
        },
        "fmp_as_reported_financials": {
            "role": "financial_statement_cross_check",
            "provider": "financial_modeling_prep",
            "company_specific": True,
        },
        "fmp_analyst_estimates": {
            "role": "independent_estimates",
            "provider": "financial_modeling_prep",
            "company_specific": True,
        },
        "fmp_ratings_snapshot": {
            "role": "independent_financial_quality",
            "provider": "financial_modeling_prep",
            "company_specific": True,
        },
        "fmp_price_target_consensus": {
            "role": "analyst_expectations",
            "provider": "financial_modeling_prep",
            "company_specific": True,
        },
        "massive_market_history": {
            "role": "independent_market_history",
            "provider": "massive",
            "company_specific": True,
        },
        "massive_company_news": {
            "role": "independent_company_news",
            "provider": "massive",
            "company_specific": True,
        },
        "fred_ten_year_treasury": {
            "role": "macro_context",
            "provider": "fred",
            "company_specific": False,
        },
    }

    @staticmethod
    def safely(callable_) -> dict[str, Any]:
        try:
            return callable_()
        except Exception as exc:
            return {
                "status": "ERROR",
                "error_type": type(exc).__name__,
                # Do not persist exception text: HTTP clients can include
                # full request URLs, including query-string API keys.
                "error": "Provider request failed or was rejected.",
            }

    @classmethod
    def summary(cls, evidence: dict[str, Any]) -> dict[str, Any]:
        """Return source coverage without promoting raw provider data to fact.

        The summary improves transparency and evidence confidence only.  It
        never replaces SEC-reported figures, an audit result, or a valuation
        assumption until a separate reconciliation rule supports that step.
        """
        completed_roles = []
        failed_roles = []
        completed_evidence_types = []
        completed_providers = set()
        company_providers = set()
        provider_timestamps: dict[str, str | None] = {}
        for field, definition in cls.EVIDENCE_FIELDS.items():
            role = definition["role"]
            result = evidence.get(field)
            if isinstance(result, dict) and result.get("status") == "COMPLETE":
                completed_roles.append(role)
                completed_evidence_types.append(field)
                completed_providers.add(definition["provider"])
                provider = definition["provider"]
                timestamp = result.get("retrieved_at")
                existing = provider_timestamps.get(provider)
                # Each provider is queried several times. Preserve the most
                # recent timestamp we received without pretending a missing
                # timestamp is fresh.
                if isinstance(timestamp, str) and (existing is None or timestamp > existing):
                    provider_timestamps[provider] = timestamp
                elif provider not in provider_timestamps:
                    provider_timestamps[provider] = None
                if definition["company_specific"]:
                    company_providers.add(definition["provider"])
            else:
                failed_roles.append(role)

        freshness = {
            provider: SourceRegistry.freshness_status(
                cls.SOURCE_REGISTRY_KEYS[provider],
                timestamp,
            )
            for provider, timestamp in provider_timestamps.items()
        }
        fresh_providers = [
            provider
            for provider, result in freshness.items()
            if result.get("status") == "FRESH"
        ]
        stale_or_untimestamped = [
            provider
            for provider, result in freshness.items()
            if result.get("status") != "FRESH"
        ]

        return {
            "version": cls.VERSION,
            "status": "COMPLETE",
            "evidence_policy": "CONFIDENCE_ONLY_PENDING_FIELD_RECONCILIATION",
            "completed_roles": sorted(set(completed_roles)),
            "unavailable_roles": sorted(set(failed_roles)),
            "completed_evidence_types": completed_evidence_types,
            "completed_source_count": len(completed_providers),
            "completed_evidence_count": len(completed_evidence_types),
            "independent_company_source_count": len(company_providers),
            "provider_freshness": freshness,
            "fresh_provider_count": len(fresh_providers),
            "stale_or_untimestamped_providers": sorted(stale_or_untimestamped),
            "freshness_policy": "Provider data is supplementary only and must be timestamped; stale saved research requires a full refresh.",
        }

    @classmethod
    def collect(
        cls,
        ticker: str,
        *,
        alpha_vantage: AlphaVantageSource | None = None,
        fmp: FinancialModelingPrepSource | None = None,
        polygon: PolygonSource | None = None,
        fred: FREDSource | None = None,
    ) -> dict[str, Any]:
        """Fetch configured supplementary sources with isolated failures.

        Values are evidence for reconciliation and diagnostics only.  They do
        not overwrite SEC facts, valuation inputs or a portfolio decision.
        """

        alpha_vantage = alpha_vantage or AlphaVantageSource()
        fmp = fmp or FinancialModelingPrepSource()
        polygon = polygon or PolygonSource()
        fred = fred or FREDSource()

        end_date = date.today()
        start_date = end_date - timedelta(days=365)

        evidence = {
            "version": cls.VERSION,
            "ticker": str(ticker).upper(),
            "status": "COMPLETE",
            "evidence_policy": "SUPPLEMENTARY_ONLY_PENDING_RECONCILIATION",
            "alpha_vantage_income_statement": cls.safely(
                lambda: alpha_vantage.income_statement(ticker)
            ),
            "fmp_as_reported_financials": cls.safely(
                lambda: fmp.as_reported_financials(ticker)
            ),
            "fmp_analyst_estimates": cls.safely(
                lambda: fmp.analyst_estimates(ticker)
            ),
            "fmp_ratings_snapshot": cls.safely(
                lambda: fmp.ratings_snapshot(ticker)
            ),
            "fmp_price_target_consensus": cls.safely(
                lambda: fmp.price_target_consensus(ticker)
            ),
            "massive_market_history": cls.safely(
                lambda: polygon.daily_bars(
                    ticker,
                    start=start_date.isoformat(),
                    end=end_date.isoformat(),
                )
            ),
            "massive_company_news": cls.safely(
                lambda: polygon.company_news(ticker)
            ),
            "fred_ten_year_treasury": cls.safely(
                lambda: fred.observations("DGS10", limit=5, sort_order="desc")
            ),
        }
        evidence["summary"] = cls.summary(evidence)
        return evidence
