import math


class YahooSource:

    SOURCE_NAME = "Yahoo Finance / yfinance"

    def __init__(self):
        import yfinance as yf
        self.yf = yf

    def safe_float(self, value, default=None):
        try:
            if value is None:
                return default

            value = float(value)

            if math.isnan(value) or math.isinf(value):
                return default

            return value

        except (TypeError, ValueError):
            return default

    def find_line(self, statement, names):
        if statement is None:
            return None

        for name in names:
            if name in statement.index:
                return statement.loc[name]

        return None

    def latest_annual_value(self, statement, names):
        line = self.find_line(statement, names)

        if line is None:
            return None

        for column in statement.columns:
            value = self.safe_float(line.get(column))

            if value is not None:
                return {
                    "value": value,
                    "period": str(column),
                }

        return None

    def get_annual_financials(self, income_statement, cash_flow):

        revenue_line = self.find_line(
            income_statement,
            [
                "Total Revenue",
                "Operating Revenue",
            ],
        )

        operating_cash_line = self.find_line(
            cash_flow,
            [
                "Operating Cash Flow",
                "Total Cash From Operating Activities",
            ],
        )

        capex_line = self.find_line(
            cash_flow,
            [
                "Capital Expenditure",
                "Capital Expenditure Reported",
            ],
        )

        if revenue_line is None:
            return []

        result = []

        for column in income_statement.columns:

            revenue = self.safe_float(
                revenue_line.get(column)
            )

            if revenue is None:
                continue

            operating_cash = None
            capex = None
            fcf = None

            if operating_cash_line is not None:
                operating_cash = self.safe_float(
                    operating_cash_line.get(column)
                )

            if capex_line is not None:
                capex = self.safe_float(
                    capex_line.get(column)
                )

            if (
                operating_cash is not None
                and capex is not None
            ):
                fcf = operating_cash + capex

            result.append(
                {
                    "period": str(column),
                    "revenue": revenue,
                    "operating_cash_flow": operating_cash,
                    "capital_expenditure": capex,
                    "free_cash_flow": fcf,
                }
            )

        return result

    def get_revenue_estimates(self, ticker):

        try:
            estimates = ticker.revenue_estimate

            if estimates is None or estimates.empty:
                return []

            result = []

            for period, row in estimates.iterrows():

                result.append(
                    {
                        "period": str(period),
                        "average": self.safe_float(row.get("avg")),
                        "low": self.safe_float(row.get("low")),
                        "high": self.safe_float(row.get("high")),
                        "yearago": self.safe_float(
                            row.get("yearAgoRevenue")
                        ),
                        "growth": self.safe_float(row.get("growth")),
                        "currency": row.get("currency"),
                    }
                )

            return result

        except Exception as error:
            return {"error": str(error)}

    def get_earnings_estimates(self, ticker):

        try:
            estimates = ticker.earnings_estimate

            if estimates is None or estimates.empty:
                return []

            result = []

            for period, row in estimates.iterrows():

                result.append(
                    {
                        "period": str(period),
                        "average": self.safe_float(row.get("avg")),
                        "low": self.safe_float(row.get("low")),
                        "high": self.safe_float(row.get("high")),
                        "yearago": self.safe_float(
                            row.get("yearAgoEps")
                        ),
                        "growth": self.safe_float(row.get("growth")),
                        "currency": row.get("currency"),
                    }
                )

            return result

        except Exception as error:
            return {"error": str(error)}

    def fetch(self, symbol):

        symbol = symbol.upper().strip()

        ticker = self.yf.Ticker(symbol)

        try:
            info = ticker.info
        except Exception:
            info = {}

        try:
            income_statement = ticker.income_stmt
        except Exception:
            income_statement = None

        try:
            cash_flow = ticker.cashflow
        except Exception:
            cash_flow = None

        try:
            balance_sheet = ticker.balance_sheet
        except Exception:
            balance_sheet = None

        annual_financials = self.get_annual_financials(
            income_statement,
            cash_flow,
        )

        latest = annual_financials[0] if annual_financials else None

        cash_result = self.latest_annual_value(
            balance_sheet,
            [
                "Cash And Cash Equivalents",
                "Cash Cash Equivalents And Short Term Investments",
            ],
        )

        debt_result = self.latest_annual_value(
            balance_sheet,
            [
                "Total Debt",
            ],
        )

        current_debt_result = self.latest_annual_value(
            balance_sheet,
            [
                "Current Debt",
                "Current Debt And Capital Lease Obligation",
            ],
        )

        long_term_debt_result = self.latest_annual_value(
            balance_sheet,
            [
                "Long Term Debt",
                "Long Term Debt And Capital Lease Obligation",
            ],
        )

        total_debt = None

        if debt_result is not None:
            total_debt = debt_result["value"]

        else:

            current_debt = (
                current_debt_result["value"]
                if current_debt_result is not None
                else 0
            )

            long_term_debt = (
                long_term_debt_result["value"]
                if long_term_debt_result is not None
                else 0
            )

            if (
                current_debt_result is not None
                or long_term_debt_result is not None
            ):
                total_debt = current_debt + long_term_debt

        current_price = self.safe_float(
            info.get("currentPrice")
        )

        if current_price is None:
            current_price = self.safe_float(
                info.get("regularMarketPrice")
            )

        market_cap = self.safe_float(
            info.get("marketCap")
        )

        shares = self.safe_float(
            info.get("sharesOutstanding")
        )

        beta = self.safe_float(
            info.get("beta")
        )

        yahoo_total_cash = self.safe_float(
            info.get("totalCash")
        )

        return {
            "source": self.SOURCE_NAME,
            "ticker": symbol,
            "company": info.get("longName"),

            "market": {
                "current_price": current_price,
                "market_cap": market_cap,
                "shares_outstanding": shares,
                "beta": beta,
            },

            "financials": {
                "latest_annual": latest,
                "historical_annual": annual_financials,
            },

            "balance_sheet": {
                "cash_and_equivalents": {
                    "value": (
                        cash_result["value"]
                        if cash_result is not None
                        else None
                    ),
                    "period": (
                        cash_result["period"]
                        if cash_result is not None
                        else None
                    ),
                },

                "total_debt": {
                    "value": total_debt,
                },

                "yahoo_total_cash": {
                    "value": yahoo_total_cash,
                    "definition": (
                        "Yahoo Finance totalCash field; "
                        "may include broader liquidity than "
                        "cash and cash equivalents."
                    ),
                },
            },

            "analyst_estimates": {
                "revenue": self.get_revenue_estimates(ticker),
                "earnings": self.get_earnings_estimates(ticker),
            },
        }
