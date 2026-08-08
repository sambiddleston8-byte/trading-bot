import feedparser

from core.company_context import CompanyContext


class NewsAnalyser:

    def analyse(self, context: CompanyContext):

        symbol = context.symbol

        url = (
            "https://feeds.finance.yahoo.com/"
            f"rss/2.0/headline?s={symbol}&region=US&lang=en-US"
        )

        feed = feedparser.parse(url)

        headlines = []
        events = []
        catalysts = []

        for entry in feed.entries[:10]:

            headline = entry.get(
                "title",
                ""
            ).strip()

            if not headline:
                continue

            headlines.append(headline)

            events.append(
                {
                    "headline": headline,
                    "category": "news",
                    "sentiment": "neutral",
                    "impact": 3,
                }
            )

            catalysts.append(headline)

        # --------------------------------
        # News Score
        # --------------------------------

        if len(headlines) >= 8:
            news_score = 70

        elif len(headlines) >= 4:
            news_score = 60

        elif len(headlines) > 0:
            news_score = 55

        else:
            news_score = 50

        # --------------------------------
        # Catalyst Score
        # --------------------------------

        if len(catalysts) >= 5:
            catalyst_score = 60

        elif len(catalysts) >= 2:
            catalyst_score = 55

        else:
            catalyst_score = 50

        return {

            "News Score": news_score,

            "Catalyst Score": catalyst_score,

            "Headlines": headlines,

            "Catalysts": catalysts,

            "Events": events,

            "Negative Headlines": 0,

            "Positive Headlines": 0,

        }