from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.application.research_service import ResearchService
from core.research.research_contract import ResearchContract
from core.research.master_portfolio_decision_engine import (
    MasterPortfolioDecisionEngine,
)
from core.research.market_signal_engine import MarketSignalEngine


class PortfolioResearchBatchService:
    """Create a reproducible, sector-diverse research queue for portfolio coverage."""

    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    PIPELINE_DIRECTORY = PROJECT_ROOT / "data" / "research" / "pipeline"
    UNIVERSE_PATH = PROJECT_ROOT / "data" / "research" / "universe" / "both.json"
    REPORT_DIRECTORY = PROJECT_ROOT / "data" / "research" / "reports"

    DEFAULT_BATCH_SIZE = 12
    REFRESH_SHARE = 0.35
    MASTER_POLICY_VERSION = MasterPortfolioDecisionEngine.VERSION

    PORTFOLIO_SECTORS = (
        "Communication Services",
        "Consumer Discretionary",
        "Consumer Staples",
        "Energy",
        "Financials",
        "Health Care",
        "Industrials",
        "Information Technology",
        "Materials",
        "Real Estate",
        "Utilities",
    )

    @staticmethod
    def timestamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    @staticmethod
    def load_json(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @classmethod
    def universe_companies(cls) -> list[dict[str, Any]]:
        universe = cls.load_json(cls.UNIVERSE_PATH) or {}
        companies = universe.get("companies", [])
        return [
            company
            for company in companies
            if isinstance(company, dict)
            and company.get("ticker")
        ]

    @classmethod
    def universe_coverage(cls) -> dict[str, Any]:
        """Validate that the research bot can see the whole combined universe."""
        companies = cls.universe_companies()
        tickers = {
            str(company["ticker"]).upper()
            for company in companies
        }
        memberships = {
            membership
            for company in companies
            for membership in company.get("index_membership", [])
        }
        return {
            "universe_path": str(cls.UNIVERSE_PATH),
            "company_count": len(companies),
            "unique_ticker_count": len(tickers),
            "sp500_available": "SP500" in memberships,
            "nasdaq100_available": "NASDAQ100" in memberships,
            "ready": (
                len(tickers) >= 500
                and "SP500" in memberships
                and "NASDAQ100" in memberships
            ),
        }

    @classmethod
    def researched_tickers(cls) -> set[str]:
        return {
            path.stem.upper()
            for path in cls.PIPELINE_DIRECTORY.glob("*.json")
        }

    @classmethod
    def stale_research_tickers(cls) -> set[str]:
        """Return completed records that pre-date required portfolio inputs.

        A saved JSON file is not necessarily current research coverage.  For
        example, records written before the technical/risk integration have no
        market signals and must be refreshed before they can enter a model
        portfolio.
        """

        stale: set[str] = set()

        for path in cls.PIPELINE_DIRECTORY.glob("*.json"):
            raw = cls.load_json(path)
            if not raw or raw.get("status") != "COMPLETE":
                continue

            canonical = ResearchContract.from_pipeline_result(raw)
            raw_research = raw.get("research") if isinstance(raw.get("research"), dict) else {}
            raw_signals = raw_research.get("market_signals") if isinstance(raw_research.get("market_signals"), dict) else {}
            signals = canonical.get("market_signals") or {}
            specialist_research = canonical.get("specialist_research") or {}
            master_decision = canonical.get("master_decision") or {}
            valuation_quality = canonical.get("valuation_quality") or {}
            diagnostics = canonical.get("diagnostics") or {}

            if (
                canonical.get("research_status") != "COMPLETE"
                or signals.get("technical_score") is None
                or signals.get("risk_score") is None
                or raw_signals.get("version") != MarketSignalEngine.VERSION
                or specialist_research.get("status") != "COMPLETE"
                or specialist_research.get("version") != "1.1-complete-specialist-coverage"
                or master_decision.get("status") != "COMPLETE"
                or master_decision.get("version") != cls.MASTER_POLICY_VERSION
                or valuation_quality.get("status") != "COMPLETE"
                or diagnostics.get("status") != "COMPLETE"
            ):
                stale.add(path.stem.upper())

        return stale

    @classmethod
    def _sector_diverse_selection(
        cls,
        companies: list[dict[str, Any]],
        batch_size: int,
    ) -> list[dict[str, Any]]:
        by_sector: dict[str, list[dict[str, Any]]] = {}

        for company in companies:
            sector = str(company.get("sector") or "Unknown")
            by_sector.setdefault(sector, []).append(company)

        for items in by_sector.values():
            items.sort(key=lambda item: str(item["ticker"]).upper())

        ordered_sectors = sorted(
            by_sector,
            key=lambda sector: (len(by_sector[sector]), sector),
        )
        selected: list[dict[str, Any]] = []
        index = 0

        while len(selected) < batch_size:
            added = False
            for sector in ordered_sectors:
                items = by_sector[sector]
                if index >= len(items):
                    continue
                selected.append(items[index])
                added = True
                if len(selected) >= batch_size:
                    break
            if not added:
                break
            index += 1

        return selected

    @classmethod
    def next_batch(
        cls,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> list[dict[str, Any]]:
        """Select unresearched companies round-robin across sectors.

        Alphabetical ordering makes the queue reproducible.  Round-robin
        selection avoids a portfolio research run being dominated by the
        largest index sector.
        """

        if batch_size < 1:
            raise ValueError("Batch size must be at least one.")

        researched = cls.researched_tickers()
        stale = cls.stale_research_tickers()

        # Every company in the combined S&P 500 / Nasdaq-100 source is in
        # scope.  Some Nasdaq records use provider-specific sector labels;
        # filtering those out would quietly make the research bot incomplete.
        universe = cls.universe_companies()

        stale_companies = []
        unresearched_companies = []

        for company in universe:
            ticker = str(company["ticker"]).upper()
            if ticker in stale:
                stale_companies.append(company)
            elif ticker not in researched:
                unresearched_companies.append(company)

        # Repair a bounded share of stale records while continuing to broaden
        # coverage.  A stale-first queue can spend every cycle repairing an
        # old cohort and never discover enough independent candidates for a
        # genuinely diversified portfolio.
        refresh_size = 0
        if stale_companies:
            refresh_size = min(
                len(stale_companies),
                max(1, int(round(batch_size * cls.REFRESH_SHARE))),
            )
        selected = cls._sector_diverse_selection(
            stale_companies,
            refresh_size,
        )

        if len(selected) < batch_size:
            selected.extend(
                cls._sector_diverse_selection(
                    unresearched_companies,
                    batch_size - len(selected),
                )
            )

        if len(selected) < batch_size:
            already_selected = {
                str(company["ticker"]).upper()
                for company in selected
            }
            selected.extend(
                company
                for company in cls._sector_diverse_selection(
                    stale_companies,
                    batch_size - len(selected),
                )
                if str(company["ticker"]).upper() not in already_selected
            )

        return selected[:batch_size]

    @classmethod
    def run(
        cls,
        companies: list[dict[str, Any]],
        research_runner: Callable[[str], dict[str, Any]] = ResearchService.run,
        report_path: Path | None = None,
        delay_seconds: float = 1.0,
    ) -> dict[str, Any]:
        """Run a bounded batch and checkpoint an auditable outcome report."""

        cls.REPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        path = report_path or (
            cls.REPORT_DIRECTORY / f"portfolio_research_batch_{cls.timestamp()}.json"
        )
        existing = cls.load_json(path) if report_path else None

        if existing:
            report = existing
            outcomes = report.get("outcomes")
            if not isinstance(outcomes, list):
                outcomes = []
            report["outcomes"] = outcomes
            report["status"] = "RUNNING"
            report.pop("completed_at", None)
        else:
            outcomes: list[dict[str, Any]] = []
            report = {
                "status": "RUNNING",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "requested_count": len(companies),
                "companies": [
                    {
                        "ticker": str(company["ticker"]).upper(),
                        "sector": company.get("sector"),
                    }
                    for company in companies
                ],
                "completed_count": 0,
                "error_count": 0,
                "outcomes": outcomes,
                "path": str(path),
            }

        def checkpoint() -> None:
            report["completed_count"] = sum(
                item["status"] == "COMPLETE"
                for item in outcomes
            )
            report["error_count"] = sum(
                item["status"] == "ERROR"
                for item in outcomes
            )
            path.write_text(
                json.dumps(report, indent=2),
                encoding="utf-8",
            )

        # A process may be interrupted by a terminal close, a rate-limit, or
        # a machine sleep.  Persist a running manifest before the first
        # network call and after every outcome so completed work is never
        # hidden just because the last item did not finish.
        checkpoint()

        recorded_tickers = {
            str(item.get("ticker") or "").upper()
            for item in outcomes
            if isinstance(item, dict)
        }

        if delay_seconds < 0:
            raise ValueError("Research delay cannot be negative.")

        remaining = [
            company
            for company in companies
            if str(company["ticker"]).upper() not in recorded_tickers
        ]

        for index, company in enumerate(remaining):
            ticker = str(company["ticker"]).upper()
            if ticker in recorded_tickers:
                continue
            try:
                result = research_runner(ticker)
                canonical = result.get("canonical", {})
                audit = canonical.get("audit", {})
                outcomes.append(
                    {
                        "ticker": ticker,
                        "sector": company.get("sector"),
                        "status": "COMPLETE",
                        "decision": canonical.get("decision"),
                        "audit_status": audit.get("status"),
                        "output_path": str(result.get("path") or ""),
                    }
                )
            except Exception as exc:
                outcomes.append(
                    {
                        "ticker": ticker,
                        "sector": company.get("sector"),
                        "status": "ERROR",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )

            checkpoint()

            # Market, news and peer data providers need a paced caller.  The
            # checkpoint above means a stop during the delay is still safe to
            # resume without duplicating completed research.
            if delay_seconds and index < len(remaining) - 1:
                time.sleep(delay_seconds)

        report["status"] = "COMPLETE"
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        checkpoint()
        report["path"] = path
        return report

    @classmethod
    def run_next_batch(
        cls,
        batch_size: int = DEFAULT_BATCH_SIZE,
        research_runner: Callable[[str], dict[str, Any]] = ResearchService.run,
        delay_seconds: float = 1.0,
    ) -> dict[str, Any]:
        return cls.run(
            cls.next_batch(batch_size),
            research_runner=research_runner,
            delay_seconds=delay_seconds,
        )

    @classmethod
    def resume(
        cls,
        report_path: Path,
        research_runner: Callable[[str], dict[str, Any]] = ResearchService.run,
        delay_seconds: float = 1.0,
    ) -> dict[str, Any]:
        """Resume a checkpointed batch without repeating saved outcomes."""

        report = cls.load_json(Path(report_path))
        if not report:
            raise ValueError("Research batch report could not be read.")

        companies = report.get("companies")
        if not isinstance(companies, list) or not companies:
            raise ValueError(
                "This older research report cannot be resumed because its queue was not saved."
            )

        return cls.run(
            companies,
            research_runner=research_runner,
            report_path=Path(report_path),
            delay_seconds=delay_seconds,
        )

    @classmethod
    def latest_resumable_report(cls) -> Path | None:
        """Return the newest interrupted batch whose queue was checkpointed."""

        for path in sorted(
            cls.REPORT_DIRECTORY.glob("portfolio_research_batch_*.json"),
            reverse=True,
        ):
            report = cls.load_json(path)
            if (
                report
                and report.get("status") == "RUNNING"
                and isinstance(report.get("companies"), list)
            ):
                return path
        return None
