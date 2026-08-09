from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.application.research_service import ResearchService
from core.portfolio.portfolio_engine import PortfolioEngine
from core.portfolio.universe_scanner import UniverseScanner


class PortfolioWorkflowService:
    """Safe application layer for small research batches and portfolio outputs."""

    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    SCAN_DIRECTORY = PROJECT_ROOT / "data" / "research" / "universe_scans"
    PORTFOLIO_DIRECTORY = PROJECT_ROOT / "data" / "research" / "portfolios"

    @staticmethod
    def timestamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    @classmethod
    def normalise_tickers(cls, values: list[str], maximum: int = 10) -> list[str]:
        tickers: list[str] = []
        for value in values:
            ticker = ResearchService.normalise_ticker(value)
            if ticker not in tickers:
                tickers.append(ticker)
        if not tickers:
            raise ValueError("Enter at least one ticker.")
        if len(tickers) > maximum:
            raise ValueError(f"Website research batches are limited to {maximum} tickers.")
        return tickers

    @classmethod
    def scan_tickers(
        cls,
        values: list[str],
        workers: int = 1,
        scanner_class: type[UniverseScanner] = UniverseScanner,
    ) -> dict[str, Any]:
        tickers = cls.normalise_tickers(values)
        universe_name = f"manual_batch_{cls.timestamp()}"
        universe = {
            "universe": universe_name,
            "companies": [{"ticker": ticker} for ticker in tickers],
        }
        scanner = scanner_class(workers=workers)
        scan = scanner.scan(universe)
        cls.SCAN_DIRECTORY.mkdir(parents=True, exist_ok=True)
        path = cls.SCAN_DIRECTORY / f"{universe_name}.json"
        scanner.save(scan, path=path)
        return {"scan": scan, "path": path}

    @classmethod
    def load_scan(cls, path: Path) -> dict[str, Any]:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Saved scan is not a valid object.")
        return value

    @classmethod
    def construct_portfolio(
        cls,
        scan: dict[str, Any],
        holdings: int = 10,
        portfolio_class: type[PortfolioEngine] = PortfolioEngine,
    ) -> dict[str, Any]:
        portfolio = portfolio_class.construct(
            scan,
            number_of_stocks=holdings,
        )
        cls.PORTFOLIO_DIRECTORY.mkdir(parents=True, exist_ok=True)
        path = cls.PORTFOLIO_DIRECTORY / f"portfolio_{cls.timestamp()}.json"
        portfolio_class.save(portfolio, path=path)
        return {"portfolio": portfolio, "path": path}
