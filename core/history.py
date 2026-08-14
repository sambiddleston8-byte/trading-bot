from core.data_sources.yahoo_history_access import YahooHistoryClient


class HistoryEngine:

    def __init__(self, history_client=None):
        self.history_client = history_client or YahooHistoryClient()

    def get_history(self, symbol, period="5y"):
        return self.history_client.history(symbol, period=period).frame
