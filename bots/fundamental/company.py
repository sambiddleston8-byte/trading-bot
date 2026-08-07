from core.financial_data import FinancialDataEngine


class CompanyAnalyser:

    def __init__(self):
        self.engine = FinancialDataEngine()

    def company_info(self, symbol):

        return self.engine.get_company_info(symbol)

    def summary(self, symbol):

        info = self.company_info(symbol)

        return {
            "Ticker": symbol,
            "Name": info.get("longName"),
            "Sector": info.get("sector"),
            "Industry": info.get("industry"),
            "Price": info.get("currentPrice"),
            "Market Cap": info.get("marketCap"),
            "Forward PE": info.get("forwardPE"),
            "Trailing PE": info.get("trailingPE"),
            "Dividend Yield": info.get("dividendYield"),
            "Beta": info.get("beta"),
            "Recommendation": info.get("recommendationKey")
        }