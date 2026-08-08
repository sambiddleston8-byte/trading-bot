import time

import requests

from core.historical_fundamentals import HistoricalFundamentals


class SECHistoricalFundamentals:

    def __init__(
        self,
        user_agent="Sam Trading Bot research@example.com",
    ):

        self.user_agent = user_agent

        self.headers = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        }

        self.cache = HistoricalFundamentals()

        self.company_tickers = {}

        self.load_company_tickers()

    # --------------------------------
    # SEC COMPANY MAPPING
    # --------------------------------

    def load_company_tickers(self):

        urls = [
            "https://www.sec.gov/files/company_tickers.json",
            "https://www.sec.gov/files/company_tickers_exchange.json",
        ]

        for url in urls:

            try:

                response = requests.get(
                    url,
                    headers=self.headers,
                    timeout=30,
                )

                response.raise_for_status()

                data = response.json()

                for item in data.values():

                    ticker = (
                        item.get("ticker", "")
                        or ""
                    ).upper().strip()

                    cik = item.get("cik_str")

                    if ticker and cik:

                        self.company_tickers[
                            ticker
                        ] = str(cik).zfill(10)

                if self.company_tickers:

                    print(
                        "SEC company mapping loaded:",
                        len(self.company_tickers),
                        "companies",
                    )

                    return

            except Exception as error:

                print(
                    f"SEC mapping failed: {error}"
                )

        # Known fallback mappings

        self.company_tickers.update({

            "NVDA": "0001045810",
            "MSFT": "0000789019",
            "AAPL": "0000320193",
            "GOOGL": "0001652044",
            "AMZN": "0001018724",
            "META": "0001326801",
            "AVGO": "0001730168",
            "MU": "0000723125",
            "CVX": "0000093410",
            "XOM": "0000034088",

        })

        print(
            "SEC mapping fallback loaded."
        )

    # --------------------------------
    # GET CIK
    # --------------------------------

    def get_cik(
        self,
        symbol,
    ):

        return self.company_tickers.get(
            symbol.upper().strip()
        )

    # --------------------------------
    # GET COMPANY FACTS
    # --------------------------------

    def get_company_facts(
        self,
        symbol,
    ):

        cik = self.get_cik(
            symbol
        )

        if not cik:

            print(
                f"{symbol}: SEC CIK not found."
            )

            return {}

        url = (
            "https://data.sec.gov/api/xbrl/"
            f"companyfacts/CIK{cik}.json"
        )

        try:

            response = requests.get(
                url,
                headers=self.headers,
                timeout=30,
            )

            response.raise_for_status()

            time.sleep(0.15)

            return response.json()

        except Exception as error:

            print(
                f"{symbol}: SEC company facts failed:"
            )

            print(
                error
            )

            return {}

    # --------------------------------
    # EXTRACT FACT AVAILABLE BEFORE DATE
    # --------------------------------

    def extract_before_date(
        self,
        facts,
        namespaces,
        concepts,
        decision_date,
    ):

        best = None

        for namespace in namespaces:

            namespace_data = (
                facts
                .get("facts", {})
                .get(namespace, {})
            )

            for concept in concepts:

                concept_data = (
                    namespace_data.get(
                        concept
                    )
                )

                if not concept_data:
                    continue

                units = concept_data.get(
                    "units",
                    {}
                )

                for unit_values in (
                    units.values()
                ):

                    for item in unit_values:

                        filed = item.get(
                            "filed"
                        )

                        if not filed:
                            continue

                        # CRITICAL:
                        #
                        # Only use information
                        # filed before the
                        # historical decision date.

                        if filed > decision_date:
                            continue

                        value = item.get(
                            "val"
                        )

                        if value is None:
                            continue

                        candidate = {

                            "Value":
                                value,

                            "Filed":
                                filed,

                            "End":
                                item.get(
                                    "end"
                                ),

                            "Start":
                                item.get(
                                    "start"
                                ),

                            "Form":
                                item.get(
                                    "form"
                                ),

                            "Fiscal Year":
                                item.get(
                                    "fy"
                                ),

                            "Fiscal Period":
                                item.get(
                                    "fp"
                                ),

                        }

                        if (
                            best is None
                            or candidate["Filed"]
                            > best["Filed"]
                        ):

                            best = candidate

        return best

    # --------------------------------
    # BUILD SNAPSHOT
    # --------------------------------

    def build_snapshot(
        self,
        facts,
        decision_date,
    ):

        namespaces = [
            "us-gaap",
        ]

        revenue = self.extract_before_date(
            facts,
            namespaces,
            [
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues",
                "SalesRevenueNet",
            ],
            decision_date,
        )

        net_income = self.extract_before_date(
            facts,
            namespaces,
            [
                "NetIncomeLoss",
                "ProfitLoss",
            ],
            decision_date,
        )

        operating_income = self.extract_before_date(
            facts,
            namespaces,
            [
                "OperatingIncomeLoss",
            ],
            decision_date,
        )

        assets = self.extract_before_date(
            facts,
            namespaces,
            [
                "Assets",
            ],
            decision_date,
        )

        liabilities = self.extract_before_date(
            facts,
            namespaces,
            [
                "Liabilities",
            ],
            decision_date,
        )

        equity = self.extract_before_date(
            facts,
            namespaces,
            [
                "StockholdersEquity",
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            ],
            decision_date,
        )

        cash = self.extract_before_date(
            facts,
            namespaces,
            [
                "CashAndCashEquivalentsAtCarryingValue",
                "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            ],
            decision_date,
        )

        operating_cash_flow = self.extract_before_date(
            facts,
            namespaces,
            [
                "NetCashProvidedByUsedInOperatingActivities",
            ],
            decision_date,
        )

        capital_expenditure = self.extract_before_date(
            facts,
            namespaces,
            [
                "PaymentsToAcquirePropertyPlantAndEquipment",
                "PaymentsToAcquireProductiveAssets",
            ],
            decision_date,
        )

        long_term_debt = self.extract_before_date(
            facts,
            namespaces,
            [
                "LongTermDebtNoncurrent",
                "LongTermDebtCurrent",
                "LongTermDebt",
            ],
            decision_date,
        )

        return {

            "Revenue":
                revenue,

            "Net Income":
                net_income,

            "Operating Income":
                operating_income,

            "Assets":
                assets,

            "Liabilities":
                liabilities,

            "Equity":
                equity,

            "Cash":
                cash,

            "Operating Cash Flow":
                operating_cash_flow,

            "Capital Expenditure":
                capital_expenditure,

            "Long Term Debt":
                long_term_debt,

        }

    # --------------------------------
    # NUMERIC VALUE
    # --------------------------------

    def value(
        self,
        item,
    ):

        if item is None:
            return None

        if isinstance(
            item,
            dict,
        ):

            return item.get(
                "Value"
            )

        return item

    # --------------------------------
    # DERIVED METRICS
    # --------------------------------

    def calculate_metrics(
        self,
        snapshot,
    ):

        revenue = self.value(
            snapshot.get(
                "Revenue"
            )
        )

        net_income = self.value(
            snapshot.get(
                "Net Income"
            )
        )

        operating_income = self.value(
            snapshot.get(
                "Operating Income"
            )
        )

        assets = self.value(
            snapshot.get(
                "Assets"
            )
        )

        liabilities = self.value(
            snapshot.get(
                "Liabilities"
            )
        )

        equity = self.value(
            snapshot.get(
                "Equity"
            )
        )

        cash = self.value(
            snapshot.get(
                "Cash"
            )
        )

        operating_cash_flow = self.value(
            snapshot.get(
                "Operating Cash Flow"
            )
        )

        capital_expenditure = self.value(
            snapshot.get(
                "Capital Expenditure"
            )
        )

        debt = self.value(
            snapshot.get(
                "Long Term Debt"
            )
        )

        free_cash_flow = None

        if (
            operating_cash_flow is not None
            and capital_expenditure is not None
        ):

            free_cash_flow = (
                operating_cash_flow
                - abs(
                    capital_expenditure
                )
            )

        profit_margin = None

        if (
            net_income is not None
            and revenue not in (
                None,
                0,
            )
        ):

            profit_margin = (
                net_income
                / revenue
            )

        operating_margin = None

        if (
            operating_income is not None
            and revenue not in (
                None,
                0,
            )
        ):

            operating_margin = (
                operating_income
                / revenue
            )

        roe = None

        if (
            net_income is not None
            and equity not in (
                None,
                0,
            )
        ):

            roe = (
                net_income
                / equity
            )

        debt_to_equity = None

        if (
            debt is not None
            and equity not in (
                None,
                0,
            )
        ):

            debt_to_equity = (
                debt
                / equity
            ) * 100

        return {

            "Revenue":
                revenue,

            "Net Income":
                net_income,

            "Operating Income":
                operating_income,

            "Assets":
                assets,

            "Liabilities":
                liabilities,

            "Equity":
                equity,

            "Cash":
                cash,

            "Operating Cash Flow":
                operating_cash_flow,

            "Capital Expenditure":
                capital_expenditure,

            "Free Cash Flow":
                free_cash_flow,

            "Long Term Debt":
                debt,

            "Profit Margin":
                profit_margin,

            "Operating Margin":
                operating_margin,

            "ROE":
                roe,

            "Debt To Equity":
                debt_to_equity,

        }

    # --------------------------------
    # COLLECT ONE COMPANY
    # --------------------------------

    def collect(
        self,
        symbol,
        decision_dates,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        print(
            f"SEC historical fundamentals: "
            f"{symbol}"
        )

        facts = (
            self.get_company_facts(
                symbol
            )
        )

        if not facts:

            return 0

        collected = 0

        for decision_date in (
            decision_dates
        ):

            snapshot = (
                self.build_snapshot(
                    facts,
                    decision_date,
                )
            )

            metrics = (
                self.calculate_metrics(
                    snapshot
                )
            )

            has_data = any(
                value is not None
                for value in metrics.values()
            )

            if not has_data:
                continue

            self.cache.add_snapshot(
                symbol,
                decision_date,
                metrics,
                source="SEC Company Facts",
            )

            collected += 1

        return collected

    # --------------------------------
    # COLLECT ENTIRE UNIVERSE
    # --------------------------------

    def collect_universe(
        self,
        symbols,
        decision_dates,
    ):

        total = len(
            symbols
        )

        total_snapshots = 0

        print()
        print("=" * 80)
        print("SEC HISTORICAL FUNDAMENTALS")
        print("=" * 80)

        print(
            f"Stocks: {total}"
        )

        print(
            f"Decision dates: "
            f"{len(decision_dates)}"
        )

        print()

        for index, symbol in enumerate(
            symbols,
            start=1,
        ):

            print(
                f"[{index}/{total}] "
                f"{symbol}"
            )

            try:

                count = self.collect(
                    symbol,
                    decision_dates,
                )

                total_snapshots += count

                print(
                    f"  Snapshots: {count}"
                )

            except Exception as error:

                print(
                    f"  FAILED: {error}"
                )

        print()
        print("=" * 80)

        print(
            f"Total snapshots: "
            f"{total_snapshots}"
        )

        print(
            f"Companies cached: "
            f"{len(self.cache.data)}"
        )

        print(
            f"Cache file: "
            f"{self.cache.cache_path}"
        )

        return total_snapshots


if __name__ == "__main__":

    from core.stock_universe import StockUniverse

    universe = StockUniverse()

    symbols = universe.load()

    if not symbols:

        symbols = universe.build()

    dates = [

        f"{year}-{month:02d}-01"

        for year in range(
            2020,
            2027,
        )

        for month in (
            1,
            4,
            7,
            10,
        )

    ]

    engine = (
        SECHistoricalFundamentals()
    )

    engine.collect_universe(
        symbols,
        dates,
    )