import yfinance as yf


class EarningsSource:

    def __init__(self):
        self._cache = {}

    def get_ticker(self, symbol):

        symbol = symbol.upper().strip()

        if symbol not in self._cache:
            self._cache[symbol] = yf.Ticker(symbol)

        return self._cache[symbol]

    def fetch(self, symbol):

        ticker = self.get_ticker(symbol)

        estimates = {}

        try:
            earnings = ticker.earnings_estimate

            if earnings is not None and not earnings.empty:

                for period in earnings.index:

                    row = earnings.loc[period]

                    estimates[str(period)] = {
                        "eps_avg": self._number(
                            row.get("avg")
                        ),
                        "eps_low": self._number(
                            row.get("low")
                        ),
                        "eps_high": self._number(
                            row.get("high")
                        ),
                        "analysts": self._number(
                            row.get(
                                "numberOfAnalysts"
                            )
                        ),
                        "year_ago_eps": self._number(
                            row.get("yearAgoEps")
                        ),
                        "growth": self._number(
                            row.get("growth")
                        ),
                    }

        except Exception:
            pass

        return {
            "symbol": symbol,
            "earnings_estimates": estimates,
        }

    def _number(self, value):

        try:

            if value is None:
                return None

            value = float(value)

            if value != value:
                return None

            return value

        except (
            TypeError,
            ValueError,
        ):

            return None
