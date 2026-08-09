from __future__ import annotations

"""Run bounded, resumable research cycles for portfolio coverage."""

from typing import Any, Callable

from core.application.portfolio_research_batch_service import (
    PortfolioResearchBatchService,
)
from core.application.portfolio_construction_service import (
    PortfolioConstructionService,
)
from core.application.research_service import ResearchService
from core.application.portfolio_research_funnel_service import (
    PortfolioResearchFunnelService,
)
from core.portfolio.portfolio_engine import PortfolioEngine


class PortfolioCoverageService:
    """The single official route from the index universe to portfolio-ready research."""

    @staticmethod
    def target_reached(readiness: dict[str, Any]) -> bool:
        """Read current readiness while remaining compatible with older reports."""
        return bool(readiness.get("target_reached", readiness.get("ready", False)))

    @classmethod
    def status(cls, target_holdings: int = PortfolioEngine.DEFAULT_HOLDINGS) -> dict[str, Any]:
        scan = PortfolioConstructionService.research_scan()
        readiness = PortfolioConstructionService.portfolio_readiness(
            scan,
            target_holdings,
        )
        return {
            "universe": PortfolioResearchBatchService.universe_coverage(),
            "readiness": readiness,
            "researched_records": scan["requested_count"],
            "eligible_records": scan["eligible_count"],
            "interrupted_batch": (
                str(PortfolioResearchBatchService.latest_resumable_report())
                if PortfolioResearchBatchService.latest_resumable_report()
                else None
            ),
            "research_funnel": {
                "version": PortfolioResearchFunnelService.VERSION,
                "target_shortlist_size": PortfolioResearchFunnelService.DEFAULT_SHORTLIST_SIZE,
                "description": "Sector-balanced market screen before full multi-bot research.",
            },
        }

    @classmethod
    def run_cycle(
        cls,
        target_holdings: int = PortfolioEngine.DEFAULT_HOLDINGS,
        batch_size: int = 12,
        delay_seconds: float = 1.0,
        research_runner: Callable[[str], dict[str, Any]] = ResearchService.run,
    ) -> dict[str, Any]:
        """Resume one interrupted batch or run exactly one new paced batch."""
        before = cls.status(target_holdings)
        if cls.target_reached(before["readiness"]):
            return {"status": "READY", "before": before, "after": before, "batch": None}

        interrupted = PortfolioResearchBatchService.latest_resumable_report()
        if interrupted:
            batch = PortfolioResearchBatchService.resume(
                interrupted,
                research_runner=research_runner,
                delay_seconds=delay_seconds,
            )
        else:
            companies = PortfolioResearchBatchService.next_batch(batch_size)
            if not companies:
                return {
                    "status": "UNIVERSE_EXHAUSTED",
                    "before": before,
                    "after": cls.status(target_holdings),
                    "batch": None,
                }
            batch = PortfolioResearchBatchService.run(
                companies,
                research_runner=research_runner,
                delay_seconds=delay_seconds,
            )

        after = cls.status(target_holdings)
        return {
            "status": (
                "READY"
                if cls.target_reached(after["readiness"])
                else "CONSTRUCTIBLE"
                if after["readiness"].get("ready")
                else "RESEARCHING"
            ),
            "before": before,
            "after": after,
            "batch": batch,
        }

    @classmethod
    def run_until_ready(
        cls,
        target_holdings: int = PortfolioEngine.DEFAULT_HOLDINGS,
        batch_size: int = PortfolioResearchBatchService.DEFAULT_BATCH_SIZE,
        max_batches: int = 8,
        delay_seconds: float = 1.0,
        research_runner: Callable[[str], dict[str, Any]] = ResearchService.run,
    ) -> dict[str, Any]:
        """Run a bounded, resumable coverage programme.

        The limit is deliberate: it prevents an unattended prototype from
        consuming an entire provider quota or researching the whole universe
        merely because the first several companies are unsuitable.
        """

        if max_batches < 1:
            raise ValueError("max_batches must be at least one.")

        cycles = []
        for _ in range(int(max_batches)):
            cycle = cls.run_cycle(
                target_holdings=target_holdings,
                batch_size=batch_size,
                delay_seconds=delay_seconds,
                research_runner=research_runner,
            )
            cycles.append(cycle)
            if cycle["status"] in {"READY", "UNIVERSE_EXHAUSTED"}:
                break

        final_status = cls.status(target_holdings)
        return {
            "status": (
                "READY"
                if cls.target_reached(final_status["readiness"])
                else "CONSTRUCTIBLE"
                if final_status["readiness"].get("ready")
                else "RESEARCHING"
            ),
            "cycles_completed": len(cycles),
            "cycles": cycles,
            "final": final_status,
        }
