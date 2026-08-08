from core.decision_engine import DecisionEngine
from core.portfolio_constructor import PortfolioConstructor


class PortfolioManager:

    def __init__(self):

        self.decision_engine = DecisionEngine()
        self.constructor = PortfolioConstructor()

    def analyse_universe(
        self,
        symbols,
    ):

        analyses = []

        for symbol in symbols:

            symbol = symbol.upper().strip()

            if not symbol:
                continue

            print(
                f"Analysing {symbol}..."
            )

            try:

                result = (
                    self.decision_engine.analyse(
                        symbol
                    )
                )

                analyses.append(
                    result
                )

            except Exception as error:

                print(
                    f"{symbol} failed: {error}"
                )

        return analyses

    def construct_portfolio(
        self,
        symbols,
    ):

        analyses = (
            self.analyse_universe(
                symbols
            )
        )

        portfolio = (
            self.constructor.construct(
                analyses
            )
        )

        return {

            "Analyses":
                analyses,

            "Portfolio":
                portfolio,

        }

    def print_portfolio(
        self,
        portfolio,
    ):

        data = portfolio.get(
            "Portfolio",
            {}
        )

        print()
        print("=" * 60)
        print("PROPOSED PORTFOLIO")
        print("=" * 60)
        print()

        positions = data.get(
            "Positions",
            []
        )

        if not positions:

            print(
                "No qualifying positions."
            )

        else:

            for position in positions:

                print(
                    f"{position['Ticker']:<10}"
                    f"{position['Allocation'] * 100:>7.2f}%"
                    f"  "
                    f"{position['Recommendation']:<12}"
                    f"Score: "
                    f"{position['Score']}"
                )

        print()

        print(
            f"Positions: "
            f"{data.get('Position Count', 0)}"
        )

        print(
            f"Invested: "
            f"{data.get('Invested %', 0)}%"
        )

        print(
            f"Cash: "
            f"{data.get('Cash %', 0)}%"
        )

        print()

        print("SECTOR EXPOSURE")
        print("-" * 60)

        sectors = data.get(
            "Sector Exposure",
            {}
        )

        if not sectors:

            print(
                "No sector exposure."
            )

        else:

            for sector, allocation in sectors.items():

                print(
                    f"{sector:<30}"
                    f"{allocation * 100:>7.2f}%"
                )

        print()
        print("=" * 60)


if __name__ == "__main__":

    manager = PortfolioManager()

    symbols = [
        "NVDA",
        "MSFT",
        "GOOGL",
        "AMZN",
        "META",
        "AAPL",
    ]

    result = manager.construct_portfolio(
        symbols
    )

    manager.print_portfolio(
        result
    )