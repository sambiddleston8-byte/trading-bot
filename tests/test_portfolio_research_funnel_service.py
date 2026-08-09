from core.application.portfolio_research_funnel_service import (
    PortfolioResearchFunnelService,
)


def snapshot(liquidity, momentum=0.10, volatility=0.20):
    return {
        "current_price": 100.0,
        "average_dollar_volume": liquidity,
        "six_month_return": momentum,
        "annualised_volatility": volatility,
        "source_count": 2,
    }


def test_screen_rejects_illiquid_candidates_without_calling_them_bad_investments():
    assert PortfolioResearchFunnelService.screen_score(snapshot(1_000_000)) is None


def test_shortlist_is_priority_ranked_with_sector_breadth():
    companies = [
        {"ticker": "TECH1", "sector": "Information Technology"},
        {"ticker": "TECH2", "sector": "Information Technology"},
        {"ticker": "HEALTH", "sector": "Health Care"},
        {"ticker": "FIN", "sector": "Financials"},
    ]
    snapshots = {
        "TECH1": snapshot(100_000_000, momentum=0.40),
        "TECH2": snapshot(90_000_000, momentum=0.30),
        "HEALTH": snapshot(50_000_000, momentum=0.10),
        "FIN": snapshot(40_000_000, momentum=0.05),
    }

    result = PortfolioResearchFunnelService.research_queue(
        companies,
        snapshots.__getitem__,
        size=4,
    )

    assert result["screened_count"] == 4
    assert len(result["shortlist"]) == 4
    assert {item["sector"] for item in result["shortlist"][:3]} == {
        "Financials",
        "Health Care",
        "Information Technology",
    }


if __name__ == "__main__":
    test_screen_rejects_illiquid_candidates_without_calling_them_bad_investments()
    test_shortlist_is_priority_ranked_with_sector_breadth()
    print("PORTFOLIO RESEARCH FUNNEL TESTS PASSED")
