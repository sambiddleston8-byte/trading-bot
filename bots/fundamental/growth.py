from core.financial_data import FinancialDataEngine


class GrowthAnalyser:

    def __init__(self):
        self.engine = FinancialDataEngine()

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

    def growth_to_score(self, growth):

        if growth is None:
            return 0

        if growth >= 0.20:
            return 100
        elif growth >= 0.10:
            return 80
        elif growth >= 0.05:
            return 60
        elif growth >= 0:
            return 40
        else:
            return 0

    def score(self, symbol):

        revenue = self.growth_to_score(
            self.revenue_growth(symbol)
        )

        income = self.growth_to_score(
            self.net_income_growth(symbol)
        )

        eps = self.growth_to_score(
            self.eps_growth(symbol)
        )

        cashflow = self.growth_to_score(
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