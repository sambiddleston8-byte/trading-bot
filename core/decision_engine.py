from bots.business_quality.analyser import BusinessQualityAnalyser
from bots.valuation.analyser import ValuationAnalyser
from bots.technical.analyser import TechnicalAnalyser
from bots.risk.analyser import RiskAnalyser
from bots.news.analyser import NewsAnalyser
from bots.summary.analyser import SummaryAnalyser
from bots.report.analyser import ReportAnalyser

from core.analysis import InvestmentAnalysis
from core.scoring import calculate_overall_score
from core.rating import get_rating


class DecisionEngine:

    def __init__(self):

        self.business = BusinessQualityAnalyser()
        self.valuation = ValuationAnalyser()
        self.technical = TechnicalAnalyser()
        self.risk = RiskAnalyser()
        self.news = NewsAnalyser()
        self.summary = SummaryAnalyser()
        self.report = ReportAnalyser()

    def analyse(self, symbol):

        business = self.business.analyse(symbol)
        valuation = self.valuation.analyse(symbol)
        technical = self.technical.analyse(symbol)
        risk = self.risk.analyse(symbol)
        news = self.news.analyse(symbol)

        analysis = InvestmentAnalysis(symbol)

        analysis.business_quality = business["Business Quality"]
        analysis.valuation = valuation["Valuation Score"]
        analysis.technical = technical["Technical Score"]
        analysis.risk = risk["Risk Score"]
        analysis.news = news["News Score"]
        analysis.catalyst = news["Catalyst Score"]

        analysis.headlines = news["Headlines"]
        analysis.catalysts = news["Catalysts"]

        analysis.overall = calculate_overall_score(
            analysis.business_quality,
            analysis.valuation,
            analysis.technical,
            analysis.risk,
            analysis.news,
            analysis.catalyst,
        )

        analysis.rating = get_rating(analysis.overall)

        summary = self.summary.analyse(analysis)

        analysis.summary = summary["Summary"]

        report = self.report.build(analysis)

        return {
            "Ticker": analysis.ticker,
            "Business Quality": analysis.business_quality,
            "Valuation": analysis.valuation,
            "Technical": analysis.technical,
            "Risk": analysis.risk,
            "News": analysis.news,
            "Catalyst": analysis.catalyst,
            "Overall Score": analysis.overall,
            "Rating": analysis.rating,
            "Strengths": summary["Strengths"],
            "Weaknesses": summary["Weaknesses"],
            "Summary": analysis.summary,
            "Report": report,
            "Catalysts": analysis.catalysts,
        }