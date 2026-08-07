from bots.business_quality.analyser import BusinessQualityAnalyser
from bots.valuation.analyser import ValuationAnalyser
from bots.technical.analyser import TechnicalAnalyser
from bots.risk.analyser import RiskAnalyser

from core.scoring import calculate_overall_score
from core.rating import get_rating


class DecisionEngine:

    def __init__(self):

        self.business = BusinessQualityAnalyser()
        self.valuation = ValuationAnalyser()
        self.technical = TechnicalAnalyser()
        self.risk = RiskAnalyser()

    def analyse(self, symbol):

        business = self.business.analyse(symbol)
        valuation = self.valuation.analyse(symbol)
        technical = self.technical.analyse(symbol)
        risk = self.risk.analyse(symbol)

        quality = business["Business Quality"]
        value = valuation["Valuation Score"]
        tech = technical["Technical Score"]
        risk_score = risk["Risk Score"]

        overall = calculate_overall_score(
            quality,
            value,
            tech,
        )

        return {
            "Ticker": symbol,
            "Business Quality": quality,
            "Valuation": value,
            "Technical": tech,
            "Risk": risk_score,
            "Overall Score": overall,
            "Rating": get_rating(overall),
        }