"""Command-line entry point for the portfolio-construction prototype."""

import argparse

from core.application.portfolio_coverage_service import PortfolioCoverageService
from core.application.portfolio_construction_service import PortfolioConstructionService
from core.portfolio_manager import PortfolioManager


def main() -> None:
    parser = argparse.ArgumentParser(description="Portfolio construction prototype")
    parser.add_argument(
        "--research",
        action="store_true",
        help="Run one paced, checkpointed research batch before checking readiness.",
    )
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()

    if args.research:
        cycle = PortfolioCoverageService.run_cycle(
            target_holdings=8,
            batch_size=args.batch_size,
            delay_seconds=args.delay,
        )
        batch = cycle.get("batch") or {}
        print(
            "Research cycle: "
            f"{cycle['status']} "
            f"({batch.get('completed_count', 0)} completed, "
            f"{batch.get('error_count', 0)} errors)"
        )

    status = PortfolioCoverageService.status(target_holdings=8)
    universe = status["universe"]
    readiness = status["readiness"]
    print("PORTFOLIO CONSTRUCTION PROTOTYPE")
    print(f"Universe: {universe['unique_ticker_count']} companies (S&P 500 + Nasdaq-100)")
    print(f"Portfolio-ready candidates: {readiness['eligible_count']}")
    print(readiness["message"])

    if readiness["ready"]:
        manager = PortfolioManager()
        manager.print_portfolio(manager.construct_portfolio(target_holdings=8))
    else:
        print("Run one paced research cycle before attempting construction.")


if __name__ == "__main__":
    main()
