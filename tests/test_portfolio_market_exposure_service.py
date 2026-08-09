import pandas as pd

from core.application.portfolio_market_exposure_service import PortfolioMarketExposureService


def history(prices, volumes):
    return pd.DataFrame({"Close": prices, "Volume": volumes})


def test_exposure_review_surfaces_correlation_and_liquidity_without_trade_signal():
    prices = [100.0 + day for day in range(90)]
    data = {
        "AAA": history(prices, [100_000] * 90),
        "BBB": history([value * 1.01 for value in prices], [5_000] * 90),
    }
    portfolio = {
        "holdings": [
            {"ticker": "AAA", "name": "Alpha", "sector": "Technology", "industry": "Software", "weight": 0.50},
            {"ticker": "BBB", "name": "Beta", "sector": "Technology", "industry": "Software", "weight": 0.50},
        ]
    }
    result = PortfolioMarketExposureService.review(
        portfolio,
        history_lookup=lambda ticker: data[ticker],
    )

    assert result["status"] == "COMPLETE"
    assert result["effective_position_count"] == 2.0
    assert result["highest_correlated_pairs"][0]["correlation"] > 0.99
    assert any("high observed price-history correlation" in alert for alert in result["risk_alerts"])
    assert any(item["status"] == "LOW" for item in result["liquidity"])


if __name__ == "__main__":
    test_exposure_review_surfaces_correlation_and_liquidity_without_trade_signal()
    print("PORTFOLIO MARKET EXPOSURE TESTS PASSED")
