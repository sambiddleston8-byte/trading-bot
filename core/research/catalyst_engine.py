from datetime import datetime, timezone


class CatalystEngine:

    VERSION = "1.0"

    CATEGORIES = {
        "earnings": {
            "direction": "BOTH",
            "importance": "HIGH",
        },
        "guidance": {
            "direction": "BOTH",
            "importance": "HIGH",
        },
        "product": {
            "direction": "POSITIVE",
            "importance": "HIGH",
        },
        "regulatory": {
            "direction": "BOTH",
            "importance": "HIGH",
        },
        "contracts": {
            "direction": "POSITIVE",
            "importance": "HIGH",
        },
        "customer": {
            "direction": "BOTH",
            "importance": "HIGH",
        },
        "industry": {
            "direction": "BOTH",
            "importance": "MEDIUM",
        },
        "capital_allocation": {
            "direction": "BOTH",
            "importance": "MEDIUM",
        },
        "acquisition": {
            "direction": "BOTH",
            "importance": "MEDIUM",
        },
        "macro": {
            "direction": "BOTH",
            "importance": "MEDIUM",
        },
        "geopolitical": {
            "direction": "BOTH",
            "importance": "HIGH",
        },
        "legal": {
            "direction": "BOTH",
            "importance": "HIGH",
        },
        "management": {
            "direction": "BOTH",
            "importance": "MEDIUM",
        },
        "industry_event": {
            "direction": "BOTH",
            "importance": "MEDIUM",
        },
    }

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def create(
        cls,
        ticker,
        title,
        category,
        direction,
        impact,
        probability=None,
        expected_date=None,
        source=None,
        url=None,
        description=None,
        already_priced=None,
        confidence=None,
    ):

        category = str(category).lower().strip()

        if category not in cls.CATEGORIES:
            raise ValueError(
                f"Unknown catalyst category: {category}"
            )

        direction = str(direction).upper().strip()

        if direction not in {
            "POSITIVE",
            "NEGATIVE",
            "BOTH",
        }:
            raise ValueError(
                "Direction must be POSITIVE, "
                "NEGATIVE or BOTH."
            )

        return {
            "ticker": ticker,
            "title": title,
            "category": category,
            "direction": direction,
            "impact": impact,
            "probability": probability,
            "expected_date": expected_date,
            "source": source,
            "url": url,
            "description": description,
            "already_priced": already_priced,
            "confidence": confidence,
            "created_at": cls.now(),
        }

    @classmethod
    def add(cls, research, catalyst):

        research.setdefault(
            "catalysts",
            [],
        ).append(catalyst)

        return research

    @classmethod
    def score_catalyst(cls, catalyst):

        impact = catalyst.get("impact")

        probability = catalyst.get(
            "probability"
        )

        already_priced = catalyst.get(
            "already_priced"
        )

        if probability is None:
            probability = 0.5

        if impact is None:
            impact = 0

        score = (
            float(impact)
            * float(probability)
        )

        if already_priced is True:
            score *= 0.25

        return score

    @classmethod
    def rank(cls, research):

        catalysts = research.get(
            "catalysts",
            [],
        )

        ranked = []

        for catalyst in catalysts:

            item = dict(catalyst)

            item["catalyst_score"] = (
                cls.score_catalyst(item)
            )

            ranked.append(item)

        ranked.sort(
            key=lambda item:
                abs(
                    item["catalyst_score"]
                ),
            reverse=True,
        )

        return ranked

    @classmethod
    def summary(cls, research):

        catalysts = research.get(
            "catalysts",
            [],
        )

        return {
            "ticker": research.get("ticker"),
            "total_catalysts": len(catalysts),
            "positive_catalysts": sum(
                1
                for item in catalysts
                if item.get("direction")
                == "POSITIVE"
            ),
            "negative_catalysts": sum(
                1
                for item in catalysts
                if item.get("direction")
                == "NEGATIVE"
            ),
            "high_impact_catalysts": sum(
                1
                for item in catalysts
                if item.get("impact", 0) >= 8
            ),
            "already_priced": sum(
                1
                for item in catalysts
                if item.get("already_priced") is True
            ),
        }

    @classmethod
    def build(cls, ticker):

        return {
            "ticker": ticker,
            "created_at": cls.now(),
            "status": "OPEN",
            "catalysts": [],
            "summary": None,
        }

    @classmethod
    def finalise(cls, research):

        research["catalysts"] = cls.rank(
            research
        )

        research["summary"] = cls.summary(
            research
        )

        research["status"] = "COMPLETE"
        research["completed_at"] = cls.now()

        return research


if __name__ == "__main__":

    print()
    print("=" * 80)
    print("CATALYST ENGINE TEST")
    print("=" * 80)

    research = CatalystEngine.build(
        "NVDA"
    )

    CatalystEngine.add(
        research,
        CatalystEngine.create(
            ticker="NVDA",
            title="Next earnings report",
            category="earnings",
            direction="POSITIVE",
            impact=9,
            probability=0.75,
            expected_date="2026-11-01",
            source="COMPANY IR",
            description=(
                "Potential earnings and guidance upside."
            ),
            already_priced=False,
            confidence="MEDIUM",
        ),
    )

    CatalystEngine.add(
        research,
        CatalystEngine.create(
            ticker="NVDA",
            title="Export restriction risk",
            category="geopolitical",
            direction="NEGATIVE",
            impact=9,
            probability=0.50,
            source="CNBC",
            description=(
                "Further restrictions could reduce sales."
            ),
            already_priced=False,
            confidence="MEDIUM",
        ),
    )

    CatalystEngine.add(
        research,
        CatalystEngine.create(
            ticker="NVDA",
            title="AI spending slowdown",
            category="macro",
            direction="NEGATIVE",
            impact=10,
            probability=0.30,
            source="CNBC",
            description=(
                "Hyperscaler capex slowdown could reduce demand."
            ),
            already_priced=False,
            confidence="LOW",
        ),
    )

    research = CatalystEngine.finalise(
        research
    )

    print()
    print("CATALYST SUMMARY")
    print(research["summary"])

    print()
    print("RANKED CATALYSTS")

    for catalyst in research["catalysts"]:

        print(
            catalyst["direction"],
            "|",
            catalyst["title"],
            "| score:",
            round(
                catalyst["catalyst_score"],
                2,
            ),
        )

    print()
    print("=" * 80)
    print("CATALYST ENGINE OK")
    print("=" * 80)
