from __future__ import annotations

import tempfile
from pathlib import Path

from core.application.portfolio_workflow_service import PortfolioWorkflowService


class FakeScanner:
    def __init__(self, workers):
        assert workers == 1

    def scan(self, universe):
        assert [item["ticker"] for item in universe["companies"]] == ["NVDA", "AAPL"]
        return {
            "universe": universe["universe"],
            "requested_count": 2,
            "completed_count": 2,
            "audit_pass_count": 1,
            "eligible_count": 1,
            "results": [],
            "ranked": [],
        }

    @staticmethod
    def save(scan, path):
        Path(path).write_text("{}", encoding="utf-8")


class FakePortfolio:
    @staticmethod
    def construct(scan, number_of_stocks):
        assert scan["eligible_count"] == 1
        return {"number_of_stocks": number_of_stocks, "holdings": []}

    @staticmethod
    def save(portfolio, path):
        Path(path).write_text("{}", encoding="utf-8")


def test_scan_and_construct():
    old_scans = PortfolioWorkflowService.SCAN_DIRECTORY
    old_portfolios = PortfolioWorkflowService.PORTFOLIO_DIRECTORY
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        PortfolioWorkflowService.SCAN_DIRECTORY = root / "scans"
        PortfolioWorkflowService.PORTFOLIO_DIRECTORY = root / "portfolios"
        scan = PortfolioWorkflowService.scan_tickers(
            ["nvda", "AAPL", "NVDA"],
            scanner_class=FakeScanner,
        )
        assert scan["path"].exists()
        portfolio = PortfolioWorkflowService.construct_portfolio(
            scan["scan"],
            holdings=1,
            portfolio_class=FakePortfolio,
        )
        assert portfolio["path"].exists()
    PortfolioWorkflowService.SCAN_DIRECTORY = old_scans
    PortfolioWorkflowService.PORTFOLIO_DIRECTORY = old_portfolios


if __name__ == "__main__":
    test_scan_and_construct()
    print("PORTFOLIO WORKFLOW SERVICE TESTS PASSED")
