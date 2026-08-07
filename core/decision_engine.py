from bots.business_quality.analyser import BusinessQualityAnalyser
from bots.valuation.analyser import ValuationAnalyser
from bots.technical.analyser import TechnicalAnalyser

from core.rating import get_rating


class DecisionEngine:

    def __init__(self):

        self.business_quality = BusinessQualityAnalyser()
        self.valuation = ValuationAnalyser()
        self.technical = TechnicalAnalyser()

    def analyse(self, symbol):

        quality = self.business_quality.analyse(symbol)
        valuation = self.valuation.analyse(symbol)
        technical = self.technical.analyse(symbol)

        quality_score = quality["Business Quality"]
        valuation_score = valuation["Valuation Score"]
        technical_score = technical["Technical Score"]

        overall = round(

            (

                quality_score * 0.4 +

                valuation_score * 0.3 +

                technical_score * 0.3

            ),

            1

        )

        return {

            "Ticker": symbol,

            "Business Quality": quality_score,

            "Valuation": valuation_score,

            "Technical": technical_score,

            "Overall Score": overall,

            "Rating": get_rating(overall)

        }