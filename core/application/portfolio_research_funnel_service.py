from __future__ import annotations

"""A broad, cheap screen ahead of full multi-bot company research.

Deep research is deliberately expensive.  This service creates an auditable
shortlist from market-liquidity and risk snapshots, preserves sector breadth,
then hands only the highest-priority candidates to the full research queue.
It does not treat a screen score as an investment decision.
"""

import math
from collections import defaultdict
from typing import Any, Callable


class PortfolioResearchFunnelService:
    VERSION = "1.0-sector-balanced-screen"
    DEFAULT_SHORTLIST_SIZE = 120
    MIN_AVERAGE_DOLLAR_VOLUME = 5_000_000.0

    @staticmethod
    def number(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def screen_score(cls, snapshot: dict[str, Any]) -> float | None:
        """Return a 0–100 research-priority score, not a buy recommendation."""

        liquidity = cls.number(snapshot.get("average_dollar_volume"))
        volatility = cls.number(snapshot.get("annualised_volatility"))
        momentum = cls.number(snapshot.get("six_month_return"))
        price = cls.number(snapshot.get("current_price"))
        if price is None or price <= 0 or liquidity is None or liquidity < cls.MIN_AVERAGE_DOLLAR_VOLUME:
            return None

        liquidity_score = min(35.0, max(0.0, (math.log10(liquidity) - 6.0) * 14.0))
        momentum_score = min(30.0, max(0.0, 15.0 + ((momentum or 0.0) * 50.0)))
        volatility_score = 20.0 if volatility is None else min(25.0, max(0.0, 25.0 - (volatility * 45.0)))
        completeness_score = 10.0 if snapshot.get("source_count", 0) >= 2 else 4.0
        return round(min(100.0, liquidity_score + momentum_score + volatility_score + completeness_score), 2)

    @classmethod
    def screen(
        cls,
        companies: list[dict[str, Any]],
        snapshot_provider: Callable[[str], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        results = []
        for company in companies:
            ticker = str(company.get("ticker") or "").upper()
            if not ticker:
                continue
            try:
                snapshot = snapshot_provider(ticker) or {}
            except Exception as exc:
                snapshot = {"screen_error": f"{type(exc).__name__}: {exc}"}
            priority = cls.screen_score(snapshot)
            results.append(
                {
                    **company,
                    "ticker": ticker,
                    "screen_status": "SCREENED" if priority is not None else "RESEARCH_DEFERRED",
                    "research_priority": priority,
                    "market_snapshot": snapshot,
                }
            )
        return results

    @classmethod
    def sector_balanced_shortlist(
        cls,
        screened: list[dict[str, Any]],
        size: int = DEFAULT_SHORTLIST_SIZE,
    ) -> list[dict[str, Any]]:
        """Select high-priority candidates without silently concentrating sectors."""

        by_sector: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in screened:
            if item.get("screen_status") != "SCREENED":
                continue
            by_sector[str(item.get("sector") or "Unknown")].append(item)

        for items in by_sector.values():
            items.sort(
                key=lambda item: (
                    -(cls.number(item.get("research_priority")) or -1.0),
                    str(item.get("ticker") or ""),
                )
            )

        selected: list[dict[str, Any]] = []
        index = 0
        sectors = sorted(by_sector)
        while len(selected) < max(0, int(size)):
            added = False
            for sector in sectors:
                items = by_sector[sector]
                if index >= len(items):
                    continue
                selected.append(items[index])
                added = True
                if len(selected) >= size:
                    break
            if not added:
                break
            index += 1
        return selected

    @classmethod
    def research_queue(
        cls,
        companies: list[dict[str, Any]],
        snapshot_provider: Callable[[str], dict[str, Any]],
        size: int = DEFAULT_SHORTLIST_SIZE,
    ) -> dict[str, Any]:
        screened = cls.screen(companies, snapshot_provider)
        shortlist = cls.sector_balanced_shortlist(screened, size=size)
        return {
            "version": cls.VERSION,
            "universe_count": len(companies),
            "screened_count": sum(item.get("screen_status") == "SCREENED" for item in screened),
            "deferred_count": sum(item.get("screen_status") != "SCREENED" for item in screened),
            "shortlist": shortlist,
            "screened": screened,
            "note": "This screen prioritises research coverage; a company must still complete full research, evidence audit and master decision before allocation.",
        }
