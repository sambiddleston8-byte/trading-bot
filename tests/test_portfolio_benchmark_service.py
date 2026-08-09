import json
from pathlib import Path
from tempfile import TemporaryDirectory

from core.application.portfolio_benchmark_service import PortfolioBenchmarkService


def test_benchmark_disclosure_is_explicit_and_does_not_fake_performance():
    disclosure = PortfolioBenchmarkService.disclosure(
        {
            "valuation_horizon_years": 5,
            "market_context": {
                "market_regime": {
                    "status": "COMPLETE",
                    "regime": "RISK_ON",
                    "benchmark": "^GSPC",
                }
            },
        }
    )

    assert disclosure["benchmark"]["name"] == "S&P 500 Index"
    assert disclosure["benchmark"]["ticker"] == "^GSPC"
    assert disclosure["performance_comparison"]["status"] == "NOT_ENOUGH_DATA"
    assert disclosure["valuation_time_horizon"]["forecast_years"] == 5
    assert "5-year" in disclosure["valuation_time_horizon"]["label"]


def test_history_requires_a_full_month_before_claiming_monthly_performance():
    portfolio = {"created_at": "2026-08-09T00:00:00+00:00"}
    snapshots = [
        {
            "portfolio_created_at": portfolio["created_at"],
            "checked_at": "2026-08-09T00:00:00+00:00",
            "benchmark": {"price": 100.0},
            "positions": [{"weight": 1.0, "price_change": 0.0}],
        },
        {
            "portfolio_created_at": portfolio["created_at"],
            "checked_at": "2026-08-20T00:00:00+00:00",
            "benchmark": {"price": 102.0},
            "positions": [{"weight": 1.0, "price_change": 0.03}],
        },
    ]
    with TemporaryDirectory() as directory:
        snapshot_directory = Path(directory)
        for index, snapshot in enumerate(snapshots):
            (snapshot_directory / f"portfolio_health_{index}.json").write_text(json.dumps(snapshot), encoding="utf-8")
        original_directory = PortfolioBenchmarkService.SNAPSHOT_DIRECTORY
        try:
            PortfolioBenchmarkService.SNAPSHOT_DIRECTORY = snapshot_directory
            comparison = PortfolioBenchmarkService.performance_comparison(portfolio)
        finally:
            PortfolioBenchmarkService.SNAPSHOT_DIRECTORY = original_directory

    assert comparison["status"] == "NOT_ENOUGH_DATA"
    assert len(comparison["history"]) == 2


def test_history_calculates_a_monthly_comparison_from_dated_snapshots():
    portfolio = {"created_at": "2026-08-01T00:00:00+00:00"}
    snapshots = [
        ("2026-08-01T00:00:00+00:00", 100.0, 0.0),
        ("2026-09-01T00:00:00+00:00", 105.0, 0.08),
    ]
    with TemporaryDirectory() as directory:
        snapshot_directory = Path(directory)
        for index, (checked_at, benchmark_price, portfolio_return) in enumerate(snapshots):
            snapshot = {
                "portfolio_created_at": portfolio["created_at"],
                "checked_at": checked_at,
                "benchmark": {"price": benchmark_price},
                "positions": [{"weight": 1.0, "price_change": portfolio_return}],
            }
            (snapshot_directory / f"portfolio_health_{index}.json").write_text(json.dumps(snapshot), encoding="utf-8")
        original_directory = PortfolioBenchmarkService.SNAPSHOT_DIRECTORY
        try:
            PortfolioBenchmarkService.SNAPSHOT_DIRECTORY = snapshot_directory
            comparison = PortfolioBenchmarkService.performance_comparison(portfolio)
        finally:
            PortfolioBenchmarkService.SNAPSHOT_DIRECTORY = original_directory

    assert comparison["status"] == "COMPLETE"
    assert round(comparison["one_month"]["portfolio_return"], 4) == 0.08
    assert round(comparison["one_month"]["sp500_return"], 4) == 0.05
    assert round(comparison["one_month"]["relative_return"], 4) == 0.03


if __name__ == "__main__":
    test_benchmark_disclosure_is_explicit_and_does_not_fake_performance()
    test_history_requires_a_full_month_before_claiming_monthly_performance()
    test_history_calculates_a_monthly_comparison_from_dated_snapshots()
    print("PORTFOLIO BENCHMARK SERVICE TESTS PASSED")
