from bots.business_quality.analyser import BusinessQualityAnalyser
from bots.valuation.analyser import ValuationAnalyser
from bots.technical.analyser import TechnicalAnalyser
from bots.risk.analyser import RiskAnalyser
from bots.news.analyser import NewsAnalyser


class AnalysisEngine:

    def __init__(self):

        self.business = BusinessQualityAnalyser()
        self.valuation = ValuationAnalyser()
        self.technical = TechnicalAnalyser()
        self.risk = RiskAnalyser()
        self.news = NewsAnalyser()

    def analyse(self, symbol):

        return {

            "Business": self.business.analyse(symbol),

            "Valuation": self.valuation.analyse(symbol),

            "Technical": self.technical.analyse(symbol),

            "Risk": self.risk.analyse(symbol),

            "News": self.news.analyse(symbol)

        }