import pandas as pd


def sma(data: pd.Series, period: int):
    return data.rolling(period).mean()


def ema(data: pd.Series, period: int):
    return data.ewm(span=period, adjust=False).mean()

def rsi(data: pd.Series, period: int = 14):
    delta = data.diff()

    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))

def macd(data: pd.Series):
    ema12 = data.ewm(span=12, adjust=False).mean()
    ema26 = data.ewm(span=26, adjust=False).mean()

    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()

    return macd_line, signal_line