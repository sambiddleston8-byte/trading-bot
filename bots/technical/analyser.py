from core.history import HistoryEngine


class TechnicalAnalyser:

    def __init__(self):
        self.history = HistoryEngine()

    def moving_average_score(self, symbol):

        data = self.history.get_history(symbol)

        close = data["Close"]

        ma50 = close.rolling(50).mean().iloc[-1]
        ma200 = close.rolling(200).mean().iloc[-1]

        if ma50 > ma200:
            return 100
        else:
            return 0

    def momentum_score(self, symbol):

        data = self.history.get_history(symbol)

        start_price = data["Close"].iloc[0]
        end_price = data["Close"].iloc[-1]

        change = (end_price - start_price) / start_price

        if change >= 0.50:
            return 100
        elif change >= 0.30:
            return 80
        elif change >= 0.15:
            return 60
        elif change >= 0:
            return 40
        else:
            return 0

    def analyse(self, symbol):

        moving_average = self.moving_average_score(symbol)
        momentum = self.momentum_score(symbol)

        technical_score = round((moving_average + momentum) / 2, 1)

        return {
            "Moving Average": moving_average,
            "Momentum": momentum,
            "Technical Score": technical_score
        }