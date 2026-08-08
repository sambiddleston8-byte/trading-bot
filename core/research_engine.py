import feedparser
import yfinance as yf

from datetime import datetime
from urllib.parse import quote

from core.filings.sec_engine import SECFilingEngine


class ResearchEngine:

    def __init__(self):

        self.sec = SECFilingEngine()

        self.sources = {

            "Google News":
                "https://news.google.com/rss/search?q={query}",

            "Yahoo Finance":
                "https://finance.yahoo.com/rss/quote/{symbol}?lang=en-US&region=US",
        }

    def collect(self, symbol):

        symbol = symbol.upper().strip()

        research = {

            "Ticker": symbol,

            "Timestamp":
                datetime.now().isoformat(),

            "Sources": [],

            "News": [],

            "Financial Data": {},

            "SEC Filings": {
                "Ticker": symbol,
                "Source": "SEC",
                "Filings": [],
                "Filing Count": 0,
                "Error": None,
            },
        }

        # --------------------------------
        # News Research
        # --------------------------------

        for source_name, url in self.sources.items():

            try:

                if source_name == "Google News":

                    query = quote(
                        f"{symbol} stock company earnings"
                    )

                    feed_url = url.format(
                        query=query
                    )

                else:

                    feed_url = url.format(
                        symbol=symbol
                    )

                feed = feedparser.parse(
                    feed_url
                )

                source_count = 0

                for entry in feed.entries[:15]:

                    title = entry.get(
                        "title"
                    )

                    if not title:
                        continue

                    research["News"].append({

                        "Source":
                            source_name,

                        "Title":
                            title,

                        "Link":
                            entry.get(
                                "link"
                            ),

                        "Published":
                            entry.get(
                                "published"
                            ),
                    })

                    source_count += 1

                if source_count > 0:

                    research[
                        "Sources"
                    ].append(
                        source_name
                    )

            except Exception as error:

                print(
                    f"{source_name} failed: {error}"
                )

        # --------------------------------
        # Financial Data
        # --------------------------------

        try:

            ticker = yf.Ticker(
                symbol
            )

            info = ticker.info

            research[
                "Financial Data"
            ] = {

                "Company":
                    info.get("longName"),

                "Sector":
                    info.get("sector"),

                "Industry":
                    info.get("industry"),

                "Market Cap":
                    info.get("marketCap"),

                "Revenue Growth":
                    info.get("revenueGrowth"),

                "Earnings Growth":
                    info.get("earningsGrowth"),

                "Profit Margin":
                    info.get("profitMargins"),

                "Operating Margin":
                    info.get("operatingMargins"),

                "Forward PE":
                    info.get("forwardPE"),

                "Trailing PE":
                    info.get("trailingPE"),

                "PEG":
                    info.get("pegRatio"),

                "Price To Book":
                    info.get("priceToBook"),

                "ROE":
                    info.get("returnOnEquity"),

                "ROIC":
                    info.get("returnOnInvestedCapital"),

                "Beta":
                    info.get("beta"),

                "Dividend Yield":
                    info.get("dividendYield"),
            }

            if "Yahoo Finance" not in research[
                "Sources"
            ]:

                research[
                    "Sources"
                ].append(
                    "Yahoo Finance"
                )

        except Exception as error:

            print(
                f"Financial research failed: {error}"
            )

        # --------------------------------
        # SEC Filings
        # --------------------------------

        try:

            sec_research = self.sec.collect(
                symbol
            )

            research[
                "SEC Filings"
            ] = sec_research

            if sec_research.get(
                "Filing Count",
                0
            ) > 0:

                research[
                    "Sources"
                ].append(
                    "SEC"
                )

        except Exception as error:

            print(
                f"SEC research failed: {error}"
            )

            research[
                "SEC Filings"
            ] = {

                "Ticker":
                    symbol,

                "Source":
                    "SEC",

                "Filings":
                    [],

                "Filing Count":
                    0,

                "Error":
                    str(error),
            }

        # --------------------------------
        # Remove Duplicate Sources
        # --------------------------------

        research[
            "Sources"
        ] = list(
            dict.fromkeys(
                research["Sources"]
            )
        )

        # --------------------------------
        # Remove Duplicate Headlines
        # --------------------------------

        unique_news = []

        seen_titles = set()

        for article in research["News"]:

            title = article.get(
                "Title"
            )

            if not title:
                continue

            normalized = (
                title.strip().lower()
            )

            if normalized in seen_titles:
                continue

            seen_titles.add(
                normalized
            )

            unique_news.append(
                article
            )

        research[
            "News"
        ] = unique_news

        return research