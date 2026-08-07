from core.financial_data import FinancialDataEngine


class QualityAnalyser:

    def __init__(self):
        self.engine = FinancialDataEngine()

    def eps_score(self, symbol):

        eps = self.engine.get_eps(symbol)

        if eps is None:
            return 0

        if eps >= 10:
            return 100
        elif eps >= 5:
            return 80
        elif eps >= 2:
            return 60
        elif eps > 0:
            return 40
        else:
            return 0