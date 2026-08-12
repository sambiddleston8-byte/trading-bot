import os
import pickle

import yfinance as yf


class MarketDataCache:

    def __init__(
        self,
        directory="data/market_cache",
    ):

        self.directory = directory

        os.makedirs(
            self.directory,
            exist_ok=True,
        )

    # --------------------------------
    # Cache Path
    # --------------------------------

    def _path(
        self,
        symbol,
    ):

        safe_symbol = (
            symbol
            .replace(
                "^",
                "_",
            )
            .replace(
                "/",
                "_",
            )
        )

        return os.path.join(
            self.directory,
            f"{safe_symbol}.pkl",
        )

    # --------------------------------
    # Download
    # --------------------------------

    def download(
        self,
        symbol,
        start="2018-01-01",
        end=None,
    ):

        print(
            f"Downloading {symbol}..."
        )

        data = yf.Ticker(
            symbol
        ).history(
            start=start,
            end=end,
            auto_adjust=True,
        )

        if data.empty:

            raise ValueError(
                f"No data returned for {symbol}"
            )

        with open(
            self._path(symbol),
            "wb",
        ) as file:

            pickle.dump(
                data,
                file,
            )

        return data

    # --------------------------------
    # Load
    # --------------------------------

    def load(
        self,
        symbol,
        start="2018-01-01",
        end=None,
    ):

        path = self._path(
            symbol
        )

        if not os.path.exists(
            path
        ):

            data = self.download(
                symbol,
                start,
                end,
            )

        else:

            print(
                f"Using cached {symbol}"
            )

            with open(
                path,
                "rb",
            ) as file:

                data = pickle.load(
                    file
                )

        # --------------------------------
        # Apply requested date range
        # --------------------------------

        if start:

            data = data[
                data.index
                >= start
            ]

        if end:

            data = data[
                data.index
                < end
            ]

        return data

    # --------------------------------
    # Download Universe
    # --------------------------------

    def load_universe(
        self,
        symbols,
        start="2018-01-01",
        end=None,
        allow_partial=False,
    ):

        data = {}
        failures = {}

        for symbol in symbols:

            try:

                data[
                    symbol
                ] = self.load(
                    symbol,
                    start,
                    end,
                )

            except Exception as error:

                failures[symbol] = error

                print(
                    f"{symbol} failed: "
                    f"{error}"
                )

        if failures and not allow_partial:
            failed_symbols = ", ".join(sorted(failures))
            raise RuntimeError(
                "Market data is incomplete; refusing to run a biased partial "
                f"universe. Failed symbols: {failed_symbols}"
            )

        return data


if __name__ == "__main__":

    cache = MarketDataCache()

    symbols = [

        "NVDA",
        "MSFT",
        "GOOGL",
        "AMZN",
        "META",
        "AAPL",
        "^GSPC",

    ]

    data = cache.load_universe(
        symbols
    )

    print()

    for symbol, prices in data.items():

        print(
            f"{symbol}: "
            f"{len(prices)} rows"
        )
