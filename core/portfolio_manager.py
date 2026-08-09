"""Compatibility facade for the one supported portfolio-construction path.

The older DecisionEngine and PortfolioConstructor are retained as historical
experiments, but this public manager deliberately does not call them.  Every
prototype portfolio must use the evidence-audited research pipeline and the
Master Portfolio Decision Engine.
"""

from __future__ import annotations

from core.application.portfolio_construction_service import (
    PortfolioConstructionService,
)
from core.application.research_service import ResearchService
from core.research.research_contract import ResearchContract


class PortfolioManager:
    """Research named companies through the official pipeline, then construct."""

    def analyse_universe(self, symbols: list[str]) -> list[dict]:
        analyses = []
        for symbol in symbols:
            ticker = str(symbol).upper().strip()
            if not ticker:
                continue
            try:
                result = ResearchService.run(ticker)
                analyses.append(ResearchContract.from_pipeline_result(result))
            except Exception as exc:
                analyses.append(
                    {
                        "ticker": ticker,
                        "research_status": "ERROR",
                        "error": str(exc),
                    }
                )
        return analyses

    def construct_portfolio(
        self,
        symbols: list[str] | None = None,
        target_holdings: int = 8,
    ) -> dict:
        analyses = self.analyse_universe(symbols) if symbols else []
        construction = PortfolioConstructionService.construct(target_holdings)
        return {
            "analyses": analyses,
            "construction": construction,
            "portfolio": construction.get("portfolio"),
        }

    @staticmethod
    def print_portfolio(result: dict) -> None:
        construction = result.get("construction") or {}
        if construction.get("status") != "CONSTRUCTED":
            print(construction.get("reason", "Portfolio is not ready to construct."))
            return

        portfolio = construction["portfolio"]
        print("\nPROPOSED PORTFOLIO")
        for holding in portfolio.get("holdings", []):
            print(
                f"{holding.get('ticker'):<8} "
                f"{holding.get('weight', 0):>7.2%} "
                f"conviction {holding.get('portfolio_conviction', 0):.1f}"
            )
        print(f"Cash: {portfolio.get('cash_weight', 0):.2%}")
