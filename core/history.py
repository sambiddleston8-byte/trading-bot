import yfinance as yf


class HistoryEngine:

    def get_history(self, symbol, period="5y"):

        ticker = yf.Ticker(symbol)

        return ticker.history(period=period)