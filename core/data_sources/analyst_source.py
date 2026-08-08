import yfinance as yf


class AnalystSource:

    def __init__(self):
        self._cache = {}

    def get_ticker(self, symbol):

        symbol = symbol.upper().strip()

        if symbol not in self._cache:
            self._cache[symbol] = yf.Ticker(symbol)

        return self._cache[symbol]

    def fetch(self, symbol):

        ticker = self.get_ticker(symbol)

        revenue = None
        earnings = None

        try:
            revenue = ticker.revenue_estimate
        except Exception:
            revenue = None

        try:
            earnings = ticker.earnings_estimate
        except Exception:
            earnings = None

        result = {
            "symbol": symbol,
            "revenue_estimates": self._extract(
                revenue
            ),
            "earnings_estimates": self._extract(
                earnings
            ),
        }

        return result

    def _extract(self, dataframe):

        if dataframe is None:
            return {}

        if getattr(dataframe, "empty", True):
            return {}

        result = {}

        for period in dataframe.index:

            row = dataframe.loc[period]

            values = {}

            for column in dataframe.columns:

                value = row.get(
                    column
                )

                try:

                    if value != value:
                        value = None

                except Exception:
                    pass

                if value is not None:

                    try:
                        value = float(value)
                    except (
                        TypeError,
                        ValueError,
                    ):
                        value = str(value)

                values[column] = value

            result[str(period)] = values

        return result
