from core.financial_data import FinancialDataEngine
from core.scoring_engine import ScoringEngine
from core.utils import average_scores


class ProfitabilityAnalyser:

    def __init__(self):
        self.engine = FinancialDataEngine()
        self.scoring = ScoringEngine()

    def gross_margin(self, symbol):
        return self.engine.get_company_info(symbol).get("grossMargins")

    def operating_margin(self, symbol):
        return self.engine.get_company_info(symbol).get("operatingMargins")

    def roe(self, symbol):
        return self.engine.get_roe(symbol)

    def roa(self, symbol):
        return self.engine.get_roa(symbol)

    def roic(self, symbol):
        return self.engine.get_roic(symbol)

    def score(self, symbol):

        scores = []

        gross = self.gross_margin(symbol)
        if gross is not None:
            scores.append(self.scoring.score_margin(gross))

        operating = self.operating_margin(symbol)
        if operating is not None:
            scores.append(self.scoring.score_margin(operating))

        roe = self.roe(symbol)
        if roe is not None:
            scores.append(self.scoring.score_return(roe))

        roa = self.roa(symbol)
        if roa is not None:
            scores.append(self.scoring.score_return(roa))

        roic = self.roic(symbol)
        if roic is not None:
            scores.append(self.scoring.score_return(roic))

        return average_scores(scores)