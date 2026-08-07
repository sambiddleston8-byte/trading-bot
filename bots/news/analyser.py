import yfinance as yf

from bots.catalyst.analyser import CatalystAnalyser


class NewsAnalyser:

    POSITIVE = [
        "beat",
        "growth",
        "record",
        "contract",
        "approval",
        "partnership",
        "profit",
        "upgrade",
        "buyback",
        "launch",
        "expands",
        "surge",
    ]

    NEGATIVE = [
        "miss",
        "downgrade",
        "lawsuit",
        "investigation",
        "delay",
        "recall",
        "decline",
        "loss",
        "offering",
        "dilution",
        "bankruptcy",
    ]

    def __init__(self):
        self.catalyst = CatalystAnalyser()

    def analyse(self, symbol):

        news = yf.Ticker(symbol).news

        positive = 0
        negative = 0

        headlines = []

        for article in news[:10]:

            content = article.get("content", {})
            title = content.get("title", "")

            if title:
                headlines.append(title)

            lower = title.lower()

            if any(word in lower for word in self.POSITIVE):
                positive += 1

            if any(word in lower for word in self.NEGATIVE):
                negative += 1

        news_score = 50 + (positive * 10) - (negative * 10)
        news_score = max(0, min(100, news_score))

        catalyst = self.catalyst.analyse(headlines)

        return {
            "News Score": news_score,
            "Positive Headlines": positive,
            "Negative Headlines": negative,
            "Headlines": headlines,
            "Catalyst Score": catalyst["Catalyst Score"],
            "Events": catalyst["Events"],
            "Catalysts": [
                event["headline"]
                for event in catalyst["Events"]
            ],
        }