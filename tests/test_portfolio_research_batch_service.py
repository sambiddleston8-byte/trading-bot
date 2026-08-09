from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core.application.portfolio_research_batch_service import (
    PortfolioResearchBatchService,
)


def test_next_batch_is_sector_diverse_and_excludes_saved_research():
    original_pipeline = PortfolioResearchBatchService.PIPELINE_DIRECTORY
    original_universe = PortfolioResearchBatchService.UNIVERSE_PATH

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        pipeline = root / "pipeline"
        pipeline.mkdir()
        (pipeline / "ALREADY.json").write_text("{}", encoding="utf-8")
        universe = root / "both.json"
        universe.write_text(
            json.dumps(
                {
                    "companies": [
                        {"ticker": "ALREADY", "sector": "Information Technology"},
                        {"ticker": "TECH1", "sector": "Information Technology"},
                        {"ticker": "TECH2", "sector": "Information Technology"},
                        {"ticker": "HEALTH1", "sector": "Health Care"},
                        {"ticker": "FIN1", "sector": "Financials"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        PortfolioResearchBatchService.PIPELINE_DIRECTORY = pipeline
        PortfolioResearchBatchService.UNIVERSE_PATH = universe

        batch = PortfolioResearchBatchService.next_batch(3)

        assert [item["ticker"] for item in batch] == [
            "FIN1",
            "HEALTH1",
            "TECH1",
        ]

    PortfolioResearchBatchService.PIPELINE_DIRECTORY = original_pipeline
    PortfolioResearchBatchService.UNIVERSE_PATH = original_universe


def test_combined_universe_coverage_requires_both_indices_without_filtering():
    original_universe = PortfolioResearchBatchService.UNIVERSE_PATH

    with tempfile.TemporaryDirectory() as directory:
        universe = Path(directory) / "both.json"
        companies = [
            {"ticker": f"S{number}", "index_membership": ["SP500"]}
            for number in range(499)
        ] + [
            {
                "ticker": "NQ100",
                "sector": "Computer Manufacturing",
                "index_membership": ["NASDAQ100"],
            }
        ]
        universe.write_text(json.dumps({"companies": companies}), encoding="utf-8")
        PortfolioResearchBatchService.UNIVERSE_PATH = universe

        coverage = PortfolioResearchBatchService.universe_coverage()
        assert coverage["ready"] is True
        assert coverage["unique_ticker_count"] == 500
        assert coverage["nasdaq100_available"] is True

        batch = PortfolioResearchBatchService.next_batch(500)
        assert "NQ100" in {item["ticker"] for item in batch}

    PortfolioResearchBatchService.UNIVERSE_PATH = original_universe


def test_run_records_each_success_and_failure():
    original_reports = PortfolioResearchBatchService.REPORT_DIRECTORY

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        PortfolioResearchBatchService.REPORT_DIRECTORY = root

        def runner(ticker):
            if ticker == "FAIL":
                raise RuntimeError("source unavailable")
            return {
                "path": root / f"{ticker}.json",
                "canonical": {
                    "decision": "BUY",
                    "audit": {"status": "PASS"},
                },
            }

        report = PortfolioResearchBatchService.run(
            [
                {"ticker": "GOOD", "sector": "Tech"},
                {"ticker": "FAIL", "sector": "Health"},
            ],
            research_runner=runner,
            delay_seconds=0,
        )

        assert report["completed_count"] == 1
        assert report["error_count"] == 1
        assert report["path"].exists()
        saved = json.loads(report["path"].read_text(encoding="utf-8"))
        assert saved["status"] == "COMPLETE"
        assert saved["completed_count"] == 1

    PortfolioResearchBatchService.REPORT_DIRECTORY = original_reports


def test_resume_only_runs_companies_missing_from_the_checkpoint():
    original_reports = PortfolioResearchBatchService.REPORT_DIRECTORY

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        PortfolioResearchBatchService.REPORT_DIRECTORY = root
        path = root / "portfolio_research_batch_resume.json"
        path.write_text(
            json.dumps(
                {
                    "status": "RUNNING",
                    "requested_count": 2,
                    "companies": [
                        {"ticker": "DONE", "sector": "Health Care"},
                        {"ticker": "NEXT", "sector": "Financials"},
                    ],
                    "completed_count": 1,
                    "error_count": 0,
                    "outcomes": [
                        {
                            "ticker": "DONE",
                            "sector": "Health Care",
                            "status": "COMPLETE",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        called = []

        def runner(ticker):
            called.append(ticker)
            return {
                "path": root / f"{ticker}.json",
                "canonical": {"decision": "BUY", "audit": {"status": "PASS"}},
            }

        report = PortfolioResearchBatchService.resume(path, research_runner=runner)

        assert called == ["NEXT"]
        assert report["status"] == "COMPLETE"
        assert report["completed_count"] == 2

    PortfolioResearchBatchService.REPORT_DIRECTORY = original_reports


def test_next_batch_refreshes_completed_records_without_market_signals():
    original_pipeline = PortfolioResearchBatchService.PIPELINE_DIRECTORY
    original_universe = PortfolioResearchBatchService.UNIVERSE_PATH

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        pipeline = root / "pipeline"
        pipeline.mkdir()
        (pipeline / "STALE.json").write_text(
            json.dumps(
                {
                    "ticker": "STALE",
                    "status": "COMPLETE",
                    "core": {"decision": {"decision": "BUY"}},
                    "research": {"thesis_challenge": {}},
                }
            ),
            encoding="utf-8",
        )
        universe = root / "both.json"
        universe.write_text(
            json.dumps(
                {
                    "companies": [
                        {"ticker": "STALE", "sector": "Financials"},
                        {"ticker": "NEW", "sector": "Health Care"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        PortfolioResearchBatchService.PIPELINE_DIRECTORY = pipeline
        PortfolioResearchBatchService.UNIVERSE_PATH = universe

        assert [item["ticker"] for item in PortfolioResearchBatchService.next_batch(1)] == [
            "STALE",
        ]

    PortfolioResearchBatchService.PIPELINE_DIRECTORY = original_pipeline
    PortfolioResearchBatchService.UNIVERSE_PATH = original_universe


def test_next_batch_refreshes_records_without_specialist_research():
    original_pipeline = PortfolioResearchBatchService.PIPELINE_DIRECTORY
    original_universe = PortfolioResearchBatchService.UNIVERSE_PATH

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        pipeline = root / "pipeline"
        pipeline.mkdir()
        (pipeline / "STALE.json").write_text(
            json.dumps(
                {
                    "ticker": "STALE",
                    "status": "COMPLETE",
                    "core": {"decision": {"decision": "BUY"}},
                    "research": {
                        "market_signals": {
                            "technical": {"score": 70},
                            "risk": {"score": 70},
                        },
                        "thesis_challenge": {},
                    },
                }
            ),
            encoding="utf-8",
        )
        universe = root / "both.json"
        universe.write_text(
            json.dumps(
                {
                    "companies": [
                        {"ticker": "STALE", "sector": "Financials"},
                        {"ticker": "NEW", "sector": "Health Care"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        PortfolioResearchBatchService.PIPELINE_DIRECTORY = pipeline
        PortfolioResearchBatchService.UNIVERSE_PATH = universe

        assert [item["ticker"] for item in PortfolioResearchBatchService.next_batch(1)] == [
            "STALE",
        ]

    PortfolioResearchBatchService.PIPELINE_DIRECTORY = original_pipeline
    PortfolioResearchBatchService.UNIVERSE_PATH = original_universe


def test_next_batch_refreshes_records_without_master_decision():
    original_pipeline = PortfolioResearchBatchService.PIPELINE_DIRECTORY
    original_universe = PortfolioResearchBatchService.UNIVERSE_PATH

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        pipeline = root / "pipeline"
        pipeline.mkdir()
        (pipeline / "STALE.json").write_text(
            json.dumps(
                {
                    "ticker": "STALE",
                    "status": "COMPLETE",
                    "core": {"decision": {"decision": "BUY"}},
                    "research": {
                        "market_signals": {
                            "technical": {"score": 70},
                            "risk": {"score": 70},
                        },
                        "specialist_research": {"status": "COMPLETE"},
                        "thesis_challenge": {},
                    },
                }
            ),
            encoding="utf-8",
        )
        universe = root / "both.json"
        universe.write_text(
            json.dumps({"companies": [{"ticker": "STALE", "sector": "Financials"}]}),
            encoding="utf-8",
        )
        PortfolioResearchBatchService.PIPELINE_DIRECTORY = pipeline
        PortfolioResearchBatchService.UNIVERSE_PATH = universe

        assert [item["ticker"] for item in PortfolioResearchBatchService.next_batch(1)] == ["STALE"]

    PortfolioResearchBatchService.PIPELINE_DIRECTORY = original_pipeline
    PortfolioResearchBatchService.UNIVERSE_PATH = original_universe


if __name__ == "__main__":
    test_next_batch_is_sector_diverse_and_excludes_saved_research()
    test_run_records_each_success_and_failure()
    test_resume_only_runs_companies_missing_from_the_checkpoint()
    test_next_batch_refreshes_completed_records_without_market_signals()
    test_next_batch_refreshes_records_without_specialist_research()
    test_next_batch_refreshes_records_without_master_decision()

    print("PORTFOLIO RESEARCH BATCH SERVICE TESTS PASSED")
