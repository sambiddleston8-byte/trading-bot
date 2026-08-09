from datetime import datetime, timezone
import re

import yfinance as yf

from core.research.news_research_engine import (
    NewsResearchEngine,
)


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
        "clinical_trial": {
            "direction": "BOTH",
            "importance": "HIGH",
        },
        "regulatory_approval": {
            "direction": "BOTH",
            "importance": "HIGH",
        },
        "certification": {
            "direction": "BOTH",
            "importance": "HIGH",
        },
        "product": {
            "direction": "POSITIVE",
            "importance": "HIGH",
        },
        "product_launch": {
            "direction": "BOTH",
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
        "major_contract": {
            "direction": "BOTH",
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
            # An unvalidated discovery headline has no allocation effect.
            # Probability is populated later by CatalystProbabilityEngine.
            probability = 0.0

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

        ranked = cls.rank(
            research
        )

        positive_score = sum(
            item.get(
                "catalyst_score",
                0,
            )
            for item in ranked
            if item.get(
                "direction"
            ) == "POSITIVE"
        )

        negative_score = sum(
            item.get(
                "catalyst_score",
                0,
            )
            for item in ranked
            if item.get(
                "direction"
            ) == "NEGATIVE"
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
            "positive_score": round(
                positive_score,
                2,
            ),
            "negative_score": round(
                negative_score,
                2,
            ),
            "net_score": round(
                positive_score - negative_score,
                2,
            ),
        }

    @classmethod
    def analyse(
        cls,
        ticker,
    ):

        ticker = (
            ticker
            .upper()
            .strip()
        )

        research = cls.build(
            ticker
        )

        # ------------------------------------------------
        # Live news evidence.
        # ------------------------------------------------

        news = NewsResearchEngine.analyse(
            ticker
        )

        evidence_items = news.get(
            "evidence",
            [],
        )

        # ------------------------------------------------
        # Keyword-based event classification.
        #
        # This is deliberately conservative. The system
        # creates a catalyst only when there is evidence
        # suggesting an identifiable event.
        # ------------------------------------------------

        rules = [

            # Specific event types are checked before generic parent labels.
            # Each headline is classified once below, preventing a trial or
            # approval from being counted twice in the catalyst model.
            (
                "clinical_trial",
                {
                    "clinical trial",
                    "phase 1",
                    "phase 2",
                    "phase 3",
                    "trial result",
                    "primary endpoint",
                    "study readout",
                },
            ),

            (
                "regulatory_approval",
                {
                    "fda approval",
                    "approved by the fda",
                    "regulatory approval",
                    "marketing authorization",
                    "cleared by the fda",
                    "drug approval",
                },
            ),

            (
                "certification",
                {
                    "faa certification",
                    "faa approved",
                    "type certification",
                    "certified by",
                },
            ),

            (
                "major_contract",
                {
                    "major contract",
                    "contract award",
                    "awarded contract",
                    "multiyear agreement",
                    "multi-year agreement",
                    "purchase order",
                },
            ),

            (
                "product_launch",
                {
                    "product launch",
                    "launches new",
                    "launching new",
                    "commercial launch",
                    "general availability",
                },
            ),

            (
                "earnings",
                {
                    "earnings",
                    "quarter",
                    "revenue",
                    "eps",
                    "profit",
                },
            ),

            (
                "guidance",
                {
                    "guidance",
                    "outlook",
                    "forecast",
                },
            ),

            (
                "regulatory",
                {
                    "regulation",
                    "regulatory",
                    "antitrust",
                    "government",
                    "export",
                    "restriction",
                },
            ),

            (
                "geopolitical",
                {
                    "china",
                    "taiwan",
                    "geopolitical",
                    "trade",
                    "sanction",
                },
            ),

            (
                "industry",
                {
                    "industry",
                    "market",
                    "ai",
                    "datacenter",
                    "semiconductor",
                    "cloud",
                },
            ),

            (
                "contracts",
                {
                    "contract",
                    "deal",
                    "agreement",
                    "order",
                },
            ),

            (
                "product",
                {
                    "launch",
                    "product",
                    "chip",
                    "platform",
                },
            ),

            (
                "customer",
                {
                    "customer",
                    "hyperscaler",
                    "client",
                },
            ),

            (
                "capital_allocation",
                {
                    "buyback",
                    "repurchase",
                    "acquisition",
                    "capex",
                    "capital",
                },
            ),

            (
                "management",
                {
                    "ceo",
                    "cfo",
                    "management",
                    "executive",
                },
            ),

            (
                "legal",
                {
                    "lawsuit",
                    "litigation",
                    "court",
                    "legal",
                    "probe",
                },
            ),

            (
                "macro",
                {
                    "rates",
                    "interest rate",
                    "recession",
                    "inflation",
                    "economy",
                    "economic",
                },
            ),
        ]

        seen = set()

        for evidence in evidence_items:

            title = (
                evidence.get(
                    "headline"
                )
                or ""
            )

            lower = title.lower()

            for category, keywords in rules:

                if not any(
                    keyword in lower
                    for keyword in keywords
                ):
                    continue

                key = (
                    category,
                    title.lower().strip(),
                )

                if key in seen:
                    continue

                seen.add(key)

                impact = 6

                if any(
                    word in lower
                    for word in {
                        "restriction",
                        "ban",
                        "lawsuit",
                        "miss",
                        "cut",
                        "warning",
                        "slowdown",
                        "probe",
                    }
                ):
                    direction = "NEGATIVE"
                    impact = 8

                elif any(
                    word in lower
                    for word in {
                        "beat",
                        "raised",
                        "record",
                        "strong",
                        "contract",
                        "approval",
                        "launch",
                        "surge",
                    }
                ):
                    direction = "POSITIVE"
                    impact = 8

                else:
                    direction = (
                        "POSITIVE"
                        if evidence.get(
                            "impact"
                        ) == "POSITIVE"
                        else
                        "NEGATIVE"
                        if evidence.get(
                            "impact"
                        ) == "NEGATIVE"
                        else
                        "BOTH"
                    )

                catalyst = cls.create(
                    ticker=ticker,
                    title=title,
                    category=category,
                    direction=direction,
                    impact=impact,
                    # Probability is calculated later from the evidence.  A
                    # generic headline must not silently become a 50% event.
                    probability=None,
                    expected_date=(
                        evidence.get(
                            "published_at"
                        )
                    ),
                    source=evidence.get(
                        "source"
                    ),
                    url=evidence.get(
                        "url"
                    ),
                    description=title,
                    already_priced=None,
                    confidence=evidence.get(
                        "confidence",
                        "MEDIUM",
                    ),
                )

                catalyst[
                    "evidence"
                ] = [
                    evidence
                ]

                catalyst[
                    "independent_source_count"
                ] = news.get(
                    "independent_source_count",
                    0,
                )

                cls.add(
                    research,
                    catalyst,
                )

                break

        # ------------------------------------------------
        # Explicit upcoming earnings catalyst.
        # ------------------------------------------------

        try:

            calendar = (
                yf.Ticker(
                    ticker
                ).calendar
            )

            earnings_date = None

            if hasattr(
                calendar,
                "to_dict"
            ):

                calendar_dict = (
                    calendar.to_dict()
                )

                for key, value in (
                    calendar_dict.items()
                ):

                    key_lower = str(
                        key
                    ).lower()

                    if "earnings date" in key_lower:

                        if isinstance(
                            value,
                            dict,
                        ):

                            values = list(
                                value.values()
                            )

                            if values:
                                earnings_date = values[0]

                        elif isinstance(
                            value,
                            list,
                        ):

                            if value:
                                earnings_date = value[0]

            elif isinstance(
                calendar,
                dict,
            ):

                for key, value in calendar.items():

                    if "earnings date" in str(
                        key
                    ).lower():

                        earnings_date = value

            if earnings_date is not None:

                catalyst = cls.create(
                    ticker=ticker,
                    title="Next earnings report",
                    category="earnings",
                    direction="BOTH",
                    impact=9,
                    # The probability engine recognises a dated earnings
                    # calendar event separately from the unknown outcome.
                    probability=None,
                    expected_date=str(
                        earnings_date
                    ),
                    source="YAHOO FINANCE",
                    description=(
                        "Upcoming earnings are a major "
                        "potential information event."
                    ),
                    already_priced=None,
                    confidence="MEDIUM",
                )

                catalyst[
                    "evidence"
                ] = [
                    {
                        "headline":
                            "Upcoming earnings report",

                        "source":
                            "YAHOO FINANCE",

                        "source_type":
                            "SECONDARY_DATA",

                        "source_tier":
                            3,

                        "underlying_source":
                            "YAHOO-EARNINGS-CALENDAR",

                        "published_at":
                            cls.now(),

                        "url":
                            None,

                        "evidence_type":
                            "FACT",

                        "impact":
                            "NEUTRAL",

                        "confidence":
                            "HIGH",

                        "event_date":
                            str(
                                earnings_date
                            ),

                        "description":
                            (
                                "Yahoo Finance earnings "
                                "calendar identifies an "
                                "upcoming earnings event."
                            ),
                    }
                ]

                catalyst[
                    "evidence_count"
                ] = 1

                catalyst[
                    "evidence_status"
                ] = "SUPPORTED"

                cls.add(
                    research,
                    catalyst,
                )

        except Exception:
            pass

        return cls.finalise(
            research
        )

    @classmethod
    def research(
        cls,
        ticker,
    ):

        return cls.analyse(
            ticker
        )

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
