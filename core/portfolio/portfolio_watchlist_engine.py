from __future__ import annotations

"""Rank auditable research that is not yet safe for a portfolio position."""

from typing import Any

from core.portfolio.portfolio_engine import PortfolioEngine


class PortfolioWatchlistEngine:
    """Keep research candidates visible without weakening holding safeguards."""

    @staticmethod
    def number(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def classify(cls, item: dict[str, Any]) -> dict[str, Any]:
        """Classify one record as holding, watchlist, or research-required."""
        if item.get("research_status") != "COMPLETE":
            return {
                "status": "RESEARCH_REQUIRED",
                "reason": "Research has not completed successfully.",
                "priority": None,
            }

        if not PortfolioEngine.audit_clears(item):
            return {
                "status": "RESEARCH_REQUIRED",
                "reason": "Evidence audit must clear before this company can be monitored.",
                "priority": None,
            }

        master = item.get("master_decision") or {}
        recommendation = str(master.get("portfolio_recommendation") or "").upper()
        if master.get("status") == "COMPLETE" and recommendation == "WATCHLIST":
            return {
                "status": "WATCHLIST",
                "reason": "Master portfolio decision requires further evidence or a stronger opportunity before allocation.",
                "priority": cls.number(master.get("opportunity_score")) or cls.number(master.get("conviction_score")),
            }

        score = PortfolioEngine.candidate_score(item)
        if score is None:
            return {
                "status": "RESEARCH_REQUIRED",
                "reason": (
                    PortfolioEngine.eligibility_reason(item)
                    or "Investment-case score is unavailable."
                ),
                "priority": None,
            }

        if score >= PortfolioEngine.MIN_PROTOTYPE_CONVICTION:
            return {
                "status": "PORTFOLIO_ELIGIBLE",
                "reason": "Clears all portfolio safety gates.",
                "priority": score,
            }

        return {
            "status": "WATCHLIST",
                "reason": (
                "Opportunity score is below the "
                f"{PortfolioEngine.MIN_PROTOTYPE_CONVICTION:.0f} prototype portfolio threshold."
            ),
            "priority": score,
        }

    @classmethod
    def build(cls, results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        eligible = []
        watchlist = []
        research_required = []

        for item in results:
            classification = cls.classify(item)
            enriched = dict(item)
            enriched["watchlist_status"] = classification["status"]
            enriched["watchlist_reason"] = classification["reason"]
            enriched["watchlist_priority"] = classification["priority"]

            if classification["status"] == "PORTFOLIO_ELIGIBLE":
                eligible.append(enriched)
            elif classification["status"] == "WATCHLIST":
                watchlist.append(enriched)
            else:
                research_required.append(enriched)

        watchlist.sort(
            key=lambda item: item.get("watchlist_priority") or -1,
            reverse=True,
        )
        return {
            "portfolio_eligible": eligible,
            "watchlist": watchlist,
            "research_required": research_required,
        }
