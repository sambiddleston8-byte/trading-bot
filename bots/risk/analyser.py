from core.financial_data import FinancialDataEngine
from core.utils import average_scores


class RiskAnalyser:

    def __init__(self):
        self.engine = FinancialDataEngine()

    def debt_risk(self, symbol):

        debt = self.engine.get_total_debt(symbol)
        cash = self.engine.get_cash(symbol)

        if debt is None or cash is None:
            return None

        if len(debt) == 0 or len(cash) == 0:
            return None

        debt = debt.iloc[0]
        cash = cash.iloc[0]

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
            return 20

    def profitability_risk(self, symbol):

        roe = self.engine.get_roe(symbol)

        if roe is None:
            return None

        if roe >= 0.25:
            return 100
        elif roe >= 0.15:
            return 80
        elif roe >= 0.10:
            return 60
        elif roe >= 0:
            return 40
        else:
            return 20

    def beta_risk(self, symbol):

        beta = self.engine.get_beta(symbol)

        if beta is None:
            return None

        if beta < 0.8:
            return 100
        elif beta < 1.2:
            return 80
        elif beta < 1.5:
            return 60
        elif beta < 2:
            return 40
        else:
            return 20

    def size_risk(self, symbol):

        market_cap = self.engine.get_market_cap(symbol)

        if market_cap is None:
            return None

        if market_cap >= 200_000_000_000:
            return 100
        elif market_cap >= 50_000_000_000:
            return 80
        elif market_cap >= 10_000_000_000:
            return 60
        elif market_cap >= 2_000_000_000:
            return 40
        else:
            return 20

    def dilution_risk(self, symbol):

        shares = self.engine.get_shares_outstanding(symbol)

        if shares is None:
            return None

        if shares < 500_000_000:
            return 100
        elif shares < 2_000_000_000:
            return 80
        elif shares < 5_000_000_000:
            return 60
        elif shares < 10_000_000_000:
            return 40
        else:
            return 20

    def analyse(self, symbol):

        score = average_scores([
            self.debt_risk(symbol),
            self.profitability_risk(symbol),
            self.beta_risk(symbol),
            self.size_risk(symbol),
            self.dilution_risk(symbol),
        ])

        return {
            "Risk Score": score
        }