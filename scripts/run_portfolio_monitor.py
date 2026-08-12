from __future__ import annotations

"""Run one paper-portfolio monitoring pass and save its dated snapshot.

Schedule this script outside the web page when continuous monitoring is wanted.
It may update the proposed paper portfolio when evidence supports a balanced
reallocation. It never submits a broker trade.
"""

import argparse

from core.application.portfolio_construction_service import PortfolioConstructionService
from core.application.portfolio_monitor_service import PortfolioMonitorService
from core.application.research_service import ResearchService
from core.operations import JobRunGuard


def refresh_holding_research(portfolio: dict) -> tuple[int, list[str]]:
    """Re-run the full company-research pipeline before the monitor reviews it.

    This optional step updates fundamentals, valuation, technical signals,
    catalysts, sentiment, thesis challenge and audit data for each current
    holding. It does not construct a new portfolio or place any trade.
    """
    refreshed = 0
    failures: list[str] = []
    for holding in portfolio.get("holdings", []):
        ticker = str(holding.get("ticker") or "").upper()
        if not ticker:
            continue
        try:
            ResearchService.run(ticker)
            refreshed += 1
        except Exception:
            failures.append(ticker)
    return refreshed, failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a research-led monitoring snapshot and update the proposed paper portfolio when warranted."
    )
    parser.add_argument(
        "--refresh-research",
        action="store_true",
        help="Refresh every current holding through the full research pipeline before review.",
    )
    args = parser.parse_args()

    with JobRunGuard("portfolio-monitor"):
        run_monitor(refresh_research=args.refresh_research)


def run_monitor(*, refresh_research: bool = False) -> None:
    portfolio, _ = PortfolioConstructionService.latest_portfolio()
    if portfolio is None:
        raise RuntimeError("No current proposed portfolio is available to monitor.")

    if refresh_research:
        refreshed, failures = refresh_holding_research(portfolio)
        print(f"Research refreshed: {refreshed}")
        if failures:
            print("Research refresh needs attention for: " + ", ".join(failures))

    snapshot = PortfolioMonitorService.evaluate(portfolio)
    applied_update = PortfolioMonitorService.apply_reallocation(portfolio, snapshot)
    if applied_update.get("status") == "APPLIED":
        persisted = PortfolioConstructionService.persist_reallocation(
            previous_portfolio=portfolio,
            applied_result=applied_update,
            checked_at=str(snapshot.get("checked_at")),
            monitor_version=PortfolioMonitorService.VERSION,
        )
        applied_update["portfolio"] = persisted["portfolio"]
        updated_path = persisted["snapshot_path"]
        snapshot["reallocation_plan"] = applied_update["reallocation_plan"]
        snapshot["applied_portfolio_changes"] = applied_update["changes"]
        snapshot["applied_portfolio_path"] = str(updated_path)
        snapshot["applied_decision_ids"] = [
            record["decision_id"] for record in persisted["ledger_records"]
        ]
        comparison = persisted["persistence_comparison"]
        if comparison["status"] == "MATCH":
            print("PostgreSQL comparison: MATCH")
        elif comparison["status"] != "DISABLED":
            print(
                "PostgreSQL comparison needs attention: "
                + str(comparison.get("status"))
                + " "
                + str(comparison.get("mismatches") or comparison.get("reason") or "")
            )
        print(f"Updated proposed paper portfolio: {updated_path}")
    elif applied_update.get("status", "NO_CHANGE").startswith("NOT_APPLIED"):
        snapshot["reallocation_apply_status"] = applied_update.get("status")
        snapshot["reallocation_apply_reason"] = applied_update.get("reason")
        print("Proposed paper portfolio unchanged: " + str(applied_update.get("reason")))
    path = PortfolioMonitorService.save(snapshot)
    summary = snapshot.get("summary") or {}
    print(f"Saved monitoring snapshot: {path}")
    print(f"Positions monitored: {summary.get('position_count', 0)}")
    print(f"Research-led allocation changes: {summary.get('allocation_changes_required', 0)}")
    print("No broker trade has been placed.")


if __name__ == "__main__":
    main()
