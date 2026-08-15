import json
import os
from datetime import datetime

from core.data_sources.yahoo_fast_info_access import YahooFastInfoClient


class StockUniverse:

    def __init__(
        self,
        path="data/stock_universe.json",
        price_client=None,
    ):

        self.path = path

        self.price_client = (
            price_client
            if price_client is not None
            else YahooFastInfoClient()
        )

        directory = os.path.dirname(
            self.path
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True,
            )

    # --------------------------------
    # Default Universe
    # --------------------------------

    def default_symbols(self):

        return [

            # Technology
            "AAPL",
            "MSFT",
            "NVDA",
            "AVGO",
            "ORCL",
            "CRM",
            "ADBE",
            "AMD",
            "CSCO",
            "IBM",
            "INTU",
            "NOW",
            "QCOM",
            "TXN",
            "AMAT",
            "ADI",
            "MU",
            "LRCX",
            "KLAC",
            "PANW",
            "CRWD",
            "FTNT",
            "SNPS",
            "CDNS",

            # Communication
            "GOOGL",
            "META",
            "NFLX",
            "DIS",
            "CMCSA",
            "TMUS",
            "VZ",
            "T",

            # Consumer
            "AMZN",
            "TSLA",
            "HD",
            "LOW",
            "MCD",
            "NKE",
            "SBUX",
            "TJX",
            "TGT",
            "COST",
            "WMT",
            "BKNG",
            "ABNB",
            "GM",
            "F",
            "ORLY",
            "AZO",

            # Financials
            "JPM",
            "BAC",
            "WFC",
            "C",
            "GS",
            "MS",
            "BLK",
            "SCHW",
            "AXP",
            "V",
            "MA",
            "PYPL",
            "COF",
            "CB",
            "MMC",
            "AON",

            # Healthcare
            "LLY",
            "UNH",
            "JNJ",
            "MRK",
            "ABBV",
            "PFE",
            "TMO",
            "ABT",
            "DHR",
            "ISRG",
            "AMGN",
            "GILD",
            "VRTX",
            "REGN",
            "BSX",
            "SYK",
            "MDT",
            "ELV",

            # Industrials
            "CAT",
            "DE",
            "GE",
            "HON",
            "RTX",
            "LMT",
            "BA",
            "UPS",
            "UNP",
            "CSX",
            "ETN",
            "EMR",
            "ITW",
            "PH",
            "ROK",
            "GD",

            # Energy
            "XOM",
            "CVX",
            "COP",
            "SLB",
            "EOG",
            "OXY",
            "MPC",
            "PSX",
            "VLO",

            # Consumer Staples
            "PG",
            "KO",
            "PEP",
            "PM",
            "MO",
            "CL",
            "MDLZ",
            "KHC",

            # Utilities
            "NEE",
            "DUK",
            "SO",
            "D",
            "AEP",

            # Real Estate
            "PLD",
            "AMT",
            "EQIX",
            "SPG",
            "O",

            # Materials
            "LIN",
            "APD",
            "SHW",
            "FCX",
            "NEM",
            "NUE",
            "DOW",

        ]

    # --------------------------------
    # Clean Universe
    # --------------------------------

    def clean(
        self,
        symbols,
    ):

        cleaned = []

        for symbol in symbols:

            symbol = (
                symbol
                .upper()
                .strip()
            )

            if not symbol:
                continue

            if symbol in cleaned:
                continue

            cleaned.append(
                symbol
            )

        return sorted(
            cleaned
        )

    # --------------------------------
    # Basic Validation
    # --------------------------------

    def validate_symbol(
        self,
        symbol,
    ):

        try:

            self.price_client.last_price(
                symbol
            )

            return True

        except Exception:

            return False

    # --------------------------------
    # Validate Universe
    # --------------------------------

    def validate(
        self,
        symbols,
    ):

        valid = []
        invalid = []

        for index, symbol in enumerate(
            symbols,
            start=1,
        ):

            print(
                f"[{index}/{len(symbols)}] "
                f"Checking {symbol}..."
            )

            if self.validate_symbol(
                symbol
            ):

                valid.append(
                    symbol
                )

            else:

                invalid.append(
                    symbol
                )

        return {
            "Valid":
                valid,

            "Invalid":
                invalid,
        }

    # --------------------------------
    # Save
    # --------------------------------

    def save(
        self,
        symbols,
        validation=None,
    ):

        data = {

            "Updated":
                datetime.now().isoformat(),

            "Count":
                len(symbols),

            "Symbols":
                symbols,

        }

        if validation:

            data[
                "Validation"
            ] = validation

        with open(
            self.path,
            "w",
        ) as file:

            json.dump(
                data,
                file,
                indent=2,
            )

    # --------------------------------
    # Load
    # --------------------------------

    def load(self):

        if not os.path.exists(
            self.path
        ):

            return []

        try:

            with open(
                self.path,
                "r",
            ) as file:

                data = json.load(
                    file
                )

            return data.get(
                "Symbols",
                [],
            )

        except (
            FileNotFoundError,
            json.JSONDecodeError,
        ):

            return []

    # --------------------------------
    # Build
    # --------------------------------

    def build(
        self,
        validate=False,
    ):

        symbols = (
            self.clean(
                self.default_symbols()
            )
        )

        validation = None

        if validate:

            validation = (
                self.validate(
                    symbols
                )
            )

            symbols = (
                validation["Valid"]
            )

        self.save(
            symbols,
            validation,
        )

        return symbols


if __name__ == "__main__":

    universe = StockUniverse()

    symbols = universe.build(
        validate=False
    )

    print()
    print("=" * 60)
    print("STOCK UNIVERSE")
    print("=" * 60)

    print(
        f"Stocks: {len(symbols)}"
    )

    print()

    print(
        symbols
    )

    print()

    print(
        f"Saved to: "
        f"{universe.path}"
    )
