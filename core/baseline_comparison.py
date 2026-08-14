import math

from core.data_sources.yahoo_history_access import YahooHistoryClient


class BaselineComparison:

    def __init__(
        self,
        benchmark="^GSPC",
        initial_capital=100000,
        history_client=None,
    ):

        self.benchmark = benchmark
        self.initial_capital = initial_capital
        self.history_client = history_client or YahooHistoryClient()

    # --------------------------------
    # Historical Data
    # --------------------------------

    def get_history(
        self,
        symbol,
        start,
        end,
    ):

        try:

            data = self.history_client.history(
                symbol,
                start=start,
                end=end,
                auto_adjust=True,
            ).frame

            if data.empty:
                return None

            return data

        except Exception:

            print("Yahoo history read failed.")

            return None

    # --------------------------------
    # Buy & Hold Return
    # --------------------------------

    def buy_and_hold_return(
        self,
        symbol,
        start,
        end,
    ):

        data = self.get_history(
            symbol,
            start,
            end,
        )

        if data is None:
            return 0.0

        if len(data) < 2:
            return 0.0

        first = float(
            data["Close"].iloc[0]
        )

        last = float(
            data["Close"].iloc[-1]
        )

        if first <= 0:
            return 0.0

        return (
            (
                last
                / first
            ) - 1
        )

    # --------------------------------
    # Equal Weight Universe
    # --------------------------------

    def equal_weight_universe(
        self,
        symbols,
        start,
        end,
    ):

        if not symbols:
            return 0.0

        returns = []

        for symbol in symbols:

            print(
                f"  Calculating "
                f"{symbol}..."
            )

            result = (
                self.buy_and_hold_return(
                    symbol,
                    start,
                    end,
                )
            )

            returns.append(
                result
            )

        if not returns:
            return 0.0

        return (
            sum(returns)
            / len(returns)
        )

    # --------------------------------
    # Benchmark
    # --------------------------------

    def benchmark_result(
        self,
        start,
        end,
    ):

        return self.buy_and_hold_return(
            self.benchmark,
            start,
            end,
        )

    # --------------------------------
    # Cash
    # --------------------------------

    def cash_result(self):

        return 0.0

    # --------------------------------
    # CAGR
    # --------------------------------

    def calculate_cagr(
        self,
        total_return,
        years,
    ):

        if years <= 0:
            return 0.0

        return (
            (
                1
                + total_return
            )
            ** (
                1 / years
            )
            - 1
        )

    # --------------------------------
    # Build Comparison
    # --------------------------------

    def compare(
        self,
        symbols,
        start="2020-01-01",
        end="2026-01-01",
    ):

        print()
        print(
            "=" * 70
        )
        print(
            "BASELINE COMPARISON"
        )
        print(
            "=" * 70
        )

        print()
        print(
            "Calculating S&P 500..."
        )

        benchmark_return = (
            self.benchmark_result(
                start,
                end,
            )
        )

        print(
            "Calculating equal-weight "
            "universe..."
        )

        universe_return = (
            self.equal_weight_universe(
                symbols,
                start,
                end,
            )
        )

        cash_return = (
            self.cash_result()
        )

        years = 6

        benchmark_cagr = (
            self.calculate_cagr(
                benchmark_return,
                years,
            )
        )

        universe_cagr = (
            self.calculate_cagr(
                universe_return,
                years,
            )
        )

        cash_cagr = (
            self.calculate_cagr(
                cash_return,
                years,
            )
        )

        report = {

            "Initial Capital":
                self.initial_capital,

            "Period":
                f"{start} -> {end}",

            "S&P 500": {

                "Return %":
                    round(
                        benchmark_return
                        * 100,
                        2,
                    ),

                "Final Capital":
                    round(
                        self.initial_capital
                        * (
                            1
                            + benchmark_return
                        ),
                        2,
                    ),

                "CAGR %":
                    round(
                        benchmark_cagr
                        * 100,
                        2,
                    ),
            },

            "Equal Weight Universe": {

                "Return %":
                    round(
                        universe_return
                        * 100,
                        2,
                    ),

                "Final Capital":
                    round(
                        self.initial_capital
                        * (
                            1
                            + universe_return
                        ),
                        2,
                    ),

                "CAGR %":
                    round(
                        universe_cagr
                        * 100,
                        2,
                    ),
            },

            "Cash": {

                "Return %":
                    0.0,

                "Final Capital":
                    self.initial_capital,

                "CAGR %":
                    round(
                        cash_cagr
                        * 100,
                        2,
                    ),
            },
        }

        return report

    # --------------------------------
    # Print
    # --------------------------------

    def print_report(
        self,
        report,
    ):

        print()
        print(
            "=" * 70
        )
        print(
            "BASELINE RESULTS"
        )
        print(
            "=" * 70
        )

        print()

        for name in (
            "S&P 500",
            "Equal Weight Universe",
            "Cash",
        ):

            data = report[
                name
            ]

            print(
                f"{name}"
            )

            print(
                f"  Return: "
                f"{data['Return %']}%"
            )

            print(
                f"  Final Capital: "
                f"${data['Final Capital']:,.2f}"
            )

            print(
                f"  CAGR: "
                f"{data['CAGR %']}%"
            )

            print()


if __name__ == "__main__":

    symbols = [

        "NVDA",
        "MSFT",
        "GOOGL",
        "AMZN",
        "META",
        "AAPL",

    ]

    comparison = (
        BaselineComparison()
    )

    report = comparison.compare(
        symbols,
        start="2020-01-01",
        end="2026-01-01",
    )

    comparison.print_report(
        report
    )
