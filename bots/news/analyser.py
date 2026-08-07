import yfinance as yf


class NewsAnalyser:

    POSITIVE = [
        "beats",
        "upgrade",
        "buyback",
        "contract",
        "partnership",
        "record",
        "growth",
        "profit",
        "approval",
        "launch",
        "expands",
        "strong"
    ]

    NEGATIVE = [
        "miss",
        "downgrade",
        "lawsuit",
        "decline",
        "delay",
        "cuts",
        "loss",
        "warning",
        "investigation",
        "bankruptcy",
        "recall"
    ]

    def analyse(self, symbol):

        company = yf.Ticker(symbol)

        try:
            news = company.news
        except Exception:
            news = []

        positive = 0
        negative = 0

        for article in news:

            title = (
                article["content"]["title"]
            ).lower()

            for word in self.POSITIVE:

                if word in title:
                    positive += 1

            for word in self.NEGATIVE:

                if word in title:
                    negative += 1

        return {

            "Headline Count": len(news),

            "Positive": positive,

            "Negative": negative,

            "News": news

        }