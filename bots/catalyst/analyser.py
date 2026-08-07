class CatalystAnalyser:

    CATEGORIES = {

        "legal": [
            "lawsuit",
            "investigation",
            "court",
            "legal",
        ],

        "regulatory": [
            "approval",
            "fda",
            "regulator",
        ],

        "earnings": [
            "earnings",
            "beat",
            "miss",
            "guidance",
        ],

        "contract": [
            "contract",
            "deal",
            "partnership",
            "agreement",
        ],

        "analyst": [
            "upgrade",
            "downgrade",
        ],

        "funding": [
            "offering",
            "dilution",
            "buyback",
        ],

        "product": [
            "launch",
            "product",
            "artificial intelligence",
            "chip",
        ],

    }

    POSITIVE = {
        "beat",
        "upgrade",
        "approval",
        "buyback",
        "contract",
        "partnership",
        "launch",
    }

    NEGATIVE = {
        "miss",
        "downgrade",
        "lawsuit",
        "investigation",
        "offering",
        "dilution",
    }

    IMPACT = {

        "earnings": 8,

        "contract": 7,

        "regulatory": 9,

        "legal": 8,

        "funding": 9,

        "analyst": 5,

        "product": 4,

        "other": 3,

    }

    def classify(self, headline):

        lower = headline.lower()

        category = "other"

        for name, keywords in self.CATEGORIES.items():

            if any(word in lower for word in keywords):

                category = name
                break

        sentiment = "neutral"

        if any(word in lower for word in self.POSITIVE):
            sentiment = "positive"

        elif any(word in lower for word in self.NEGATIVE):
            sentiment = "negative"

        impact = self.IMPACT.get(category, 3)

        return {

            "headline": headline,

            "category": category,

            "sentiment": sentiment,

            "impact": impact,

        }

    def analyse(self, headlines):

        score = 50

        events = []

        for headline in headlines:

            event = self.classify(headline)

            events.append(event)

            if event["sentiment"] == "positive":
                score += event["impact"]

            elif event["sentiment"] == "negative":
                score -= event["impact"]

        score = max(0, min(100, score))

        return {

            "Catalyst Score": score,

            "Events": events,

        }