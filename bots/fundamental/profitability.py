from core.financial_data import FinancialDataEngine


class ProfitabilityAnalyser:

    def __init__(self):
        self.engine = FinancialDataEngine()

    def gross_margin(self, symbol):

        company = self.engine.get_company(symbol)

        return company.info.get("grossMargins")

    def operating_margin(self, symbol):

        company = self.engine.get_company(symbol)

        return company.info.get("operatingMargins")

    def roe(self, symbol):

        company = self.engine.get_company(symbol)

        return company.info.get("returnOnEquity")

    def gross_margin_score(self, symbol):

        margin = self.gross_margin(symbol)

        if margin is None:
            return 0

        if margin >= 0.60:
            return 100
        elif margin >= 0.40:
            return 80
        elif margin >= 0.20:
            return 60
        elif margin >= 0.10:
            return 40
        else:
            return 0

    def operating_margin_score(self, symbol):

        margin = self.operating_margin(symbol)

        if margin is None:
            return 0

        if margin >= 0.30:
            return 100
        elif margin >= 0.20:
            return 80
        elif margin >= 0.10:
            return 60
        elif margin >= 0.05:
            return 40
        else:
            return 0

    def roe_score(self, symbol):

        roe = self.roe(symbol)

        if roe is None:
            return 0

        if roe >= 0.30:
            return 100
        elif roe >= 0.20:
            return 80
        elif roe >= 0.15:
            return 60
        elif roe >= 0.10:
            return 40
        else:
            return 0