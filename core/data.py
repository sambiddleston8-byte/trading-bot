import yfinance as yf


def get_data(symbol, period="6mo"):
    stock = yf.Ticker(symbol)
    return stock.history(period=period)


def get_price(symbol):
    data = get_data(symbol)

    if data.empty:
        return None

    return float(data["Close"].iloc[-1])