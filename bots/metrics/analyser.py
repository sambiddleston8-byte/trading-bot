from core.financial_data import FinancialDataEngine


class MetricsAnalyser:

    def __init__(self):

        self.engine = FinancialDataEngine()

    def analyse(self, symbol):

        info = self.engine.get_company_info(symbol)

        return {

            "Company": info.get("longName"),

            "Symbol": symbol,

            "Exchange": info.get("exchange"),

            "Sector": info.get("sector"),

            "Industry": info.get("industry"),

            "Country": info.get("country"),

            "Currency": info.get("currency"),

            "Employees": info.get("fullTimeEmployees"),

            "Market Cap": info.get("marketCap"),

            "Enterprise Value": info.get("enterpriseValue"),

            "Revenue": info.get("totalRevenue"),

            "Net Income": info.get("netIncomeToCommon"),

            "PE": info.get("trailingPE"),

            "Forward PE": info.get("forwardPE"),

            "PEG": info.get("pegRatio"),

            "Price to Book": info.get("priceToBook"),

            "Price to Sales": info.get("priceToSalesTrailing12Months"),

            "Dividend Yield": info.get("dividendYield"),

            "Beta": info.get("beta"),

            "52 Week High": info.get("fiftyTwoWeekHigh"),

            "52 Week Low": info.get("fiftyTwoWeekLow"),

            "Current Price": info.get("currentPrice"),

            "Previous Close": info.get("previousClose"),

        }