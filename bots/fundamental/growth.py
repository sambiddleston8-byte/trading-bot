from core.financial_data import FinancialDataEngine
from core.scoring_engine import ScoringEngine


class GrowthAnalyser:

    def __init__(self):
        self.engine = FinancialDataEngine()
        self.scoring = ScoringEngine()

    def revenue_growth(self, symbol):

        revenue = self.engine.get_revenue(symbol)

        revenue = revenue.dropna()

        if len(revenue) < 2:
            return None

        latest = revenue.iloc[0]
        previous = revenue.iloc[1]

        if previous == 0:
            return None

        return (latest - previous) / previous

    def net_income_growth(self, symbol):

        income = self.engine.get_net_income(symbol)

        income = income.dropna()

        if len(income) < 2:
            return None

        latest = income.iloc[0]
        previous = income.iloc[1]

        if previous == 0:
            return None

        return (latest - previous) / previous

    def eps_growth(self, symbol):

        eps = self.engine.get_diluted_eps_history(symbol)

        if eps is None:
            return None

        eps = eps.dropna()

        if len(eps) < 2:
            return None

        latest = eps.iloc[0]
        previous = eps.iloc[1]

        if previous == 0:
            return None

        return (latest - previous) / previous

    def operating_cash_flow_growth(self, symbol):

        cashflow = self.engine.get_operating_cash_flow(symbol)

        if cashflow is None:
            return None

        cashflow = cashflow.dropna()

        if len(cashflow) < 2:
            return None

        latest = cashflow.iloc[0]
        previous = cashflow.iloc[1]

        if previous == 0:
            return None

        return (latest - previous) / previous

    def score(self, symbol):

        revenue = self.scoring.score_growth(
            self.revenue_growth(symbol)
        )

        income = self.scoring.score_growth(
            self.net_income_growth(symbol)
        )

        eps = self.scoring.score_growth(
            self.eps_growth(symbol)
        )

        cashflow = self.scoring.score_growth(
            self.operating_cash_flow_growth(symbol)
        )

        return round(
            (
                revenue +
                income +
                eps +
                cashflow
            ) / 4,
            1
        )