from core.financial_data import FinancialData
from core.history import HistoryEngine


class DataEngine:

    def __init__(self):

        self.financial = FinancialData()
        self.history = HistoryEngine()

        self._history_cache = {}

    # ============================================================
    # COMPANY INFORMATION
    # ============================================================

    def get_company_info(
        self,
        symbol,
    ):

        return self.financial.get_company_info(
            symbol
        )

    # ============================================================
    # FINANCIAL DATA
    # ============================================================

    def get_financial_data(
        self,
        symbol,
    ):

        return {

            "Revenue":
                self.financial.get_revenue(
                    symbol
                ),

            "Net Income":
                self.financial.get_net_income(
                    symbol
                ),

            "Operating Cash Flow":
                self.financial.get_operating_cash_flow(
                    symbol
                ),

            "Total Debt":
                self.financial.get_total_debt(
                    symbol
                ),

            "Cash":
                self.financial.get_cash(
                    symbol
                ),

            "ROE":
                self.financial.get_roe(
                    symbol
                ),

            "ROA":
                self.financial.get_roa(
                    symbol
                ),

            "ROIC":
                self.financial.get_roic(
                    symbol
                ),

            "Beta":
                self.financial.get_beta(
                    symbol
                ),

            "Market Cap":
                self.financial.get_market_cap(
                    symbol
                ),

            "Trailing PE":
                self.financial.get_trailing_pe(
                    symbol
                ),

            "Forward PE":
                self.financial.get_forward_pe(
                    symbol
                ),

            "PEG":
                self.financial.get_peg_ratio(
                    symbol
                ),

            "Price To Book":
                self.financial.get_price_to_book(
                    symbol
                ),

            "Price To Sales":
                self.financial.get_price_to_sales(
                    symbol
                ),

        }

    # ============================================================
    # PRICE HISTORY
    # ============================================================

    def get_history(
        self,
        symbol,
    ):

        if symbol not in self._history_cache:

            self._history_cache[
                symbol
            ] = self.history.get_history(
                symbol
            )

        return self._history_cache[
            symbol
        ]

    # ============================================================
    # COMPATIBILITY METHOD
    # ============================================================

    def load(
        self,
        symbol,
        start=None,
        end=None,
    ):

        history = self.get_history(
            symbol
        )

        if history is None:
            return None

        if start is not None:

            history = history[
                history.index >= start
            ]

        if end is not None:

            history = history[
                history.index < end
            ]

        return history

    # ============================================================
    # UNIVERSE LOADER
    # ============================================================

    def load_universe(
        self,
        symbols,
        start=None,
        end=None,
    ):

        results = {}

        for symbol in symbols:

            try:

                data = self.load(
                    symbol,
                    start=start,
                    end=end,
                )

                if data is not None:

                    results[
                        symbol
                    ] = data

            except Exception as error:

                print(
                    f"{symbol} history failed: "
                    f"{error}"
                )

        return results


# ================================================================
# COMPATIBILITY ALIAS
# ================================================================
#
# The multi-factor engine currently imports MarketDataEngine.
# Keep DataEngine as the underlying implementation and expose
# the expected name.

MarketDataEngine = DataEngine