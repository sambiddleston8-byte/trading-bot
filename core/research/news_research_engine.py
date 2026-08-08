from datetime import datetime, timezone


class NewsResearchEngine:

    VERSION = "1.0"

    # ========================================================
    # SOURCE HIERARCHY
    # ========================================================

    SOURCE_TYPES = {

        "SEC EDGAR": {
            "tier": 1,
            "type": "PRIMARY",
            "independence_group": "REGULATORY",
        },

        "COMPANY FILINGS": {
            "tier": 1,
            "type": "PRIMARY",
            "independence_group": "REGULATORY",
        },

        "COMPANY IR": {
            "tier": 1,
            "type": "PRIMARY_CORPORATE",
            "independence_group": "COMPANY",
        },

        "EARNINGS RELEASE": {
            "tier": 1,
            "type": "PRIMARY_CORPORATE",
            "independence_group": "COMPANY",
        },

        "COMPANY TRANSCRIPT": {
            "tier": 1,
            "type": "PRIMARY_CORPORATE",
            "independence_group": "COMPANY",
        },

        "CNBC": {
            "tier": 2,
            "type": "SECONDARY",
            "independence_group": "MEDIA",
        },

        "REUTERS": {
            "tier": 2,
            "type": "SECONDARY",
            "independence_group": "MEDIA",
        },

        "BLOOMBERG": {
            "tier": 2,
            "type": "SECONDARY",
            "independence_group": "MEDIA",
        },

        "FINANCIAL TIMES": {
            "tier": 2,
            "type": "SECONDARY",
            "independence_group": "MEDIA",
        },

        "WALL STREET JOURNAL": {
            "tier": 2,
            "type": "SECONDARY",
            "independence_group": "MEDIA",
        },

        "YAHOO FINANCE": {
            "tier": 3,
            "type": "SECONDARY_DATA",
            "independence_group": "DATA",
        },

        "MARKETWATCH": {
            "tier": 3,
            "type": "SECONDARY",
            "independence_group": "MEDIA",
        },

        "ANALYST": {
            "tier": 3,
            "type": "ANALYST",
            "independence_group": "ANALYST",
        },

        "SOCIAL": {
            "tier": 4,
            "type": "COMMENTARY",
            "independence_group": "SOCIAL",
        },
    }

    # ========================================================
    # EVIDENCE TYPES
    # ========================================================

    EVIDENCE_TYPES = {
        "FACT",
        "EXPECTATION",
        "INTERPRETATION",
        "SPECULATION",
    }

    # ========================================================
    # IMPACT
    # ========================================================

    IMPACTS = {
        "POSITIVE",
        "NEGATIVE",
        "NEUTRAL",
        "MIXED",
    }

    @staticmethod
    def now():

        return datetime.now(
            timezone.utc
        ).isoformat()

    # ========================================================
    # SOURCE METADATA
    # ========================================================

    @classmethod
    def source_metadata(
        cls,
        source,
    ):

        normalized = (
            str(source)
            .upper()
            .strip()
        )

        return cls.SOURCE_TYPES.get(
            normalized,
            {
                "tier": 99,
                "type": "UNKNOWN",
                "independence_group": (
                    f"UNKNOWN:{normalized}"
                ),
            },
        )

    # ========================================================
    # CREATE EVIDENCE
    # ========================================================

    @classmethod
    def create_evidence(
        cls,
        ticker,
        headline,
        source,
        published_at=None,
        url=None,
        summary=None,
        evidence_type="FACT",
        impact="NEUTRAL",
        event_id=None,
        underlying_source=None,
        confidence="MEDIUM",
    ):

        evidence_type = (
            str(evidence_type)
            .upper()
            .strip()
        )

        impact = (
            str(impact)
            .upper()
            .strip()
        )

        if evidence_type not in cls.EVIDENCE_TYPES:

            raise ValueError(
                "Invalid evidence type: "
                f"{evidence_type}"
            )

        if impact not in cls.IMPACTS:

            raise ValueError(
                "Invalid impact: "
                f"{impact}"
            )

        metadata = cls.source_metadata(
            source
        )

        return {

            "ticker":
                ticker,

            "headline":
                headline,

            "source":
                str(source)
                .upper()
                .strip(),

            "source_tier":
                metadata["tier"],

            "source_type":
                metadata["type"],

            "independence_group":
                metadata[
                    "independence_group"
                ],

            "published_at":
                published_at,

            "url":
                url,

            "summary":
                summary,

            "evidence_type":
                evidence_type,

            "impact":
                impact,

            "event_id":
                event_id,

            "underlying_source":
                underlying_source,

            "confidence":
                confidence,

            "created_at":
                cls.now(),
        }

    # ========================================================
    # ADD EVIDENCE
    # ========================================================

    @classmethod
    def add(
        cls,
        research,
        evidence,
    ):

        research.setdefault(
            "evidence",
            [],
        ).append(
            evidence
        )

        return research

    # ========================================================
    # INDEPENDENCE
    #
    # Two articles are NOT independent if they belong to
    # the same underlying source / independence group.
    #
    # Example:
    #
    # Nvidia announcement
    #      ↓
    # CNBC article
    #      ↓
    # Yahoo Finance article
    #
    # That is one underlying piece of information.
    # ========================================================

    @classmethod
    def independence_analysis(
        cls,
        evidence,
    ):

        groups = {}

        for item in evidence:

            group = item.get(
                "independence_group"
            )

            underlying = (
                item.get(
                    "underlying_source"
                )
                or group
            )

            key = (
                group,
                underlying,
            )

            groups.setdefault(
                key,
                [],
            ).append(
                item
            )

        independent_groups = []

        for key, items in groups.items():

            if items:

                independent_groups.append(
                    {
                        "group": key[0],
                        "underlying_source": key[1],
                        "sources": sorted(
                            set(
                                item[
                                    "source"
                                ]
                                for item in items
                            )
                        ),
                        "count": len(items),
                    }
                )

        return independent_groups

    # ========================================================
    # INDEPENDENT SOURCE COUNT
    # ========================================================

    @classmethod
    def independent_source_count(
        cls,
        evidence,
    ):

        groups = set()

        for item in evidence:

            underlying = (
                item.get(
                    "underlying_source"
                )
            )

            if underlying:

                groups.add(
                    underlying
                )

            else:

                groups.add(
                    (
                        item.get(
                            "independence_group"
                        ),
                        item.get(
                            "source"
                        ),
                    )
                )

        return len(groups)

    # ========================================================
    # QUALITY SCORE
    # ========================================================

    @classmethod
    def quality_score(
        cls,
        evidence,
    ):

        if not evidence:

            return {
                "score": 0,
                "confidence": "REVIEW",
            }

        independent_count = (
            cls.independent_source_count(
                evidence
            )
        )

        highest_tier = min(
            item.get(
                "source_tier",
                99,
            )
            for item in evidence
        )

        factual_count = sum(
            1
            for item in evidence
            if item.get(
                "evidence_type"
            ) == "FACT"
        )

        if (
            highest_tier == 1
            and independent_count >= 2
            and factual_count >= 1
        ):

            score = 100
            confidence = "VERY_HIGH"

        elif (
            highest_tier == 1
            and independent_count >= 1
        ):

            score = 90
            confidence = "HIGH"

        elif (
            highest_tier <= 2
            and independent_count >= 2
        ):

            score = 85
            confidence = "HIGH"

        elif (
            highest_tier <= 2
        ):

            score = 70
            confidence = "MEDIUM"

        elif independent_count >= 2:

            score = 60
            confidence = "MEDIUM"

        else:

            score = 40
            confidence = "LOW"

        return {
            "score": score,
            "confidence": confidence,
        }

    # ========================================================
    # RESEARCH SUMMARY
    # ========================================================

    @classmethod
    def summarize(
        cls,
        research,
    ):

        evidence = research.get(
            "evidence",
            [],
        )

        quality = cls.quality_score(
            evidence
        )

        independent_groups = (
            cls.independence_analysis(
                evidence
            )
        )

        return {

            "ticker":
                research.get(
                    "ticker"
                ),

            "evidence_count":
                len(evidence),

            "independent_source_count":
                cls.independent_source_count(
                    evidence
                ),

            "independence_groups":
                independent_groups,

            "facts":
                sum(
                    1
                    for item in evidence
                    if item.get(
                        "evidence_type"
                    ) == "FACT"
                ),

            "expectations":
                sum(
                    1
                    for item in evidence
                    if item.get(
                        "evidence_type"
                    ) == "EXPECTATION"
                ),

            "interpretations":
                sum(
                    1
                    for item in evidence
                    if item.get(
                        "evidence_type"
                    ) == "INTERPRETATION"
                ),

            "speculation":
                sum(
                    1
                    for item in evidence
                    if item.get(
                        "evidence_type"
                    ) == "SPECULATION"
                ),

            "positive":
                sum(
                    1
                    for item in evidence
                    if item.get(
                        "impact"
                    ) == "POSITIVE"
                ),

            "negative":
                sum(
                    1
                    for item in evidence
                    if item.get(
                        "impact"
                    ) == "NEGATIVE"
                ),

            "quality":
                quality,
        }

    # ========================================================
    # BUILD RESEARCH PACKAGE
    # ========================================================

    @classmethod
    def build(
        cls,
        ticker,
    ):

        return {

            "ticker":
                ticker,

            "created_at":
                cls.now(),

            "status":
                "OPEN",

            "evidence":
                [],

            "summary":
                None,
        }

    # ========================================================
    # FINALISE
    # ========================================================

    @classmethod
    def finalise(
        cls,
        research,
    ):

        research[
            "summary"
        ] = cls.summarize(
            research
        )

        research[
            "status"
        ] = "COMPLETE"

        research[
            "completed_at"
        ] = cls.now()

        return research


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 80)
    print("NEWS RESEARCH ENGINE TEST")
    print("=" * 80)

    research = (
        NewsResearchEngine.build(
            "NVDA"
        )
    )

    # --------------------------------------------------------
    # PRIMARY COMPANY EVIDENCE
    # --------------------------------------------------------

    NewsResearchEngine.add(
        research,
        NewsResearchEngine.create_evidence(

            ticker="NVDA",

            headline=(
                "NVIDIA reports quarterly results"
            ),

            source="COMPANY IR",

            published_at=(
                "2026-08-08"
            ),

            evidence_type="FACT",

            impact="POSITIVE",

            event_id="NVDA-EARNINGS-001",

            underlying_source=(
                "NVIDIA-EARNINGS-001"
            ),

            confidence="HIGH",
        ),
    )

    # --------------------------------------------------------
    # CNBC REPORTING THE COMPANY ANNOUNCEMENT
    #
    # This should NOT count as independent confirmation
    # of the underlying company announcement.
    # --------------------------------------------------------

    NewsResearchEngine.add(
        research,
        NewsResearchEngine.create_evidence(

            ticker="NVDA",

            headline=(
                "NVIDIA earnings beat expectations"
            ),

            source="CNBC",

            published_at=(
                "2026-08-08"
            ),

            evidence_type="FACT",

            impact="POSITIVE",

            event_id="NVDA-EARNINGS-001",

            underlying_source=(
                "NVIDIA-EARNINGS-001"
            ),

            confidence="HIGH",
        ),
    )

    # --------------------------------------------------------
    # INDEPENDENT REUTERS REPORT
    # --------------------------------------------------------

    NewsResearchEngine.add(
        research,
        NewsResearchEngine.create_evidence(

            ticker="NVDA",

            headline=(
                "Analysts raise estimates after "
                "AI infrastructure demand data"
            ),

            source="REUTERS",

            published_at=(
                "2026-08-08"
            ),

            evidence_type="INTERPRETATION",

            impact="POSITIVE",

            event_id="NVDA-DEMAND-001",

            underlying_source=(
                "REUTERS-ANALYSIS-001"
            ),

            confidence="MEDIUM",
        ),
    )

    # --------------------------------------------------------
    # NEGATIVE CNBC REPORT FROM DIFFERENT UNDERLYING EVENT
    # --------------------------------------------------------

    NewsResearchEngine.add(
        research,
        NewsResearchEngine.create_evidence(

            ticker="NVDA",

            headline=(
                "New export restrictions could "
                "pressure NVIDIA sales"
            ),

            source="CNBC",

            published_at=(
                "2026-08-08"
            ),

            evidence_type="FACT",

            impact="NEGATIVE",

            event_id="NVDA-EXPORT-001",

            underlying_source=(
                "GOVERNMENT-POLICY-001"
            ),

            confidence="HIGH",
        ),
    )

    research = (
        NewsResearchEngine
        .finalise(
            research
        )
    )

    print()
    print("RESEARCH SUMMARY")

    print(
        research[
            "summary"
        ]
    )

    print()
    print("INDEPENDENCE ANALYSIS")

    for group in (
        research[
            "summary"
        ][
            "independence_groups"
        ]
    ):

        print(group)

    print()
    print(
        "Independent source count:",
        research[
            "summary"
        ][
            "independent_source_count"
        ],
    )

    print()
    print("=" * 80)
    print("NEWS RESEARCH ENGINE OK")
    print("=" * 80)
