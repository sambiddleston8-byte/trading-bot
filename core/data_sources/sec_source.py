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

    def annual_flow_for_period(
        self,
        facts,
        tags,
        period_end,
    ):
        """Return an annual flow only when it matches the reference period.

        Company Facts retains old taxonomy tags for many years.  Without an
        explicit period check a missing current tag can silently pull a value
        from an old annual filing into an otherwise current analysis.
        """

        if not period_end:
            return self.latest_annual_flow(facts, tags)

        candidates = []

        for tag in tags:
            for item in self.annual_values(facts, tag):
                if (
                    item.get("start")
                    and item.get("end") == period_end
                    and item.get("fiscal_period") == "FY"
                ):
                    candidates.append({"tag": tag, **item})

        if not candidates:
            return None

        candidates.sort(
            key=lambda item: (item.get("filed") or "", item.get("tag") or ""),
            reverse=True,
        )
        return candidates[0]

    def balance_value_for_period(
        self,
        facts,
        tags,
        period_end,
    ):
        """Return a balance-sheet value for the reference fiscal year-end."""

        if not period_end:
            return self.latest_balance_value(facts, tags)

        candidates = []

        for tag in tags:
            for item in self.annual_values(facts, tag):
                if item.get("end") == period_end:
                    candidates.append({"tag": tag, **item})

        if not candidates:
            return None

        candidates.sort(
            key=lambda item: (item.get("filed") or "", item.get("tag") or ""),
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

        reference_end = revenue.get("end") if revenue else None

        net_income = self.annual_flow_for_period(
            facts,
            [
                "NetIncomeLoss",
                "ProfitLoss",
            ],
            reference_end,
        )

        operating_income = self.annual_flow_for_period(
            facts,
            [
                "OperatingIncomeLoss",
            ],
            reference_end,
        )

        gross_profit = self.annual_flow_for_period(
            facts,
            [
                "GrossProfit",
            ],
            reference_end,
        )

        operating_cash_flow = self.annual_flow_for_period(
            facts,
            [
                "NetCashProvidedByUsedInOperatingActivities",
            ],
            reference_end,
        )

        capex = self.annual_flow_for_period(
            facts,
            [
                "PaymentsToAcquirePropertyPlantAndEquipment",
                "PaymentsToAcquireProductiveAssets",
            ],
            reference_end,
        )

        cash = self.balance_value_for_period(
            facts,
            [
                "CashAndCashEquivalentsAtCarryingValue",
            ],
            reference_end,
        )

        equity = self.balance_value_for_period(
            facts,
            [
                "StockholdersEquity",
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            ],
            reference_end,
        )

        current_debt = self.balance_value_for_period(
            facts,
            [
                "LongTermDebtCurrent",
                "ShortTermBorrowings",
                "ShortTermDebt",
            ],
            reference_end,
        )

        noncurrent_debt = self.balance_value_for_period(
            facts,
            [
                "LongTermDebtNoncurrent",
                "LongTermDebt",
            ],
            reference_end,
        )

        total_debt = self.balance_value_for_period(
            facts,
            [
                "LongTermDebt",
            ],
            reference_end,
        )

        # Balance-sheet components are only additive when they come from the
        # same reporting date.  Some issuers stop using one standard taxonomy
        # tag, leaving an old fact behind in Company Facts; combining it with a
        # newer component would manufacture an invalid current debt balance.
        if (
            current_debt is not None
            and noncurrent_debt is not None
            and current_debt.get("end")
            and noncurrent_debt.get("end")
            and current_debt.get("end")
            != noncurrent_debt.get("end")
        ):
            current_debt = None

        shares = self.balance_value_for_period(
            facts,
            [
                "CommonStockSharesOutstanding",
            ],
            reference_end,
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

        # Prefer the explicitly split balance-sheet components.  A generic
        # ``LongTermDebt`` fact is not consistently comparable across issuers:
        # it can exclude the current portion, while Yahoo's Total Debt includes
        # it.  Adding the SEC current and non-current facts gives the like-for-
        # like measure used by the validation layer.
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

            debt_source_tag = "+".join(
                tag.get("tag")
                for tag in (
                    current_debt,
                    noncurrent_debt,
                )
                if tag is not None
                and tag.get("tag")
            )

        elif total_debt is not None:

            debt_value = float(
                total_debt["value"]
            )

            debt_source_tag = total_debt.get("tag")

        else:

            debt_value = None
            debt_source_tag = None

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

                    "source_tag": debt_source_tag,
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
