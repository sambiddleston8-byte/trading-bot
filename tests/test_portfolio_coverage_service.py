from __future__ import annotations

from core.application.portfolio_coverage_service import PortfolioCoverageService
from core.application.portfolio_research_batch_service import PortfolioResearchBatchService


def test_coverage_cycle_uses_one_bounded_batch():
    calls = []
    original_status = PortfolioCoverageService.__dict__["status"]
    original_interrupted = PortfolioResearchBatchService.__dict__["latest_resumable_report"]
    original_next_batch = PortfolioResearchBatchService.__dict__["next_batch"]
    original_run = PortfolioResearchBatchService.__dict__["run"]

    try:
        PortfolioCoverageService.status = classmethod(
            lambda cls, target: {
                "readiness": {"ready": False},
                "eligible_records": 1,
            }
        )
        PortfolioResearchBatchService.latest_resumable_report = classmethod(
            lambda cls: None
        )
        PortfolioResearchBatchService.next_batch = classmethod(
            lambda cls, size: [{"ticker": "TEST", "sector": "Health Care"}]
        )

        def run(cls, companies, research_runner, delay_seconds):
            calls.append((companies, delay_seconds))
            return {"status": "COMPLETE", "completed_count": 1}

        PortfolioResearchBatchService.run = classmethod(run)

        result = PortfolioCoverageService.run_cycle(
            batch_size=1,
            delay_seconds=0,
            research_runner=lambda ticker: {"canonical": {}},
        )

        assert result["status"] == "RESEARCHING"
        assert calls == [([{"ticker": "TEST", "sector": "Health Care"}], 0)]
    finally:
        PortfolioCoverageService.status = original_status
        PortfolioResearchBatchService.latest_resumable_report = original_interrupted
        PortfolioResearchBatchService.next_batch = original_next_batch
        PortfolioResearchBatchService.run = original_run


def test_run_until_ready_stops_when_the_target_is_reached():
    original_cycle = PortfolioCoverageService.__dict__["run_cycle"]
    original_status = PortfolioCoverageService.__dict__["status"]
    calls = []
    try:
        def run_cycle(cls, **kwargs):
            calls.append(kwargs)
            return {"status": "READY", "before": {}, "after": {}, "batch": None}

        PortfolioCoverageService.run_cycle = classmethod(run_cycle)
        PortfolioCoverageService.status = classmethod(
            lambda cls, target: {"readiness": {"ready": True}}
        )
        result = PortfolioCoverageService.run_until_ready(max_batches=3)
        assert result["status"] == "READY"
        assert result["cycles_completed"] == 1
        assert len(calls) == 1
    finally:
        PortfolioCoverageService.run_cycle = original_cycle
        PortfolioCoverageService.status = original_status


if __name__ == "__main__":
    test_coverage_cycle_uses_one_bounded_batch()
    test_run_until_ready_stops_when_the_target_is_reached()
    print("PORTFOLIO COVERAGE SERVICE TESTS PASSED")
