from core.financial_data import FinancialDataEngine


class BalanceSheetAnalyser:

    def __init__(self):
        self.engine = FinancialDataEngine()

    def debt(self, symbol):

        debt = self.engine.get_total_debt(symbol)

        if len(debt) == 0:
            return None

        return debt.iloc[0]

    def cash(self, symbol):

        cash = self.engine.get_cash(symbol)

        if len(cash) == 0:
            return None

        return cash.iloc[0]

    def debt_score(self, symbol):

        debt = self.debt(symbol)
        cash = self.cash(symbol)

        if debt is None or cash is None:
            return 0

        if debt == 0:
            return 100

        ratio = cash / debt

        if ratio >= 2:
            return 100
        elif ratio >= 1:
            return 80
        elif ratio >= 0.5:
            return 60
        elif ratio >= 0.25:
            return 40
        else:
            return 0

    def score(self, symbol):

        return self.debt_score(symbol)