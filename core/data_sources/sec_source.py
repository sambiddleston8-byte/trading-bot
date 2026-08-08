import requests


class SECSource:

    SOURCE_NAME = "SEC EDGAR / Company Facts"

    SEC_COMPANY_FACTS_URL = (
        "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    )

    def __init__(
        self,
        user_agent="SamPatInvestmentResearch/1.0 contact@example.com",
    ):
        self.headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
        }

    def fetch_company_facts(self, cik):

        cik = str(cik).zfill(10)

        url = self.SEC_COMPANY_FACTS_URL.format(
            cik=cik
        )

        response = requests.get(
            url,
            headers=self.headers,
            timeout=30,
        )

        response.raise_for_status()

        return response.json()

    def get_fact(self, facts, tag):

        return (
            facts
            .get("facts", {})
            .get("us-gaap", {})
            .get(tag)
        )

    def get_units(self, fact):

        if not fact:
            return []

        units = fact.get("units", {})

        if "USD" in units:
            return units["USD"]

        if "shares" in units:
            return units["shares"]

        result = []

        for values in units.values():
            result.extend(values)

        return result

    def annual_values(self, facts, tag):

        fact = self.get_fact(
            facts,
            tag
        )

        values = self.get_units(fact)

        result = []

        for item in values:

            if item.get("form") != "10-K":
                continue

            result.append(
                {
                    "value": item.get("val"),
                    "start": item.get("start"),
                    "end": item.get("end"),
                    "filed": item.get("filed"),
                    "accession": item.get("accn"),
                    "fiscal_year": item.get("fy"),
                    "fiscal_period": item.get("fp"),
                    "frame": item.get("frame"),
                }
            )

        return result

    def latest_annual_flow(self, facts, tags):

        candidates = []

        for tag in tags:

            for item in self.annual_values(
                facts,
                tag
            ):

                if (
                    item.get("start")
                    and item.get("end")
                    and item.get("fiscal_period") == "FY"
                ):

                    candidates.append(
                        {
                            "tag": tag,
                            **item,
                        }
                    )

        if not candidates:
            return None

        candidates.sort(
            key=lambda x: (
                x.get("end") or "",
                x.get("filed") or "",
            ),
            reverse=True,
        )

        return candidates[0]

    def latest_balance_value(self, facts, tags):

        candidates = []

        for tag in tags:

            for item in self.annual_values(
                facts,
                tag
            ):

                if item.get("end"):

                    candidates.append(
                        {
                            "tag": tag,
                            **item,
                        }
                    )

        if not candidates:
            return None

        candidates.sort(
            key=lambda x: (
                x.get("end") or "",
                x.get("filed") or "",
            ),
            reverse=True,
        )

        return candidates[0]

    def parse(self, company_facts):

        facts = company_facts

        revenue = self.latest_annual_flow(
            facts,
            [
                "Revenues",
                "RevenueFromContractWithCustomerExcludingAssessedTax",
            ],
        )

        net_income = self.latest_annual_flow(
            facts,
            [
                "NetIncomeLoss",
                "ProfitLoss",
            ],
        )

        operating_income = self.latest_annual_flow(
            facts,
            [
                "OperatingIncomeLoss",
            ],
        )

        gross_profit = self.latest_annual_flow(
            facts,
            [
                "GrossProfit",
            ],
        )

        operating_cash_flow = self.latest_annual_flow(
            facts,
            [
                "NetCashProvidedByUsedInOperatingActivities",
            ],
        )

        capex = self.latest_annual_flow(
            facts,
            [
                "PaymentsToAcquirePropertyPlantAndEquipment",
                "PaymentsToAcquireProductiveAssets",
            ],
        )

        cash = self.latest_balance_value(
            facts,
            [
                "CashAndCashEquivalentsAtCarryingValue",
            ],
        )

        equity = self.latest_balance_value(
            facts,
            [
                "StockholdersEquity",
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            ],
        )

        current_debt = self.latest_balance_value(
            facts,
            [
                "LongTermDebtCurrent",
            ],
        )

        noncurrent_debt = self.latest_balance_value(
            facts,
            [
                "LongTermDebtNoncurrent",
            ],
        )

        total_debt = self.latest_balance_value(
            facts,
            [
                "LongTermDebt",
            ],
        )

        shares = self.latest_balance_value(
            facts,
            [
                "CommonStockSharesOutstanding",
            ],
        )

        free_cash_flow = None

        if (
            operating_cash_flow is not None
            and capex is not None
        ):

            free_cash_flow = (
                float(operating_cash_flow["value"])
                - float(capex["value"])
            )

        debt_value = None

        if total_debt is not None:

            debt_value = float(
                total_debt["value"]
            )

        else:

            current_value = (
                float(current_debt["value"])
                if current_debt is not None
                else 0
            )

            noncurrent_value = (
                float(noncurrent_debt["value"])
                if noncurrent_debt is not None
                else 0
            )

            if (
                current_debt is not None
                or noncurrent_debt is not None
            ):

                debt_value = (
                    current_value
                    + noncurrent_value
                )

        return {

            "source": self.SOURCE_NAME,

            "entity_name": company_facts.get(
                "entityName"
            ),

            "cik": company_facts.get(
                "cik"
            ),

            "financials": {

                "revenue": revenue,

                "net_income":
                    net_income,

                "operating_income":
                    operating_income,

                "gross_profit":
                    gross_profit,

                "operating_cash_flow":
                    operating_cash_flow,

                "capex": {

                    "data": capex,

                    "interpretation": (
                        "SEC-reported acquisition of "
                        "productive assets / PP&E. "
                        "The underlying taxonomy tag is "
                        "retained for provenance."
                    ),
                },

                "free_cash_flow": {

                    "value": free_cash_flow,

                    "method": (
                        "Operating cash flow minus "
                        "SEC productive-asset / PP&E "
                        "cash expenditure."
                    ),
                },
            },

            "balance_sheet": {

                "cash_and_equivalents": cash,

                "equity": equity,

                "current_debt": current_debt,

                "noncurrent_debt": noncurrent_debt,

                "total_debt": {

                    "value": debt_value,

                    "source_tag": (
                        total_debt.get("tag")
                        if total_debt is not None
                        else None
                    ),
                },

                "shares_outstanding": shares,
            },
        }

    def resolve_cik(self, symbol):

        symbol = symbol.upper().strip()

        url = "https://www.sec.gov/files/company_tickers.json"

        response = requests.get(
            url,
            headers=self.headers,
            timeout=30,
        )

        response.raise_for_status()

        companies = response.json()

        for item in companies.values():

            ticker = str(
                item.get("ticker", "")
            ).upper().strip()

            if ticker == symbol:

                return str(
                    item["cik_str"]
                ).zfill(10)

        raise ValueError(
            f"Could not resolve SEC CIK for {symbol}"
        )

    def fetch_for_symbol(self, symbol):

        cik = self.resolve_cik(symbol)

        return self.fetch(cik)

    def fetch(self, cik):

        company_facts = self.fetch_company_facts(
            cik
        )

        return self.parse(
            company_facts
        )
