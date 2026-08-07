from bots.fundamental.analyser import FundamentalAnalyser
from bots.technical.analyser import TechnicalAnalyser


class DecisionEngine:

    def __init__(self):

        self.fundamental = FundamentalAnalyser()
        self.technical = TechnicalAnalyser()

    def analyse(self, symbol):

        fundamental = self.fundamental.analyse(symbol)
        technical = self.technical.analyse(symbol)

        fundamental_score = fundamental["Fundamental Score"]
        technical_score = technical["Technical Score"]

        overall = round(
            (fundamental_score + technical_score) / 2,
            1
        )

        return {
            "Ticker": symbol,
            "Fundamental Score": fundamental_score,
            "Technical Score": technical_score,
            "Overall Score": overall
        }