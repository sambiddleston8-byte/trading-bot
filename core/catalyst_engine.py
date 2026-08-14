import json
import os
import re
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

from core.data_sources.sec_access import SECJSONClient, SECProviderError


class CatalystEngine:

    def __init__(
        self,
        cache_path="data/catalyst_cache.json",
        output_path="data/catalyst_results.json",
        lookahead_days=90,
        recent_days=30,
        sec_client=None,
        session=None,
    ):

        self.cache_path = cache_path
        self.output_path = output_path

        self.lookahead_days = (
            lookahead_days
        )

        self.recent_days = (
            recent_days
        )

        os.makedirs(
            os.path.dirname(
                cache_path
            ),
            exist_ok=True,
        )

        os.makedirs(
            os.path.dirname(
                output_path
            ),
            exist_ok=True,
        )

        # --------------------------------------------------------
        # SEC-compatible declared application identity.
        #
        # No personal email address is stored here.
        # --------------------------------------------------------

        self.sec_client = sec_client or SECJSONClient(
            user_agent="SamAndPatTradingBot/1.0"
        )
        self.session = session or requests.Session()

    # ============================================================
    # TIME
    # ============================================================

    def utc_now(
        self,
    ):

        return datetime.now(
            timezone.utc
        )

    # ============================================================
    # SAFE FLOAT
    # ============================================================

    def safe_float(
        self,
        value,
        default=0.0,
    ):

        try:

            if value is None:

                return default

            return float(
                value
            )

        except (
            TypeError,
            ValueError,
        ):

            return default

    # ============================================================
    # LOAD CACHE
    # ============================================================

    def load_cache(
        self,
    ):

        if not os.path.exists(
            self.cache_path
        ):

            return {}

        try:

            with open(
                self.cache_path,
                "r",
            ) as file:

                data = json.load(
                    file
                )

            if isinstance(
                data,
                dict,
            ):

                return data

        except Exception as error:

            print(
                "Catalyst cache load failed: "
                f"{error}"
            )

        return {}

    # ============================================================
    # SAVE CACHE
    # ============================================================

    def save_cache(
        self,
        data,
    ):

        with open(
            self.cache_path,
            "w",
        ) as file:

            json.dump(
                data,
                file,
                indent=2,
                default=str,
            )

    # ============================================================
    # HTTP GET
    # ============================================================

    def request(
        self,
        url,
        timeout=15,
    ):

        try:

            response = (
                self.session.get(
                    url,
                    timeout=timeout,
                )
            )

            response.raise_for_status()

            return response

        except Exception as error:

            print(
                f"Request failed: "
                f"{error}"
            )

            return None

    # ============================================================
    # SEC TICKER MAP
    # ============================================================

    def get_sec_ticker_map(
        self,
    ):

        cache = (
            self.load_cache()
        )

        cached = cache.get(
            "_SEC_TICKER_MAP"
        )

        if cached:

            return cached

        url = (
            "https://www.sec.gov/files/"
            "company_tickers.json"
        )

        try:
            data = self.sec_client.get_json(url)
        except (SECProviderError, TypeError, ValueError):

            return {}

        cache[
            "_SEC_TICKER_MAP"
        ] = data

        self.save_cache(
            cache
        )

        return data

    # ============================================================
    # GET CIK
    # ============================================================

    def get_cik(
        self,
        symbol,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        ticker_data = (
            self.get_sec_ticker_map()
        )

        for item in (
            ticker_data.values()
        ):

            ticker = str(
                item.get(
                    "ticker",
                    "",
                )
            ).upper()

            if ticker != symbol:

                continue

            cik = item.get(
                "cik_str"
            )

            if cik is None:

                return None

            return str(
                cik
            ).zfill(10)

        return None

    # ============================================================
    # SEC SUBMISSIONS
    # ============================================================

    def get_sec_filings(
        self,
        symbol,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        cache = (
            self.load_cache()
        )

        cache_key = (
            f"SEC_FILINGS_{symbol}"
        )

        cached = cache.get(
            cache_key
        )

        if cached:

            return cached

        cik = self.get_cik(
            symbol
        )

        if not cik:

            return []

        url = (
            "https://data.sec.gov/"
            "submissions/"
            f"CIK{cik}.json"
        )

        try:
            data = self.sec_client.get_json(url)
        except (SECProviderError, TypeError, ValueError):

            return []

        recent = (
            data
            .get(
                "filings",
                {}
            )
            .get(
                "recent",
                {}
            )
        )

        forms = recent.get(
            "form",
            []
        )

        dates = recent.get(
            "filingDate",
            []
        )

        accession_numbers = (
            recent.get(
                "accessionNumber",
                []
            )
        )

        primary_documents = (
            recent.get(
                "primaryDocument",
                []
            )
        )

        primary_doc_descriptions = (
            recent.get(
                "primaryDocDescription",
                []
            )
        )

        filings = []

        for index in range(
            len(forms)
        ):

            filing_date = (
                dates[index]
                if index < len(dates)
                else None
            )

            accession = (
                accession_numbers[index]
                if index < len(
                    accession_numbers
                )
                else None
            )

            document = (
                primary_documents[index]
                if index < len(
                    primary_documents
                )
                else None
            )

            filing_url = None

            if (
                accession
                and document
                and cik
            ):

                accession_clean = (
                    accession.replace(
                        "-",
                        "",
                    )
                )

                filing_url = (
                    "https://www.sec.gov/"
                    "Archives/edgar/data/"
                    f"{int(cik)}/"
                    f"{accession_clean}/"
                    f"{document}"
                )

            filings.append({

                "Form":
                    forms[index],

                "Filing Date":
                    filing_date,

                "Accession Number":
                    accession,

                "Primary Document":
                    document,

                "Description":
                    (
                        primary_doc_descriptions[index]
                        if index < len(
                            primary_doc_descriptions
                        )
                        else None
                    ),

                "URL":
                    filing_url,

            })

        cache[
            cache_key
        ] = filings

        self.save_cache(
            cache
        )

        return filings

    # ============================================================
    # CATALYST CLASSIFICATION
    # ============================================================

    def classify_text(
        self,
        text,
    ):

        text = (
            str(
                text or ""
            )
            .lower()
        )

        patterns = [

            (
                "Earnings",
                [
                    "earnings",
                    "quarterly results",
                    "financial results",
                    "revenue results",
                    "profit results",
                ],
                "HIGH",
            ),

            (
                "Guidance",
                [
                    "guidance",
                    "outlook",
                    "forecast",
                    "raises guidance",
                    "lowers guidance",
                ],
                "VERY HIGH",
            ),

            (
                "Product Launch",
                [
                    "launches",
                    "launch",
                    "new product",
                    "new platform",
                    "new device",
                    "new technology",
                ],
                "HIGH",
            ),

            (
                "Major Contract",
                [
                    "contract",
                    "agreement",
                    "awarded",
                    "customer",
                    "partnership",
                    "deal",
                ],
                "HIGH",
            ),

            (
                "M&A",
                [
                    "acquire",
                    "acquisition",
                    "merger",
                    "merges",
                    "takeover",
                    "strategic combination",
                ],
                "VERY HIGH",
            ),

            (
                "Regulatory",
                [
                    "regulatory",
                    "regulator",
                    "approval",
                    "approved",
                    "investigation",
                    "antitrust",
                    "competition authority",
                ],
                "HIGH",
            ),

            (
                "Clinical / FDA",
                [
                    "fda",
                    "clinical trial",
                    "clinical study",
                    "phase 1",
                    "phase 2",
                    "phase 3",
                    "trial results",
                    "drug approval",
                ],
                "VERY HIGH",
            ),

            (
                "Capital Raising",
                [
                    "offering",
                    "secondary offering",
                    "stock issuance",
                    "debt offering",
                    "capital raise",
                    "financing",
                ],
                "HIGH",
            ),

            (
                "Investor Event",
                [
                    "investor day",
                    "analyst day",
                    "capital markets day",
                    "investor conference",
                ],
                "MEDIUM",
            ),

            (
                "Management",
                [
                    "ceo",
                    "chief executive",
                    "cfo",
                    "chief financial officer",
                    "executive appointment",
                    "resigns",
                    "resignation",
                ],
                "MEDIUM",
            ),

            (
                "Litigation",
                [
                    "lawsuit",
                    "litigation",
                    "settlement",
                    "court",
                    "legal action",
                ],
                "HIGH",
            ),

        ]

        for (
            catalyst_type,
            keywords,
            impact,
        ) in patterns:

            for keyword in keywords:

                if keyword in text:

                    return {

                        "Type":
                            catalyst_type,

                        "Impact":
                            impact,

                        "Matched Keyword":
                            keyword,

                    }

        return None

    # ============================================================
    # SEC FILING CLASSIFICATION
    # ============================================================

    def classify_filing(
        self,
        filing,
    ):

        form = str(
            filing.get(
                "Form",
                "",
            )
        ).upper()

        description = (
            filing.get(
                "Description",
                "",
            )
        )

        text = (
            f"{form} {description}"
        )

        text_classification = (
            self.classify_text(
                text
            )
        )

        # --------------------------------------------------------
        # 8-K
        # --------------------------------------------------------

        if form == "8-K":

            if text_classification:

                return text_classification

            return {

                "Type":
                    "Corporate Event",

                "Impact":
                    "MEDIUM",

                "Matched Keyword":
                    None,

            }

        # --------------------------------------------------------
        # 10-Q
        # --------------------------------------------------------

        if form == "10-Q":

            return {

                "Type":
                    "Quarterly Results",

                "Impact":
                    "HIGH",

                "Matched Keyword":
                    None,

            }

        # --------------------------------------------------------
        # 10-K
        # --------------------------------------------------------

        if form == "10-K":

            return {

                "Type":
                    "Annual Results",

                "Impact":
                    "HIGH",

                "Matched Keyword":
                    None,

            }

        # --------------------------------------------------------
        # DEF 14A
        # --------------------------------------------------------

        if form == "DEF 14A":

            return {

                "Type":
                    "Shareholder / Governance",

                "Impact":
                    "MEDIUM",

                "Matched Keyword":
                    None,

            }

        # --------------------------------------------------------
        # Registration / offering forms
        # --------------------------------------------------------

        if form in (
            "S-1",
            "S-3",
            "424B2",
            "424B3",
            "424B5",
        ):

            return {

                "Type":
                    "Capital Raising",

                "Impact":
                    "HIGH",

                "Matched Keyword":
                    None,

            }

        return None

    # ============================================================
    # RECENT SEC CATALYSTS
    # ============================================================

    def find_recent_sec_catalysts(
        self,
        symbol,
    ):

        filings = (
            self.get_sec_filings(
                symbol
            )
        )

        catalysts = []

        cutoff = (
            self.utc_now()
            - timedelta(
                days=self.recent_days
            )
        )

        for filing in filings:

            filing_date = (
                filing.get(
                    "Filing Date"
                )
            )

            try:

                date = datetime.strptime(
                    filing_date,
                    "%Y-%m-%d",
                ).replace(
                    tzinfo=timezone.utc
                )

            except (
                TypeError,
                ValueError,
            ):

                continue

            if date < cutoff:

                continue

            classification = (
                self.classify_filing(
                    filing
                )
            )

            if not classification:

                continue

            catalysts.append({

                "Source":
                    "SEC",

                "Date":
                    filing_date,

                "Form":
                    filing.get(
                        "Form"
                    ),

                "Type":
                    classification[
                        "Type"
                    ],

                "Impact":
                    classification[
                        "Impact"
                    ],

                "Matched Keyword":
                    classification.get(
                        "Matched Keyword"
                    ),

                "Reason":
                    (
                        f"SEC {filing.get('Form')} "
                        "filing."
                    ),

                "URL":
                    filing.get(
                        "URL"
                    ),

                "Accession Number":
                    filing.get(
                        "Accession Number"
                    ),

            })

        return catalysts

    # ============================================================
    # YAHOO EVENT DATA
    # ============================================================

    def get_yahoo_events(
        self,
        symbol,
    ):

        try:

            import yfinance as yf

            ticker = yf.Ticker(
                symbol
            )

            calendar = (
                ticker.calendar
            )

            if calendar is None:

                return {}

            if hasattr(
                calendar,
                "to_dict",
            ):

                return calendar.to_dict()

            return calendar

        except Exception as error:

            print(
                f"{symbol}: Yahoo calendar failed: "
                f"{error}"
            )

            return {}

    # ============================================================
    # EARNINGS CATALYST
    # ============================================================

    def earnings_catalyst(
        self,
        symbol,
    ):

        calendar = (
            self.get_yahoo_events(
                symbol
            )
        )

        if not calendar:

            return None

        earnings_date = None

        for key in (
            "Earnings Date",
            "earningsDate",
        ):

            if key in calendar:

                earnings_date = (
                    calendar[key]
                )

                break

        if earnings_date is None:

            return None

        if isinstance(
            earnings_date,
            list,
        ):

            if not earnings_date:

                return None

            earnings_date = (
                earnings_date[0]
            )

        try:

            if hasattr(
                earnings_date,
                "date",
            ):

                earnings_date = (
                    earnings_date.date()
                )

            if hasattr(
                earnings_date,
                "strftime",
            ):

                date_string = (
                    earnings_date.strftime(
                        "%Y-%m-%d"
                    )
                )

            else:

                date_string = str(
                    earnings_date
                )

        except Exception:

            return None

        return {

            "Source":
                "Yahoo Finance",

            "Date":
                date_string,

            "Type":
                "Earnings",

            "Impact":
                "HIGH",

            "Reason":
                "Upcoming earnings announcement.",

            "URL":
                None,

        }

    # ============================================================
    # GOOGLE NEWS RSS
    # ============================================================

    def get_news_rss(
        self,
        symbol,
        company_name=None,
    ):

        search_term = (
            company_name
            if company_name
            else symbol
        )

        query = quote(
            f'"{search_term}" stock'
        )

        url = (
            "https://news.google.com/rss/"
            f"search?q={query}"
            "&hl=en-US"
            "&gl=US"
            "&ceid=US:en"
        )

        response = self.request(
            url,
            timeout=15,
        )

        if response is None:

            return []

        try:

            root = (
                ET.fromstring(
                    response.content
                )
            )

        except Exception as error:

            print(
                f"{symbol}: news RSS parse failed: "
                f"{error}"
            )

            return []

        items = []

        for item in root.findall(
            ".//item"
        ):

            title = (
                item.findtext(
                    "title"
                )
                or ""
            )

            description = (
                item.findtext(
                    "description"
                )
                or ""
            )

            link = (
                item.findtext(
                    "link"
                )
                or ""
            )

            pub_date = (
                item.findtext(
                    "pubDate"
                )
            )

            source = (
                item.findtext(
                    "source"
                )
            )

            items.append({

                "Title":
                    title,

                "Description":
                    description,

                "URL":
                    link,

                "Published":
                    pub_date,

                "Source":
                    source,

            })

        return items

    # ============================================================
    # PARSE NEWS DATE
    # ============================================================

    def parse_news_date(
        self,
        value,
    ):

        if not value:

            return None

        try:

            parsed = (
                parsedate_to_datetime(
                    value
                )
            )

            if parsed.tzinfo is None:

                parsed = parsed.replace(
                    tzinfo=timezone.utc
                )

            return parsed.astimezone(
                timezone.utc
            )

        except Exception:

            return None

    # ============================================================
    # NEWS CATALYSTS
    # ============================================================

    def find_news_catalysts(
        self,
        symbol,
        company_name=None,
    ):

        articles = (
            self.get_news_rss(
                symbol,
                company_name,
            )
        )

        catalysts = []

        cutoff = (
            self.utc_now()
            - timedelta(
                days=self.recent_days
            )
        )

        for article in articles:

            title = article.get(
                "Title",
                "",
            )

            description = article.get(
                "Description",
                "",
            )

            text = (
                f"{title} "
                f"{description}"
            )

            classification = (
                self.classify_text(
                    text
                )
            )

            if not classification:

                continue

            published = (
                self.parse_news_date(
                    article.get(
                        "Published"
                    )
                )
            )

            if published is not None:

                if published < cutoff:

                    continue

                date_string = (
                    published.date()
                    .isoformat()
                )

            else:

                date_string = (
                    self.utc_now()
                    .date()
                    .isoformat()
                )

            catalysts.append({

                "Source":
                    article.get(
                        "Source"
                    )
                    or "Google News",

                "Date":
                    date_string,

                "Type":
                    classification[
                        "Type"
                    ],

                "Impact":
                    classification[
                        "Impact"
                    ],

                "Matched Keyword":
                    classification.get(
                        "Matched Keyword"
                    ),

                "Reason":
                    title,

                "URL":
                    article.get(
                        "URL"
                    ),

                "News Title":
                    title,

            })

        return catalysts

    # ============================================================
    # IMPACT SCORE
    # ============================================================

    def impact_score(
        self,
        impact,
    ):

        mapping = {

            "LOW":
                20,

            "MEDIUM":
                50,

            "HIGH":
                80,

            "VERY HIGH":
                100,

        }

        return mapping.get(
            str(
                impact
            ).upper(),
            30,
        )

    # ============================================================
    # TIME SCORE
    # ============================================================

    def time_score(
        self,
        date_string,
    ):

        try:

            date = datetime.strptime(
                str(
                    date_string
                )[:10],
                "%Y-%m-%d",
            ).replace(
                tzinfo=timezone.utc
            )

        except (
            TypeError,
            ValueError,
        ):

            return 0

        days = (
            date
            - self.utc_now()
        ).days

        if days < 0:

            return 10

        if days <= 7:

            return 100

        if days <= 30:

            return 85

        if days <= 60:

            return 70

        if days <= 90:

            return 50

        return 20

    # ============================================================
    # CATALYST PROBABILITY
    # ============================================================

    def catalyst_probability(
        self,
        catalyst,
    ):

        source = str(
            catalyst.get(
                "Source",
                ""
            )
        ).lower()

        impact = str(
            catalyst.get(
                "Impact",
                "MEDIUM"
            )
        ).upper()

        probability = 0.50

        # --------------------------------------------------------
        # Scheduled events have relatively high occurrence
        # probability.
        # --------------------------------------------------------

        if (
            catalyst.get(
                "Type"
            )
            in (
                "Earnings",
                "Investor Event",
            )
        ):

            probability = 0.90

        # --------------------------------------------------------
        # SEC filings are confirmed events.
        # --------------------------------------------------------

        elif source == "sec":

            probability = 1.00

        # --------------------------------------------------------
        # News-reported events are treated as less certain until
        # independently confirmed.
        # --------------------------------------------------------

        elif "news" in source:

            probability = 0.55

        if impact == "VERY HIGH":

            probability += 0.05

        return round(
            min(
                probability,
                1.0,
            ),
            2,
        )

    # ============================================================
    # CATALYST SCORE
    # ============================================================

    def score_catalyst(
        self,
        catalyst,
    ):

        impact = (
            self.impact_score(
                catalyst.get(
                    "Impact",
                    "LOW",
                )
            )
        )

        timing = (
            self.time_score(
                catalyst.get(
                    "Date"
                )
            )
        )

        probability = (
            self.catalyst_probability(
                catalyst
            )
        )

        # --------------------------------------------------------
        # Raw event strength.
        # --------------------------------------------------------

        score = (

            impact
            * 0.45

            +

            timing
            * 0.25

            +

            probability
            * 100
            * 0.30

        )

        catalyst[
            "Probability"
        ] = round(
            probability * 100,
            1,
        )

        catalyst[
            "Impact Score"
        ] = round(
            impact,
            2,
        )

        catalyst[
            "Timing Score"
        ] = round(
            timing,
            2,
        )

        catalyst[
            "Catalyst Score"
        ] = round(
            score,
            2,
        )

        return catalyst

    # ============================================================
    # DEDUPLICATE CATALYSTS
    # ============================================================

    def deduplicate(
        self,
        catalysts,
    ):

        seen = set()
        result = []

        for catalyst in catalysts:

            key = (

                str(
                    catalyst.get(
                        "Type",
                        ""
                    )
                ).lower(),

                str(
                    catalyst.get(
                        "Date",
                        ""
                    )
                )[:10],

                str(
                    catalyst.get(
                        "Reason",
                        ""
                    )
                ).lower()[:120],

            )

            if key in seen:

                continue

            seen.add(
                key
            )

            result.append(
                catalyst
            )

        return result

    # ============================================================
    # ANALYSE STOCK
    # ============================================================

    def analyse(
        self,
        symbol,
        company_name=None,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        catalysts = []

        # --------------------------------------------------------
        # 1. Scheduled earnings
        # --------------------------------------------------------

        earnings = (
            self.earnings_catalyst(
                symbol
            )
        )

        if earnings:

            catalysts.append(
                earnings
            )

        # --------------------------------------------------------
        # 2. SEC events
        # --------------------------------------------------------

        catalysts.extend(
            self.find_recent_sec_catalysts(
                symbol
            )
        )

        # --------------------------------------------------------
        # 3. Web/news events
        # --------------------------------------------------------

        catalysts.extend(
            self.find_news_catalysts(
                symbol,
                company_name,
            )
        )

        # --------------------------------------------------------
        # Remove duplicates.
        # --------------------------------------------------------

        catalysts = (
            self.deduplicate(
                catalysts
            )
        )

        # --------------------------------------------------------
        # Score individual catalysts.
        # --------------------------------------------------------

        scored = []

        for catalyst in catalysts:

            scored.append(
                self.score_catalyst(
                    catalyst
                )
            )

        # --------------------------------------------------------
        # Separate future and recent events.
        # --------------------------------------------------------

        upcoming = []

        recent = []

        now = (
            self.utc_now()
        )

        future_cutoff = (
            now
            + timedelta(
                days=self.lookahead_days
            )
        )

        recent_cutoff = (
            now
            - timedelta(
                days=self.recent_days
            )
        )

        for catalyst in scored:

            try:

                date = datetime.strptime(
                    str(
                        catalyst.get(
                            "Date"
                        )
                    )[:10],
                    "%Y-%m-%d",
                ).replace(
                    tzinfo=timezone.utc
                )

            except (
                TypeError,
                ValueError,
            ):

                continue

            if (
                date >= now
                and date <= future_cutoff
            ):

                upcoming.append(
                    catalyst
                )

            elif date >= recent_cutoff:

                recent.append(
                    catalyst
                )

        # --------------------------------------------------------
        # Rank catalysts.
        # --------------------------------------------------------

        upcoming.sort(
            key=lambda item:
                item.get(
                    "Catalyst Score",
                    0,
                ),
            reverse=True,
        )

        recent.sort(
            key=lambda item:
                item.get(
                    "Catalyst Score",
                    0,
                ),
            reverse=True,
        )

        # --------------------------------------------------------
        # Overall catalyst score.
        #
        # Upcoming catalysts receive greater weight because they
        # are potentially actionable.
        # --------------------------------------------------------

        upcoming_scores = [

            self.safe_float(
                catalyst.get(
                    "Catalyst Score",
                    0,
                )
            )

            for catalyst
            in upcoming

        ]

        recent_scores = [

            self.safe_float(
                catalyst.get(
                    "Catalyst Score",
                    0,
                )
            )

            for catalyst
            in recent

        ]

        strongest_upcoming = (

            max(
                upcoming_scores
            )

            if upcoming_scores

            else 0

        )

        strongest_recent = (

            max(
                recent_scores
            )

            if recent_scores

            else 0

        )

        # --------------------------------------------------------
        # Confirmation bonus.
        #
        # If multiple sources identify relevant catalysts, this
        # increases confidence but is capped.
        # --------------------------------------------------------

        source_types = set()

        for catalyst in scored:

            source = str(
                catalyst.get(
                    "Source",
                    ""
                )
            ).lower()

            if "sec" in source:

                source_types.add(
                    "SEC"
                )

            elif "yahoo" in source:

                source_types.add(
                    "Yahoo"
                )

            elif (
                "news" in source
                or catalyst.get(
                    "News Title"
                )
            ):

                source_types.add(
                    "News"
                )

        confirmation_bonus = 0

        if len(
            source_types
        ) >= 2:

            confirmation_bonus = 10

        elif len(
            source_types
        ) >= 3:

            confirmation_bonus = 15

        catalyst_score = (

            strongest_upcoming
            * 0.70

            +

            strongest_recent
            * 0.15

            +

            confirmation_bonus
            * 0.15

        )

        catalyst_score = round(
            min(
                100,
                catalyst_score,
            ),
            2,
        )

        return {

            "Ticker":
                symbol,

            "Catalyst Score":
                catalyst_score,

            "Catalyst Count":
                len(
                    scored
                ),

            "Source Count":
                len(
                    source_types
                ),

            "Sources":
                sorted(
                    source_types
                ),

            "Upcoming Catalysts":
                upcoming,

            "Recent Catalysts":
                recent,

            "All Catalysts":
                scored,

            "Analysed At":
                self.utc_now().isoformat(),

        }

    # ============================================================
    # ANALYSE UNIVERSE
    # ============================================================

    def analyse_universe(
        self,
        symbols,
        company_names=None,
    ):

        results = []

        if company_names is None:

            company_names = {}

        total = len(
            symbols
        )

        print()
        print("=" * 80)
        print(
            "CATALYST ENGINE"
        )
        print("=" * 80)

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

                result = (
                    self.analyse(
                        symbol,
                        company_name=
                            company_names.get(
                                symbol
                            ),
                    )
                )

                results.append(
                    result
                )

            except Exception as error:

                print(
                    f"{symbol}: catalyst analysis failed: "
                    f"{error}"
                )

        return results

    # ============================================================
    # SAVE RESULTS
    # ============================================================

    def save_results(
        self,
        results,
    ):

        output = {

            "Timestamp":
                self.utc_now().isoformat(),

            "Stocks Analysed":
                len(
                    results
                ),

            "Results":
                results,

        }

        with open(
            self.output_path,
            "w",
        ) as file:

            json.dump(
                output,
                file,
                indent=2,
                default=str,
            )

        return self.output_path

    # ============================================================
    # PRINT STOCK SUMMARY
    # ============================================================

    def print_result(
        self,
        result,
    ):

        print()
        print("=" * 80)
        print(
            f"CATALYST ANALYSIS — "
            f"{result.get('Ticker', '')}"
        )
        print("=" * 80)

        print()

        print(
            f"Catalyst Score: "
            f"{result.get('Catalyst Score', 0):.2f}"
        )

        print(
            f"Catalysts Found: "
            f"{result.get('Catalyst Count', 0)}"
        )

        print(
            f"Sources: "
            f"{', '.join(result.get('Sources', []))}"
        )

        print()

        upcoming = result.get(
            "Upcoming Catalysts",
            [],
        )

        if upcoming:

            print(
                "UPCOMING CATALYSTS"
            )

            print()

            for catalyst in (
                upcoming[:10]
            ):

                print(
                    f"- "
                    f"{catalyst.get('Date', '')} | "
                    f"{catalyst.get('Type', '')} | "
                    f"{catalyst.get('Impact', '')} | "
                    f"{catalyst.get('Catalyst Score', 0):.1f}"
                )

                print(
                    f"  "
                    f"{catalyst.get('Reason', '')}"
                )

        else:

            print(
                "No upcoming catalysts identified."
            )

        print()

        recent = result.get(
            "Recent Catalysts",
            [],
        )

        if recent:

            print(
                "RECENT CATALYSTS"
            )

            print()

            for catalyst in (
                recent[:10]
            ):

                print(
                    f"- "
                    f"{catalyst.get('Date', '')} | "
                    f"{catalyst.get('Type', '')} | "
                    f"{catalyst.get('Impact', '')}"
                )

                print(
                    f"  "
                    f"{catalyst.get('Reason', '')}"
                )

        print()

    # ============================================================
    # DEVELOPMENT RUN
    # ============================================================

    def development_run(
        self,
        symbol,
        company_name=None,
    ):

        result = (
            self.analyse(
                symbol,
                company_name,
            )
        )

        self.print_result(
            result
        )

        self.save_results(
            [result]
        )

        return result


if __name__ == "__main__":

    engine = (
        CatalystEngine()
    )

    # ------------------------------------------------------------
    # Development mode intentionally analyses ONE stock only.
    #
    # We are building the system first. The final universe size
    # will be decided later.
    # ------------------------------------------------------------

    result = (
        engine.development_run(
            "NVDA",
            "NVIDIA Corporation",
        )
    )

    print()

    print(
        f"Saved to: "
        f"{engine.output_path}"
    )
