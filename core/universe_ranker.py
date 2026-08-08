import json
import os
from datetime import datetime

from core.stock_universe import StockUniverse
from core.historical_signal import HistoricalSignalEngine


class UniverseRanker:

    def __init__(
        self,
        output_path="data/universe_rankings.json",
    ):

        self.output_path = output_path

        directory = os.path.dirname(
            self.output_path
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True,
            )

        self.signal_engine = (
            HistoricalSignalEngine()
        )

    # --------------------------------
    # Load Prices
    # --------------------------------

    def load_prices(
        self,
        symbol,
    ):

        try:

            import yfinance as yf

            data = yf.Ticker(
                symbol
            ).history(
                period="2y",
                auto_adjust=True,
            )

            if data.empty:
                return None

            return data

        except Exception as error:

            print(
                f"{symbol} failed: "
                f"{error}"
            )

            return None

    # --------------------------------
    # Score One Stock
    # --------------------------------

    def analyse(
        self,
        symbol,
    ):

        prices = self.load_prices(
            symbol
        )

        if prices is None:
            return None

        try:

            signal = (
                self.signal_engine
                .generate_signal(
                    prices
                )
            )

        except Exception as error:

            print(
                f"{symbol} signal failed: "
                f"{error}"
            )

            return None

        if not signal:

            return None

        return {

            "Ticker":
                symbol,

            "Signal":
                signal.get(
                    "Signal",
                    "UNKNOWN",
                ),

            "Score":
                signal.get(
                    "Score",
                    50,
                ),

            "Fast MA":
                signal.get(
                    "Fast MA"
                ),

            "Slow MA":
                signal.get(
                    "Slow MA"
                ),

            "Momentum":
                signal.get(
                    "Momentum"
                ),

            "Analysed":
                datetime.now().isoformat(),

        }

    # --------------------------------
    # Rank Universe
    # --------------------------------

    def rank(
        self,
        symbols,
    ):

        results = []

        total = len(
            symbols
        )

        for index, symbol in enumerate(
            symbols,
            start=1,
        ):

            print(
                f"[{index}/{total}] "
                f"Analysing {symbol}..."
            )

            result = self.analyse(
                symbol
            )

            if result is None:
                continue

            results.append(
                result
            )

        # --------------------------------
        # Sort
        # --------------------------------

        results.sort(
            key=lambda item: (
                item.get(
                    "Score",
                    0,
                )
            ),
            reverse=True,
        )

        # --------------------------------
        # Add Rank
        # --------------------------------

        for rank, result in enumerate(
            results,
            start=1,
        ):

            result[
                "Rank"
            ] = rank

        return results

    # --------------------------------
    # Save
    # --------------------------------

    def save(
        self,
        results,
    ):

        output = {

            "Timestamp":
                datetime.now().isoformat(),

            "Universe Size":
                len(results),

            "Rankings":
                results,

        }

        with open(
            self.output_path,
            "w",
        ) as file:

            json.dump(
                output,
                file,
                indent=2,
                default=str,
            )

        return output

    # --------------------------------
    # Print
    # --------------------------------

    def print_top(
        self,
        results,
        count=20,
    ):

        print()
        print("=" * 80)
        print("TOP STOCKS")
        print("=" * 80)
        print()

        for result in results[:count]:

            print(
                f"{result['Rank']:>3}. "
                f"{result['Ticker']:<6} "
                f"Score: "
                f"{result['Score']:>5} "
                f"Signal: "
                f"{result['Signal']}"
            )

    # --------------------------------
    # Full Run
    # --------------------------------

    def run(
        self,
    ):

        universe = (
            StockUniverse()
        )

        symbols = universe.load()

        if not symbols:

            symbols = universe.build(
                validate=False
            )

        print()
        print(
            f"Universe contains "
            f"{len(symbols)} stocks."
        )

        results = self.rank(
            symbols
        )

        self.save(
            results
        )

        self.print_top(
            results
        )

        return results


if __name__ == "__main__":

    ranker = UniverseRanker()

    results = ranker.run()

    print()
    print(
        f"Ranked "
        f"{len(results)} stocks."
    )

    print(
        "Saved to "
        "data/universe_rankings.json"
    )