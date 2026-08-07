from core.financial_data import FinancialDataEngine


class GrowthAnalyser:

    def __init__(self):
        self.engine = FinancialDataEngine()

    def revenue_growth(self, symbol):

        revenue = self.engine.get_revenue(symbol)
        revenue = revenue[revenue > 0]

        if len(revenue) < 2:
            return None

        latest = revenue.iloc[0]
        previous = revenue.iloc[1]

        return (latest - previous) / previous

    def net_income_growth(self, symbol):

        income = self.engine.get_net_income(symbol)
        income = income[income > 0]

        if len(income) < 2:
            return None

        latest = income.iloc[0]
        previous = income.iloc[1]

        return (latest - previous) / previous

    def revenue_score(self, symbol):

        growth = self.revenue_growth(symbol)

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

    def net_income_score(self, symbol):

        growth = self.net_income_growth(symbol)

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