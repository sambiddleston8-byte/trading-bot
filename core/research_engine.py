import feedparser
import yfinance as yf
import re

from datetime import datetime
from urllib.parse import quote

from core.filings.sec_engine import SECFilingEngine


class ResearchEngine:

    @staticmethod
    def headline_is_relevant(title, symbol, company_name=None):
        """Require a headline to identify the company before it becomes evidence.

        RSS feeds sometimes return broad market articles even where the URL is
        ticker-specific.  Those articles must not create catalysts for the
        wrong company.
        """
        text = str(title or "").lower()
        ticker = str(symbol or "").strip().lower()
        if len(ticker) >= 2 and re.search(
            rf"(?<![a-z0-9]){re.escape(ticker)}(?![a-z0-9])",
            text,
        ):
            return True

        company = str(company_name or "").lower()
        company = re.sub(
            r"\b(incorporated|inc|corp|corporation|plc|ltd|limited|company|co)\b\.?,?",
            " ",
            company,
        )
        aliases = {
            part.strip()
            for part in re.split(r"[,&/]|\band\b", company)
            if len(part.strip()) >= 4
        }
        aliases.update(re.findall(r"[a-z]{4,}", company))
        return any(
            re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", text)
            for alias in aliases
        )

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

            before_filter = len(research["News"])
            research["News"] = [
                article
                for article in research["News"]
                if self.headline_is_relevant(
                    article.get("Title"),
                    symbol,
                    info.get("longName"),
                )
            ]
            research["News Relevance"] = {
                "checked": before_filter,
                "accepted": len(research["News"]),
                "rejected": before_filter - len(research["News"]),
                "company": info.get("longName"),
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
