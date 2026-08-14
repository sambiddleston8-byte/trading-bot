from core.data_sources.yahoo_history_access import YahooHistoryAccessError, YahooHistoryClient


class BacktestEngine:

    def __init__(self, history_client=None):
        self.history_client = history_client or YahooHistoryClient()

    def get_return(
        self,
        symbol,
        period="1y",
    ):

        try:
            history = self.history_client.history(symbol, period=period).frame
        except (YahooHistoryAccessError, TypeError, ValueError):
            return None

        if len(history) < 2:
            return None

        start = float(history["Close"].iloc[0])
        end = float(history["Close"].iloc[-1])

        return round(((end - start) / start) * 100, 2)

    def compare(
        self,
        symbol,
        benchmark="SPY",
        period="1y",
    ):

        stock = self.get_return(symbol, period)
        if stock is None:
            return None
        market = self.get_return(benchmark, period)

        if market is None:
            return None

        return {
            "Ticker": symbol,
            "Stock Return": stock,
            "Benchmark Return": market,
            "Outperformance": round(stock - market, 2),
            "Beat Market": stock > market,
        }

    def compare_watchlist(
        self,
        watchlist,
        benchmark="SPY",
        period="1y",
    ):

        results = []

        for ticker in watchlist:

            result = self.compare(
                ticker,
                benchmark,
                period,
            )

            if result:
                results.append(result)

        return results
