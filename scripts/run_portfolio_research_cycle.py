from __future__ import annotations

"""Run one complete proposed-portfolio research and paper-rebalance cycle.

The script is designed for a scheduler: it refreshes every current holding,
extends the audit-cleared replacement pool with one paced universe batch, then
updates only the proposed paper portfolio when balanced evidence warrants it.
It has no broker integration and cannot place a real order.
"""

import argparse
from typing import Any

from core.application.portfolio_construction_service import PortfolioConstructionService
from core.application.portfolio_coverage_service import PortfolioCoverageService
from core.application.portfolio_monitor_service import PortfolioMonitorService
from core.application.research_service import ResearchService


def refresh_holdings(portfolio: dict[str, Any]) -> tuple[int, list[str]]:
    refreshed = 0
    failures: list[str] = []
    for holding in portfolio.get("holdings") or []:
        ticker = str(holding.get("ticker") or "").upper()
        if not ticker:
            continue
        try:
            ResearchService.run(ticker)
            refreshed += 1
        except Exception:
            failures.append(ticker)
    return refreshed, failures


def run_cycle(
    *,
    candidate_batch_size: int = 12,
    refresh_current_holdings: bool = True,
) -> dict[str, Any]:
    portfolio, _ = PortfolioConstructionService.latest_portfolio()
    if portfolio is None:
        raise RuntimeError("No current proposed portfolio is available to refresh.")

    refreshed = 0
    failures: list[str] = []
    if refresh_current_holdings:
        refreshed, failures = refresh_holdings(portfolio)

    # The universe batch is deliberately separate from the current holdings:
    # it creates a larger, sector-diverse replacement pool while preserving
    # the existing portfolio until audit and master-decision gates clear.
    coverage = PortfolioCoverageService.run_cycle(
        target_holdings=15,
        batch_size=max(1, int(candidate_batch_size)),
    )

    snapshot = PortfolioMonitorService.evaluate(portfolio)
    applied = PortfolioMonitorService.apply_reallocation(portfolio, snapshot)
    updated_path = None
    persistence_comparison = {"status": "DISABLED", "mode": "local-files"}
    if applied.get("status") == "APPLIED":
        persisted = PortfolioConstructionService.persist_reallocation(
            previous_portfolio=portfolio,
            applied_result=applied,
            checked_at=str(snapshot.get("checked_at")),
            monitor_version=PortfolioMonitorService.VERSION,
        )
        applied["portfolio"] = persisted["portfolio"]
        updated_path = persisted["snapshot_path"]
        snapshot["reallocation_plan"] = applied["reallocation_plan"]
        snapshot["applied_portfolio_changes"] = applied["changes"]
        snapshot["applied_portfolio_path"] = str(updated_path)
        snapshot["applied_decision_ids"] = [
            record["decision_id"] for record in persisted["ledger_records"]
        ]
        persistence_comparison = persisted["persistence_comparison"]
    elif applied.get("status", "NO_CHANGE").startswith("NOT_APPLIED"):
        snapshot["reallocation_apply_status"] = applied.get("status")
        snapshot["reallocation_apply_reason"] = applied.get("reason")

    snapshot_path = PortfolioMonitorService.save(snapshot)
    return {
        "holdings_refreshed": refreshed,
        "holding_refresh_failures": failures,
        "coverage": coverage,
        "monitoring_snapshot": snapshot_path,
        "proposed_portfolio_update": updated_path,
        "reallocation_status": applied.get("status"),
        "persistence_comparison": persistence_comparison,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh portfolio research, expand its candidate pool and update the proposed paper portfolio when warranted."
    )
    parser.add_argument(
        "--candidate-batch-size",
        type=int,
        default=12,
        help="Number of sector-diverse universe candidates to research after holdings refresh.",
    )
    parser.add_argument(
        "--skip-holding-refresh",
        action="store_true",
        help="Use only for a quick monitoring check; scheduled runs should refresh current holdings.",
    )
    args = parser.parse_args()
    result = run_cycle(
        candidate_batch_size=args.candidate_batch_size,
        refresh_current_holdings=not args.skip_holding_refresh,
    )
    print(f"Current holdings refreshed: {result['holdings_refreshed']}")
    if result["holding_refresh_failures"]:
        print("Holding refresh failures: " + ", ".join(result["holding_refresh_failures"]))
    print("Candidate coverage status: " + str((result["coverage"] or {}).get("status")))
    print("Reallocation status: " + str(result["reallocation_status"]))
    comparison = result["persistence_comparison"]
    if comparison["status"] == "MATCH":
        print("PostgreSQL comparison: MATCH")
    elif comparison["status"] != "DISABLED":
        print(
            "PostgreSQL comparison needs attention: "
            + str(comparison.get("status"))
            + " "
            + str(comparison.get("mismatches") or comparison.get("reason") or "")
        )
    print("Monitoring snapshot: " + str(result["monitoring_snapshot"]))
    if result["proposed_portfolio_update"]:
        print("Updated proposed paper portfolio: " + str(result["proposed_portfolio_update"]))
    print("No broker trade has been placed.")


if __name__ == "__main__":
    main()
