from core.indicators import sma, rsi, macd


def should_buy(data):
    close = data["Close"]

    sma20 = sma(close, 20)
    sma50 = sma(close, 50)
    rsi14 = rsi(close)
    macd_line, signal_line = macd(close)

    return (
        sma20.iloc[-1] > sma50.iloc[-1]
        and rsi14.iloc[-1] < 30
        and macd_line.iloc[-1] > signal_line.iloc[-1]
    )


def should_sell(data):
    close = data["Close"]

    sma20 = sma(close, 20)
    sma50 = sma(close, 50)
    rsi14 = rsi(close)
    macd_line, signal_line = macd(close)

    return (
        sma20.iloc[-1] < sma50.iloc[-1]
        or rsi14.iloc[-1] > 70
        or macd_line.iloc[-1] < signal_line.iloc[-1]
    )