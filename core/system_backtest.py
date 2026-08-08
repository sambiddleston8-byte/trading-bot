import yfinance as yf

from datetime import datetime


class SystemBacktest:

    def __init__(
        self,
        benchmark="^GSPC",
    ):

        self.benchmark = benchmark

    # --------------------------------
    # Historical Price Data
    # --------------------------------

    def get_prices(
        self,
        symbol,
        start,
        end,
    ):

        try:

            data = yf.Ticker(
                symbol
            ).history(
                start=start,
                end=end,
            )

            if data.empty:
                return None

            return data

        except Exception as error:

            print(
                f"{symbol} data failed: {error}"
            )

            return None

    # --------------------------------
    # Simple Score Strategy
    # --------------------------------

    def generate_signal(
        self,
        score,
    ):

        if score >= 85:
            return "STRONG BUY"

        if score >= 75:
            return "BUY"

        if score >= 65:
            return "HOLD"

        return "AVOID"

    # --------------------------------
    # Backtest
    # --------------------------------

    def run(
        self,
        symbol,
        score,
        start,
        end,
    ):

        data = self.get_prices(
            symbol,
            start,
            end,
        )

        if data is None:
            return {
                "Ticker": symbol,
                "Error": "No price data",
            }

        signal = self.generate_signal(
            score
        )

        start_price = float(
            data["Close"].iloc[0]
        )

        end_price = float(
            data["Close"].iloc[-1]
        )

        stock_return = (
            (
                end_price
                / start_price
            ) - 1
        ) * 100

        benchmark = self.get_prices(
            self.benchmark,
            start,
            end,
        )

        benchmark_return = None

        if benchmark is not None:

            benchmark_start = float(
                benchmark[
                    "Close"
                ].iloc[0]
            )

            benchmark_end = float(
                benchmark[
                    "Close"
                ].iloc[-1]
            )

            benchmark_return = (
                (
                    benchmark_end
                    / benchmark_start
                ) - 1
            ) * 100

        relative_return = None

        if benchmark_return is not None:

            relative_return = (
                stock_return
                - benchmark_return
            )

        return {

            "Ticker":
                symbol,

            "Signal":
                signal,

            "Score":
                score,

            "Start":
                start,

            "End":
                end,

            "Start Price":
                round(
                    start_price,
                    2,
                ),

            "End Price":
                round(
                    end_price,
                    2,
                ),

            "Stock Return %":
                round(
                    stock_return,
                    2,
                ),

            "Benchmark Return %":
                (
                    round(
                        benchmark_return,
                        2,
                    )
                    if benchmark_return is not None
                    else None
                ),

            "Relative Return %":
                (
                    round(
                        relative_return,
                        2,
                    )
                    if relative_return is not None
                    else None
                ),
        }

    # --------------------------------
    # Multi-stock Backtest
    # --------------------------------

    def run_universe(
        self,
        scores,
        start,
        end,
    ):

        results = []

        for symbol, score in scores.items():

            print(
                f"Backtesting {symbol}..."
            )

            result = self.run(
                symbol,
                score,
                start,
                end,
            )

            results.append(
                result
            )

        return results

    # --------------------------------
    # Summary
    # --------------------------------

    def summary(
        self,
        results,
    ):

        valid = [

            result

            for result in results

            if "Stock Return %" in result
        ]

        if not valid:

            return {

                "Results": 0,

                "Average Return": 0,

                "Average Relative Return": 0,

            }

        average_return = (
            sum(
                result[
                    "Stock Return %"
                ]
                for result in valid
            )
            / len(valid)
        )

        relative_values = [

            result[
                "Relative Return %"
            ]

            for result in valid

            if result[
                "Relative Return %"
            ] is not None
        ]

        average_relative = 0

        if relative_values:

            average_relative = (
                sum(
                    relative_values
                )
                / len(
                    relative_values
                )
            )

        return {

            "Results":
                len(valid),

            "Average Return":
                round(
                    average_return,
                    2,
                ),

            "Average Relative Return":
                round(
                    average_relative,
                    2,
                ),
        }


if __name__ == "__main__":

    backtest = SystemBacktest()

    scores = {

        "NVDA": 85,

        "MSFT": 80,

        "GOOGL": 78,

        "AMZN": 82,

        "META": 80,

        "AAPL": 72,

    }

    results = backtest.run_universe(
        scores,
        "2023-01-01",
        "2025-01-01",
    )

    print()
    print("=" * 60)
    print("SYSTEM BACKTEST")
    print("=" * 60)

    for result in results:

        print()
        print(
            result
        )

    print()
    print(
        backtest.summary(
            results
        )
    )