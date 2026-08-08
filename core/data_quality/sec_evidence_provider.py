import json
import urllib.request
from datetime import datetime, timezone


class SECEvidenceProvider:

    VERSION = "1.0"

    SEC_SUBMISSIONS_URL = (
        "https://data.sec.gov/submissions/"
        "CIK{cik}.json"
    )

    SEC_COMPANY_TICKERS_URL = (
        "https://www.sec.gov/files/"
        "company_tickers.json"
    )

    USER_AGENT = (
        "TradingBot research platform "
        "contact@example.com"
    )

    # ========================================================
    # TIME
    # ========================================================

    @staticmethod
    def now():

        return datetime.now(
            timezone.utc
        ).isoformat()

    # ========================================================
    # HTTP
    # ========================================================

    @classmethod
    def get_json(
        cls,
        url,
    ):

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                    cls.USER_AGENT,
                "Accept":
                    "application/json",
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=20,
        ) as response:

            return json.loads(
                response.read().decode(
                    "utf-8"
                )
            )

    # ========================================================
    # TICKER → CIK
    # ========================================================

    @classmethod
    def get_cik(
        cls,
        ticker,
    ):

        ticker = str(
            ticker
        ).upper().strip()

        data = cls.get_json(
            cls.SEC_COMPANY_TICKERS_URL
        )

        for item in data.values():

            if (
                str(
                    item.get(
                        "ticker",
                        ""
                    )
                ).upper()
                == ticker
            ):

                return str(
                    item["cik_str"]
                ).zfill(10)

        return None

    # ========================================================
    # COMPANY SUBMISSIONS
    # ========================================================

    @classmethod
    def get_submissions(
        cls,
        ticker,
    ):

        cik = cls.get_cik(
            ticker
        )

        if cik is None:

            raise ValueError(
                f"SEC CIK not found for {ticker}"
            )

        url = (
            cls.SEC_SUBMISSIONS_URL
            .format(
                cik=cik
            )
        )

        data = cls.get_json(
            url
        )

        return {
            "ticker":
                ticker,

            "cik":
                cik,

            "data":
                data,

            "retrieved_at":
                cls.now(),

        }

    # ========================================================
    # RECENT FILINGS
    # ========================================================

    @classmethod
    def recent_filings(
        cls,
        ticker,
        forms=None,
    ):

        result = cls.get_submissions(
            ticker
        )

        recent = (
            result["data"]
            .get(
                "filings",
                {}
            )
            .get(
                "recent",
                {}
            )
        )

        forms = (
            forms
            or
            {
                "10-K",
                "10-Q",
                "8-K",
            }
        )

        filings = []

        count = len(
            recent.get(
                "form",
                []
            )
        )

        for index in range(
            count
        ):

            form = recent[
                "form"
            ][index]

            if form not in forms:
                continue

            filings.append(
                {
                    "form":
                        form,

                    "filing_date":
                        recent[
                            "filingDate"
                        ][index],

                    "report_date":
                        recent[
                            "reportDate"
                        ][index],

                    "accession_number":
                        recent[
                            "accessionNumber"
                        ][index],

                    "primary_document":
                        recent[
                            "primaryDocument"
                        ][index],

                    "primary_doc_description":
                        recent[
                            "primaryDocDescription"
                        ][index],

                    "filing_url":
                        (
                            "https://www.sec.gov/"
                            "Archives/edgar/data/"
                            f"{int(result['cik'])}/"
                            f"{recent['accessionNumber'][index].replace('-', '')}/"
                            f"{recent['primaryDocument'][index]}"
                        ),
                }
            )

        return filings

    # ========================================================
    # EVIDENCE RECORD
    # ========================================================

    @classmethod
    def build_evidence_record(
        cls,
        ticker,
        field,
        value,
        filing,
    ):

        return {
            "source":
                "SEC EDGAR",

            "source_type":
                "PRIMARY_REGULATORY",

            "priority":
                100,

            "ticker":
                ticker,

            "field":
                field,

            "value":
                value,

            "url":
                filing.get(
                    "filing_url"
                ),

            "title":
                filing.get(
                    "primary_doc_description"
                ),

            "form":
                filing.get(
                    "form"
                ),

            "filing_date":
                filing.get(
                    "filing_date"
                ),

            "report_date":
                filing.get(
                    "report_date"
                ),

            "accession_number":
                filing.get(
                    "accession_number"
                ),

            "retrieved_at":
                cls.now(),

            "verification":
                {
                    "is_primary":
                        True,

                    "is_contextual":
                        False,

                    "independent":
                        True,
                },
        }


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    ticker = "NVDA"

    print()
    print("=" * 80)
    print("SEC EVIDENCE PROVIDER TEST")
    print("=" * 80)

    print()
    print("LOOKING UP CIK...")

    cik = (
        SECEvidenceProvider
        .get_cik(
            ticker
        )
    )

    print(
        "CIK:",
        cik
    )

    print()
    print("FETCHING RECENT FILINGS...")

    filings = (
        SECEvidenceProvider
        .recent_filings(
            ticker
        )
    )

    print(
        "Filings found:",
        len(filings)
    )

    print()

    for filing in filings[:5]:

        print(
            filing["form"],
            "|",
            filing["filing_date"],
            "|",
            filing["primary_document"],
        )

        print(
            filing["filing_url"]
        )

        print()

    print("=" * 80)
    print("SEC EVIDENCE PROVIDER TEST COMPLETE")
    print("=" * 80)
