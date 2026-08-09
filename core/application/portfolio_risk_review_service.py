from __future__ import annotations

from typing import Any

from core.risk_engine import RiskEngine


class PortfolioRiskReviewService:
    """Run the established risk engine against the new portfolio schema."""

    DEFAULT_HIGH_RISK_THRESHOLD = 40.0

    @staticmethod
    def number(value: Any, default: float | None = None) -> float | None:
        try:
            return default if value is None else float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def legacy_portfolio(cls, portfolio: dict[str, Any]) -> tuple[dict[str, Any], dict[str, float]]:
        holdings = []
        volatility_data: dict[str, float] = {}

        for holding in portfolio.get("holdings", []):
            signals = holding.get("market_signals") or {}
            ticker = str(holding.get("ticker") or "UNKNOWN").upper()
            risk_score = cls.number(signals.get("risk_score"), 50.0)
            volatility = cls.number(signals.get("annualised_volatility"))

            holdings.append(
                {
                    "Ticker": ticker,
                    "Sector": holding.get("sector") or "Unknown",
                    "Weight %": (cls.number(holding.get("weight"), 0.0) or 0.0) * 100,
                    "Risk Score": risk_score,
                }
            )

            if volatility is not None:
                volatility_data[ticker] = volatility

        return (
            {
                "Holdings": holdings,
                "Cash Weight %": (cls.number(portfolio.get("cash_weight"), 0.0) or 0.0) * 100,
            },
            volatility_data,
        )

    @classmethod
    def review(
        cls,
        portfolio: dict[str, Any],
        risk_engine_class: type[RiskEngine] = RiskEngine,
    ) -> dict[str, Any]:
        constraints = portfolio.get("constraints") or {}
        legacy_portfolio, volatility_data = cls.legacy_portfolio(portfolio)

        engine = risk_engine_class(
            max_position_weight=cls.number(constraints.get("max_weight"), 0.15),
            max_sector_weight=cls.number(constraints.get("max_sector_weight"), 0.50),
            minimum_cash_weight=cls.number(constraints.get("cash_weight"), 0.0),
            high_risk_threshold=cls.DEFAULT_HIGH_RISK_THRESHOLD,
        )

        report = engine.review(
            legacy_portfolio,
            volatility_data=volatility_data or None,
        )
        report["method"] = "POSITION_SECTOR_CASH_RISK_AND_VOLATILITY_REVIEW"
        report["volatility_coverage"] = {
            "covered_holdings": len(volatility_data),
            "total_holdings": len(legacy_portfolio["Holdings"]),
            "note": (
                "Portfolio volatility is an independent-position estimate; "
                "correlations are not yet modelled."
            ),
        }
        return report
