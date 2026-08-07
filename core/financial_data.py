import yfinance as yf


class FinancialDataEngine:

    def get_company(self, symbol):
        return yf.Ticker(symbol)

    def get_income_statement(self, symbol):
        return self.get_company(symbol).financials

    def get_balance_sheet(self, symbol):
        return self.get_company(symbol).balance_sheet

    def get_cash_flow(self, symbol):
        return self.get_company(symbol).cashflow

    def get_company_info(self, symbol):
        return self.get_company(symbol).info

    def get_revenue(self, symbol):
        income = self.get_income_statement(symbol)
        return income.loc["Total Revenue"]

    def get_net_income(self, symbol):
        income = self.get_income_statement(symbol)
        return income.loc["Net Income"]

    def get_total_debt(self, symbol):
        balance = self.get_balance_sheet(symbol)
        return balance.loc["Total Debt"]

    def get_cash(self, symbol):
        balance = self.get_balance_sheet(symbol)
        return balance.loc["Cash And Cash Equivalents"]

    def get_eps(self, symbol):
        return self.get_company_info(symbol).get("trailingEps")

    # ---------- VALUATION ----------

    def get_trailing_pe(self, symbol):
        return self.get_company_info(symbol).get("trailingPE")

    def get_forward_pe(self, symbol):
        return self.get_company_info(symbol).get("forwardPE")

    def get_peg_ratio(self, symbol):
        return self.get_company_info(symbol).get("pegRatio")

    def get_price_to_book(self, symbol):
        return self.get_company_info(symbol).get("priceToBook")

    def get_price_to_sales(self, symbol):
        return self.get_company_info(symbol).get("priceToSalesTrailing12Months")

    def get_enterprise_value(self, symbol):
        return self.get_company_info(symbol).get("enterpriseValue")

    def get_ebitda(self, symbol):
        return self.get_company_info(symbol).get("ebitda")