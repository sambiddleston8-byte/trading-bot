from datetime import datetime, timezone
import yfinance as yf

from core.company_context import CompanyContext
from core.data_sources.yahoo_source import YahooSource
from core.data_sources.sec_source import SECSource
from core.validation.validated_financial_data import ValidatedFinancialData


class FinancialDataEngine:

    def __init__(self):

        self._ticker_cache = {}
        self._info_cache = {}
        self._financials_cache = {}
        self._balance_sheet_cache = {}
        self._cashflow_cache = {}
        self._history_cache = {}

    # ============================================================
    # INTERNAL CACHE
    # ============================================================

    def get_company(
        self,
        symbol,
    ):

        if symbol not in self._ticker_cache:

            self._ticker_cache[
                symbol
            ] = yf.Ticker(
                symbol
            )

        return self._ticker_cache[
            symbol
        ]

    # ============================================================
    # COMPANY INFORMATION
    # ============================================================

    def get_company_info(
        self,
        symbol,
    ):

        if symbol not in self._info_cache:

            self._info_cache[
                symbol
            ] = self.get_company(
                symbol
            ).info

        return self._info_cache[
            symbol
        ]

    # ============================================================
    # FINANCIAL STATEMENTS
    # ============================================================

    def get_income_statement(
        self,
        symbol,
    ):

        if symbol not in self._financials_cache:

            self._financials_cache[
                symbol
            ] = (
                self.get_company(
                    symbol
                ).financials
            )

        return self._financials_cache[
            symbol
        ]

    def get_balance_sheet(
        self,
        symbol,
    ):

        if symbol not in self._balance_sheet_cache:

            self._balance_sheet_cache[
                symbol
            ] = (
                self.get_company(
                    symbol
                ).balance_sheet
            )

        return self._balance_sheet_cache[
            symbol
        ]

    def get_cash_flow(
        self,
        symbol,
    ):

        if symbol not in self._cashflow_cache:

            self._cashflow_cache[
                symbol
            ] = (
                self.get_company(
                    symbol
                ).cashflow
            )

        return self._cashflow_cache[
            symbol
        ]

    # ============================================================
    # PRICE HISTORY
    # ============================================================

    def get_price_history(
        self,
        symbol,
        period="1y",
    ):

        key = (
            f"{symbol}:{period}"
        )

        if key not in self._history_cache:

            self._history_cache[
                key
            ] = (
                self.get_company(
                    symbol
                ).history(
                    period=period
                )
            )

        return self._history_cache[
            key
        ]

    # ============================================================
    # COMPANY CONTEXT
    # ============================================================

    def build_context(
        self,
        symbol,
    ):

        symbol = symbol.upper().strip()

        validated_financial_data = None

        try:

            yahoo_source = YahooSource()

            sec_source = SECSource()

            retrieval_time = (
                datetime.now(
                    timezone.utc
                ).isoformat()
            )

            yahoo_data = yahoo_source.fetch(
                symbol
            )

            sec_data = sec_source.fetch_for_symbol(
                symbol
            )

            if isinstance(
                yahoo_data,
                dict,
            ):
                yahoo_data[
                    "_retrieved_at"
                ] = retrieval_time

            if isinstance(
                sec_data,
                dict,
            ):
                sec_data[
                    "_retrieved_at"
                ] = retrieval_time

            validated_financial_data = (
                ValidatedFinancialData(
                    yahoo_data,
                    sec_data,
                ).build(
                    symbol
                )
            )

        except Exception as error:

            validated_financial_data = {
                "ticker": symbol,
                "status": "VALIDATION_ERROR",
                "error": str(error),
            }

        return CompanyContext(

            symbol=symbol,

            info=self.get_company_info(
                symbol
            ),

            financials=self.get_income_statement(
                symbol
            ),

            balance_sheet=self.get_balance_sheet(
                symbol
            ),

            cashflow=self.get_cash_flow(
                symbol
            ),

            history=self.get_price_history(
                symbol
            ),

            validated_financial_data=
                validated_financial_data,

        )

    def get_revenue(
        self,
        symbol,
    ):

        income = (
            self.get_income_statement(
                symbol
            )
        )

        if "Total Revenue" not in income.index:

            return None

        return income.loc[
            "Total Revenue"
        ]

    def get_net_income(
        self,
        symbol,
    ):

        income = (
            self.get_income_statement(
                symbol
            )
        )

        if "Net Income" not in income.index:

            return None

        return income.loc[
            "Net Income"
        ]

    def get_operating_cash_flow(
        self,
        symbol,
    ):

        cashflow = (
            self.get_cash_flow(
                symbol
            )
        )

        if (
            "Operating Cash Flow"
            not in cashflow.index
        ):

            return None

        return cashflow.loc[
            "Operating Cash Flow"
        ]

    def get_total_debt(
        self,
        symbol,
    ):

        sheet = (
            self.get_balance_sheet(
                symbol
            )
        )

        if "Total Debt" not in sheet.index:

            return None

        return sheet.loc[
            "Total Debt"
        ]

    def get_cash(
        self,
        symbol,
    ):

        sheet = (
            self.get_balance_sheet(
                symbol
            )
        )

        if (
            "Cash And Cash Equivalents"
            not in sheet.index
        ):

            return None

        return sheet.loc[
            "Cash And Cash Equivalents"
        ]

    def get_diluted_eps_history(
        self,
        symbol,
    ):

        income = (
            self.get_income_statement(
                symbol
            )
        )

        if (
            "Diluted EPS"
            not in income.index
        ):

            return None

        return income.loc[
            "Diluted EPS"
        ]

    # ============================================================
    # RETURNS
    # ============================================================

    def get_roe(
        self,
        symbol,
    ):

        return self.get_company_info(
            symbol
        ).get(
            "returnOnEquity"
        )

    def get_roa(
        self,
        symbol,
    ):

        return self.get_company_info(
            symbol
        ).get(
            "returnOnAssets"
        )

    def get_roic(
        self,
        symbol,
    ):

        return self.get_company_info(
            symbol
        ).get(
            "returnOnInvestedCapital"
        )

    # ============================================================
    # RISK
    # ============================================================

    def get_beta(
        self,
        symbol,
    ):

        return self.get_company_info(
            symbol
        ).get(
            "beta"
        )

    def get_market_cap(
        self,
        symbol,
    ):

        return self.get_company_info(
            symbol
        ).get(
            "marketCap"
        )

    # ============================================================
    # VALUATION
    # ============================================================

    def get_trailing_pe(
        self,
        symbol,
    ):

        return self.get_company_info(
            symbol
        ).get(
            "trailingPE"
        )

    def get_forward_pe(
        self,
        symbol,
    ):

        return self.get_company_info(
            symbol
        ).get(
            "forwardPE"
        )

    def get_peg_ratio(
        self,
        symbol,
    ):

        return self.get_company_info(
            symbol
        ).get(
            "pegRatio"
        )

    def get_price_to_book(
        self,
        symbol,
    ):

        return self.get_company_info(
            symbol
        ).get(
            "priceToBook"
        )

    def get_price_to_sales(
        self,
        symbol,
    ):

        return self.get_company_info(
            symbol
        ).get(
            "priceToSalesTrailing12Months"
        )

    def get_enterprise_value(
        self,
        symbol,
    ):

        return self.get_company_info(
            symbol
        ).get(
            "enterpriseValue"
        )

    def get_ebitda(
        self,
        symbol,
    ):

        return self.get_company_info(
            symbol
        ).get(
            "ebitda"
        )

    def get_shares_outstanding(
        self,
        symbol,
    ):

        return self.get_company_info(
            symbol
        ).get(
            "sharesOutstanding"
        )

    def get_float_shares(
        self,
        symbol,
    ):

        return self.get_company_info(
            symbol
        ).get(
            "floatShares"
        )


# ================================================================
# COMPATIBILITY ALIAS
# ================================================================
#
# Some parts of the project import FinancialData while the
# original implementation was called FinancialDataEngine.
#
# This alias lets both interfaces work without duplicating
# the underlying implementation.

FinancialData = FinancialDataEngine