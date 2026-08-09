from core.portfolio.portfolio_watchlist_engine import PortfolioWatchlistEngine
from core.research.master_portfolio_decision_engine import (
    MasterPortfolioDecisionEngine,
)


def candidate(**overrides):
    record = {
        "ticker": "TEST",
        "research_status": "COMPLETE",
        "investment_case_score": 72,
        "decision": "BUY",
        "expected_return": 0.30,
        "current_price": 100,
        "base_intrinsic_value": 130,
        "audit": {"status": "PASS"},
        "thesis": {"thesis_survives": True},
        "market_signals": {"technical_score": 70, "risk_score": 70},
    }
    record.update(overrides)
    record.setdefault(
        "master_decision",
        MasterPortfolioDecisionEngine.evaluate(record),
    )
    return record


def test_audit_cleared_but_not_investable_candidate_enters_watchlist():
    result = PortfolioWatchlistEngine.classify(
        candidate(investment_case_score=30, expected_return=0.10)
    )
    assert result["status"] == "WATCHLIST"
    assert "Master portfolio decision" in result["reason"]


def test_failed_audit_requires_research_instead_of_watchlist():
    result = PortfolioWatchlistEngine.classify(candidate(audit={"status": "FAIL"}))
    assert result["status"] == "RESEARCH_REQUIRED"


def test_watchlist_is_ranked_by_priority():
    low = candidate(ticker="LOW", investment_case_score=20, expected_return=0.10)
    high = candidate(ticker="HIGH", investment_case_score=30, expected_return=0.10)
    result = PortfolioWatchlistEngine.build([low, high])
    assert [item["ticker"] for item in result["watchlist"]] == ["HIGH", "LOW"]


if __name__ == "__main__":
    test_audit_cleared_but_not_investable_candidate_enters_watchlist()
    test_failed_audit_requires_research_instead_of_watchlist()
    test_watchlist_is_ranked_by_priority()
    print("PORTFOLIO WATCHLIST TESTS PASSED")
