from bots.fundamental.analyser import FundamentalAnalyser
from bots.technical.analyser import TechnicalAnalyser

from core.scoring import calculate_overall_score
from core.rating import get_rating


class DecisionEngine:

    def __init__(self):

        self.fundamental = FundamentalAnalyser()
        self.technical = TechnicalAnalyser()

    def analyse(self, symbol):

        fundamental = self.fundamental.analyse(symbol)
        technical = self.technical.analyse(symbol)

        fundamental_score = fundamental["Fundamental Score"]
        technical_score = technical["Technical Score"]

        overall_score = calculate_overall_score(
            fundamental_score,
            technical_score
        )

        rating = get_rating(overall_score)

        return {

            "Ticker": symbol,

            "Fundamental Score": fundamental_score,

            "Technical Score": technical_score,

            "Overall Score": overall_score,

            "Rating": rating

        }