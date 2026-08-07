from core.financial_data import FinancialData
from core.history import HistoryEngine


class DataEngine:

    def __init__(self):

        self.financial = FinancialData()
        self.history = HistoryEngine()

    def get_company_info(self, symbol):
        return self.financial.get_company_info(symbol)

    def get_financial_data(self, symbol):
        return self.financial.get_financial_data(symbol)

    def get_history(self, symbol):
        return self.history.get_history(symbol)