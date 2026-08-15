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


def test_default_history_lookup_uses_the_injected_boundary_with_unchanged_request_shape():
    calls = []

    class HistoryClient:
        def history(self, symbol, **kwargs):
            calls.append((symbol, kwargs))
            frame = history([100.0 + day for day in range(90)], [100_000] * 90)
            return type("Observation", (), {"frame": frame})()

    result = PortfolioMarketExposureService.review(
        {"holdings": [{"ticker": "aaa", "weight": 1.0}]},
        history_client=HistoryClient(),
    )

    assert result["covered_holdings"] == 1
    assert calls == [("AAA", {"period": "1y", "auto_adjust": False})]


def test_default_history_lookup_stays_a_visible_gap_when_the_boundary_fails():
    class FailingHistoryClient:
        def history(self, symbol, **kwargs):
            raise RuntimeError("boundary refused the read")

    result = PortfolioMarketExposureService.review(
        {"holdings": [{"ticker": "AAA", "weight": 1.0}]},
        history_client=FailingHistoryClient(),
    )

    assert result["covered_holdings"] == 0
    assert result["status"] == "LIMITED"
    assert result["liquidity"][0]["status"] == "UNAVAILABLE"


if __name__ == "__main__":
    test_exposure_review_surfaces_correlation_and_liquidity_without_trade_signal()
    test_default_history_lookup_uses_the_injected_boundary_with_unchanged_request_shape()
    test_default_history_lookup_stays_a_visible_gap_when_the_boundary_fails()
    print("PORTFOLIO MARKET EXPOSURE TESTS PASSED")
