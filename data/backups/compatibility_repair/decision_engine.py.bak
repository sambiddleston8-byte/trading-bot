from bots.business_quality.analyser import BusinessQualityAnalyser
from bots.valuation.analyser import ValuationAnalyser
from bots.technical.analyser import TechnicalAnalyser
from bots.risk.analyser import RiskAnalyser
from bots.news.analyser import NewsAnalyser
from bots.management.analyser import ManagementAnalyser
from bots.metrics.analyser import MetricsAnalyser
from bots.profile.analyser import ProfileAnalyser
from bots.financial_intelligence.analyser import FinancialIntelligenceAnalyser
from bots.moat.analyser import MoatAnalyser
from bots.industry.analyser import IndustryAnalyser
from bots.competitors.analyser import CompetitorAnalyser
from bots.earnings.analyser import EarningsAnalyser
from bots.summary.analyser import SummaryAnalyser
from bots.report.analyser import ReportAnalyser

from core.analysis import InvestmentAnalysis
from core.financial_data import FinancialDataEngine
from core.scoring import calculate_overall_score
from core.rating import get_rating
from core.research_memory import ResearchMemory
from core.research_compare import ResearchCompare
from core.investment_committee import InvestmentCommittee
from core.research_engine import ResearchEngine
from core.evidence_engine import EvidenceEngine
from core.learning_engine import LearningEngine
from core.position_sizing import PositionSizingEngine


class DecisionEngine:

    def __init__(self):

        self.data = FinancialDataEngine()

        # --------------------------------
        # Research Infrastructure
        # --------------------------------

        self.research = ResearchEngine()
        self.evidence = EvidenceEngine()
        self.learning = LearningEngine()

        # --------------------------------
        # Position Sizing
        # --------------------------------

        self.position_sizing = (
            PositionSizingEngine()
        )

        # --------------------------------
        # Intelligence Bots
        # --------------------------------

        self.profile = ProfileAnalyser()
        self.financials = FinancialIntelligenceAnalyser()
        self.moat = MoatAnalyser()
        self.industry = IndustryAnalyser()
        self.competitors = CompetitorAnalyser()
        self.earnings = EarningsAnalyser()

        # --------------------------------
        # Specialist Bots
        # --------------------------------

        self.business = BusinessQualityAnalyser()
        self.valuation = ValuationAnalyser()
        self.technical = TechnicalAnalyser()
        self.risk = RiskAnalyser()
        self.news = NewsAnalyser()
        self.management = ManagementAnalyser()
        self.metrics = MetricsAnalyser()

        # --------------------------------
        # Synthesis
        # --------------------------------

        self.summary = SummaryAnalyser()
        self.report = ReportAnalyser()
        self.committee = InvestmentCommittee()

        # --------------------------------
        # Research Memory
        # --------------------------------

        self.memory = ResearchMemory()
        self.compare = ResearchCompare()

    def analyse(self, symbol):

        symbol = symbol.upper().strip()

        # --------------------------------
        # Shared Company Context
        # --------------------------------

        context = self.data.build_context(
            symbol
        )

        # --------------------------------
        # External Research
        # --------------------------------

        research = self.research.collect(
            symbol
        )

        evidence = self.evidence.analyse(
            research
        )

        # --------------------------------
        # Intelligence Analysis
        # --------------------------------

        profile = self.profile.analyse(
            context
        )

        financials = self.financials.analyse(
            context
        )

        moat = self.moat.analyse(
            context
        )

        industry = self.industry.analyse(
            context
        )

        competitors = self.competitors.analyse(
            context
        )

        earnings = self.earnings.analyse(
            context
        )

        metrics = self.metrics.analyse(
            context
        )

        # --------------------------------
        # Specialist Analysis
        # --------------------------------

        business = self.business.analyse(
            context
        )

        valuation = self.valuation.analyse(
            context
        )

        technical = self.technical.analyse(
            context
        )

        risk = self.risk.analyse(
            context
        )

        news = self.news.analyse(
            context
        )

        management = self.management.analyse(
            context
        )

        # --------------------------------
        # Investment Analysis Object
        # --------------------------------

        analysis = InvestmentAnalysis(
            symbol
        )

        analysis.business_quality = business[
            "Business Quality"
        ]

        analysis.valuation = valuation[
            "Valuation Score"
        ]

        analysis.technical = technical[
            "Technical Score"
        ]

        analysis.risk = risk[
            "Risk Score"
        ]

        analysis.news = news[
            "News Score"
        ]

        analysis.catalyst = news[
            "Catalyst Score"
        ]

        analysis.headlines = news[
            "Headlines"
        ]

        analysis.catalysts = news[
            "Catalysts"
        ]

        # --------------------------------
        # Core Overall Score
        # --------------------------------

        analysis.overall = calculate_overall_score(
            analysis.business_quality,
            analysis.valuation,
            analysis.technical,
            analysis.risk,
            analysis.news,
            analysis.catalyst,
        )

        analysis.rating = get_rating(
            analysis.overall
        )

        # --------------------------------
        # Human-readable Summary
        # --------------------------------

        summary = self.summary.analyse(
            analysis
        )

        analysis.summary = summary[
            "Summary"
        ]

        # --------------------------------
        # Report
        # --------------------------------

        report = self.report.build(
            analysis
        )

        # --------------------------------
        # Complete Result
        # --------------------------------

        result = {

            "Ticker":
                analysis.ticker,

            # Research

            "Research":
                research,

            "Evidence":
                evidence,

            # Company intelligence

            "Profile":
                profile,

            "Metrics":
                metrics,

            "Financial Intelligence":
                financials,

            "Moat Analysis":
                moat,

            "Industry Analysis":
                industry,

            "Competitor Analysis":
                competitors,

            "Earnings Analysis":
                earnings,

            # Core scores

            "Overall Score":
                analysis.overall,

            "Rating":
                analysis.rating,

            "Business Quality":
                analysis.business_quality,

            "Valuation":
                analysis.valuation,

            "Technical":
                analysis.technical,

            "Risk":
                analysis.risk,

            "News":
                analysis.news,

            "Catalyst":
                analysis.catalyst,

            # Specialist outputs

            "Business Analysis":
                business,

            "Valuation Analysis":
                valuation,

            "Technical Analysis":
                technical,

            "Risk Analysis":
                risk,

            "News Analysis":
                news,

            "Management Analysis":
                management,

            # Summary

            "Strengths":
                summary["Strengths"],

            "Weaknesses":
                summary["Weaknesses"],

            "Summary":
                analysis.summary,

            "Report":
                report,

            "Catalysts":
                analysis.catalysts,

            "Headlines":
                analysis.headlines,
        }

        # --------------------------------
        # Investment Committee
        # --------------------------------

        specialist_performance = (
            self.learning.specialist_performance()
        )

        committee = self.committee.review(
            result,
            specialist_performance,
        )

        result[
            "Investment Committee"
        ] = committee

        # --------------------------------
        # Position Sizing
        # --------------------------------

        volatility = (
            self._get_volatility(
                context
            )
        )

        position = (
            self.position_sizing.calculate(
                score=committee[
                    "Committee Score"
                ],
                confidence=committee[
                    "Confidence"
                ],
                risk=analysis.risk,
                volatility=volatility,
            )
        )

        result[
            "Position Sizing"
        ] = position

        # --------------------------------
        # Research Comparison
        # --------------------------------

        previous = self.memory.load_latest(
            analysis.ticker
        )

        comparison = self.compare.compare(
            previous,
            result
        )

        result[
            "Research Comparison"
        ] = comparison

        # --------------------------------
        # Save Research
        # --------------------------------

        self.memory.save(
            result
        )

        # --------------------------------
        # Learning Engine
        # --------------------------------

        prediction = (
            self.learning.record_prediction(
                result
            )
        )

        result[
            "Learning Prediction"
        ] = prediction

        return result

    # --------------------------------
    # Volatility Extraction
    # --------------------------------

    def _get_volatility(
        self,
        context,
    ):

        # Try common context locations.

        if hasattr(
            context,
            "volatility",
        ):

            return context.volatility

        if hasattr(
            context,
            "technical",
        ):

            technical = context.technical

            if isinstance(
                technical,
                dict,
            ):

                return technical.get(
                    "Volatility"
                )

        if hasattr(
            context,
            "financials",
        ):

            financials = context.financials

            if isinstance(
                financials,
                dict,
            ):

                return financials.get(
                    "Beta"
                )

        return None