from datetime import datetime

from core.data_sources.sec_access import SECJSONClient


class SECFilingEngine:

    SEC_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

    COMPANY_TICKERS_URL = (
        "https://www.sec.gov/files/company_tickers.json"
    )

    def __init__(self, sec_client=None):

        self.sec_client = sec_client or SECJSONClient(
            user_agent="SamPatInvestmentResearch/1.0 research@example.com",
        )

        self.ticker_map = None

    # --------------------------------
    # Load SEC ticker database
    # --------------------------------

    def _load_ticker_map(self):

        if self.ticker_map is not None:

            return self.ticker_map

        data = self.sec_client.get_json(
            self.COMPANY_TICKERS_URL
        )

        ticker_map = {}

        for item in data.values():

            ticker = item.get(
                "ticker"
            )

            cik = item.get(
                "cik_str"
            )

            if ticker and cik:

                ticker_map[
                    ticker.upper()
                ] = str(cik).zfill(10)

        self.ticker_map = ticker_map

        return ticker_map

    # --------------------------------
    # Find CIK
    # --------------------------------

    def get_cik(self, symbol):

        symbol = symbol.upper().strip()

        ticker_map = (
            self._load_ticker_map()
        )

        return ticker_map.get(
            symbol
        )

    # --------------------------------
    # Get filing history
    # --------------------------------

    def get_filings(
        self,
        symbol,
        limit=20,
    ):

        cik = self.get_cik(
            symbol
        )

        if not cik:

            return []

        url = self.SEC_URL.format(
            cik=cik
        )

        data = self.sec_client.get_json(url)

        recent = data.get(
            "filings",
            {}
        ).get(
            "recent",
            {}
        )

        forms = recent.get(
            "form",
            []
        )

        accession_numbers = recent.get(
            "accessionNumber",
            []
        )

        filing_dates = recent.get(
            "filingDate",
            []
        )

        report_dates = recent.get(
            "reportDate",
            []
        )

        primary_documents = recent.get(
            "primaryDocument",
            []
        )

        filings = []

        for i in range(
            min(
                len(forms),
                limit,
            )
        ):

            form = forms[i]

            accession = (
                accession_numbers[i]
                if i < len(accession_numbers)
                else None
            )

            filing_date = (
                filing_dates[i]
                if i < len(filing_dates)
                else None
            )

            report_date = (
                report_dates[i]
                if i < len(report_dates)
                else None
            )

            document = (
                primary_documents[i]
                if i < len(primary_documents)
                else None
            )

            accession_clean = (
                accession.replace(
                    "-",
                    "",
                )
                if accession
                else None
            )

            filing_url = None

            if (
                accession_clean
                and document
            ):

                filing_url = (
                    "https://www.sec.gov/Archives/edgar/data/"
                    f"{int(cik)}/"
                    f"{accession_clean}/"
                    f"{document}"
                )

            filings.append({

                "Form":
                    form,

                "Filing Date":
                    filing_date,

                "Report Date":
                    report_date,

                "Accession Number":
                    accession,

                "Document":
                    document,

                "URL":
                    filing_url,

            })

        return filings

    # --------------------------------
    # Research-relevant filings
    # --------------------------------

    def collect(self, symbol):

        filings = self.get_filings(
            symbol,
            limit=50,
        )

        important_forms = {

            "10-K":
                "Annual Report",

            "10-Q":
                "Quarterly Report",

            "8-K":
                "Current Report",

            "20-F":
                "Foreign Annual Report",

            "6-K":
                "Foreign Current Report",

            "DEF 14A":
                "Proxy Statement",

            "13F-HR":
                "Institutional Holdings",

        }

        selected = []

        for filing in filings:

            form = filing.get(
                "Form"
            )

            if form not in important_forms:

                continue

            filing["Type"] = (
                important_forms[form]
            )

            selected.append(
                filing
            )

        return {

            "Ticker":
                symbol.upper(),

            "Timestamp":
                datetime.now().isoformat(),

            "Source":
                "SEC",

            "Filings":
                selected,

            "Filing Count":
                len(selected),

        }
