from core.data_sources.yahoo_history_access import YahooHistoryClient


def get_data(symbol, period="6mo", *, history_client=None):
    client = history_client or YahooHistoryClient()
    return client.history(symbol, period=period).frame


def get_price(symbol, *, history_client=None):
    data = get_data(symbol, history_client=history_client)

    if data.empty:
        return None

    return float(data["Close"].iloc[-1])
