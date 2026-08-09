from core.application.portfolio_risk_review_service import PortfolioRiskReviewService


def portfolio(risk_score=70.0, weight=0.15):
    return {
        "constraints": {
            "max_weight": 0.20,
            "max_sector_weight": 0.30,
            "cash_weight": 0.05,
        },
        "cash_weight": 0.05,
        "holdings": [
            {
                "ticker": "AAA",
                "sector": "Technology",
                "weight": weight,
                "market_signals": {
                    "risk_score": risk_score,
                    "annualised_volatility": 0.20,
                },
            },
            {
                "ticker": "BBB",
                "sector": "Health Care",
                "weight": 0.15,
                "market_signals": {
                    "risk_score": 75.0,
                    "annualised_volatility": 0.20,
                },
            },
            {
                "ticker": "CCC",
                "sector": "Financials",
                "weight": 0.15,
                "market_signals": {
                    "risk_score": 80.0,
                    "annualised_volatility": 0.20,
                },
            },
            {
                "ticker": "DDD",
                "sector": "Industrials",
                "weight": 0.15,
                "market_signals": {
                    "risk_score": 85.0,
                    "annualised_volatility": 0.20,
                },
            },
            {
                "ticker": "EEE",
                "sector": "Utilities",
                "weight": 0.15,
                "market_signals": {
                    "risk_score": 90.0,
                    "annualised_volatility": 0.20,
                },
            },
            {
                "ticker": "FFF",
                "sector": "Materials",
                "weight": 0.20,
                "market_signals": {
                    "risk_score": 90.0,
                    "annualised_volatility": 0.20,
                },
            },
        ],
    }


def test_risk_review_passes_a_compliant_portfolio():
    report = PortfolioRiskReviewService.review(portfolio(weight=0.15))

    assert report["Pass"] is True
    assert report["volatility_coverage"]["covered_holdings"] == 6


def test_risk_review_flags_a_low_risk_score():
    report = PortfolioRiskReviewService.review(portfolio(risk_score=35.0, weight=0.15))

    assert report["Pass"] is False
    assert report["Risk Check"]["Flags"][0]["Ticker"] == "AAA"


if __name__ == "__main__":
    test_risk_review_passes_a_compliant_portfolio()
    test_risk_review_flags_a_low_risk_score()
    print("PORTFOLIO RISK REVIEW SERVICE TESTS PASSED")
