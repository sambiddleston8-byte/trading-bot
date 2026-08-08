from datetime import datetime, timezone


class CatalystEvidenceBridge:

    VERSION = "1.0"

    @staticmethod
    def now():
        return datetime.now(
            timezone.utc
        ).isoformat()

    # ========================================================
    # CONVERT NEWS EVIDENCE INTO CATALYST EVIDENCE
    # ========================================================

    @classmethod
    def attach_evidence(
        cls,
        catalyst,
        evidence,
        independent_source_count=0,
    ):

        catalyst = dict(catalyst)

        catalyst.setdefault(
            "evidence",
            [],
        )

        catalyst["evidence"].append(
            {
                "headline":
                    evidence.get(
                        "headline"
                    ),

                "source":
                    evidence.get(
                        "source"
                    ),

                "source_type":
                    evidence.get(
                        "source_type"
                    ),

                "source_tier":
                    evidence.get(
                        "source_tier"
                    ),

                "underlying_source":
                    evidence.get(
                        "underlying_source"
                    ),

                "published_at":
                    evidence.get(
                        "published_at"
                    ),

                "url":
                    evidence.get(
                        "url"
                    ),

                "evidence_type":
                    evidence.get(
                        "evidence_type"
                    ),

                "impact":
                    evidence.get(
                        "impact"
                    ),

                "confidence":
                    evidence.get(
                        "confidence"
                    ),
            }
        )

        catalyst[
            "independent_source_count"
        ] = independent_source_count

        catalyst[
            "evidence_count"
        ] = len(
            catalyst[
                "evidence"
            ]
        )

        catalyst[
            "evidence_updated_at"
        ] = cls.now()

        return catalyst

    # ========================================================
    # EVIDENCE QUALITY
    # ========================================================

    @classmethod
    def calculate_confidence(
        cls,
        catalyst,
    ):

        evidence = catalyst.get(
            "evidence",
            [],
        )

        independent = catalyst.get(
            "independent_source_count",
            0,
        )

        if not evidence:

            return "REVIEW"

        primary = any(
            item.get(
                "source_tier"
            ) == 1
            for item in evidence
        )

        factual = any(
            item.get(
                "evidence_type"
            ) == "FACT"
            for item in evidence
        )

        if (
            primary
            and independent >= 2
            and factual
        ):

            return "VERY_HIGH"

        if (
            primary
            and factual
        ):

            return "HIGH"

        if independent >= 2:

            return "MEDIUM"

        return "LOW"

    # ========================================================
    # THESIS IMPACT
    # ========================================================

    @classmethod
    def thesis_impact(
        cls,
        catalyst,
    ):

        direction = (
            catalyst.get(
                "direction"
            )
        )

        impact = catalyst.get(
            "impact",
            0,
        )

        if direction == "NEGATIVE":

            if impact >= 9:
                return "SEVERE_NEGATIVE"

            if impact >= 7:
                return "MATERIAL_NEGATIVE"

            if impact >= 4:
                return "MINOR_NEGATIVE"

            return "NEUTRAL"

        if direction == "POSITIVE":

            if impact >= 9:
                return "STRONG_POSITIVE"

            if impact >= 7:
                return "MATERIAL_POSITIVE"

            if impact >= 4:
                return "MINOR_POSITIVE"

            return "NEUTRAL"

        return "NEUTRAL"

    # ========================================================
    # FINALISE CATALYST
    # ========================================================

    @classmethod
    def finalise(
        cls,
        catalyst,
    ):

        catalyst[
            "evidence_confidence"
        ] = cls.calculate_confidence(
            catalyst
        )

        catalyst[
            "thesis_impact"
        ] = cls.thesis_impact(
            catalyst
        )

        catalyst[
            "evidence_status"
        ] = (
            "SUPPORTED"
            if catalyst.get(
                "evidence"
            )
            else "UNSUPPORTED"
        )

        catalyst[
            "evidence_finalised_at"
        ] = cls.now()

        return catalyst


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 80)
    print("CATALYST EVIDENCE BRIDGE TEST")
    print("=" * 80)

    catalyst = {

        "ticker":
            "NVDA",

        "title":
            "Export restriction risk",

        "category":
            "geopolitical",

        "direction":
            "NEGATIVE",

        "impact":
            9,

        "probability":
            0.50,

        "already_priced":
            False,

    }

    evidence = {

        "headline":
            "New export restrictions could pressure NVIDIA sales",

        "source":
            "CNBC",

        "source_type":
            "SECONDARY",

        "source_tier":
            2,

        "underlying_source":
            "GOVERNMENT-POLICY-001",

        "published_at":
            "2026-08-08",

        "url":
            None,

        "evidence_type":
            "FACT",

        "impact":
            "NEGATIVE",

        "confidence":
            "HIGH",
    }

    catalyst = (
        CatalystEvidenceBridge
        .attach_evidence(
            catalyst,
            evidence,
            independent_source_count=2,
        )
    )

    catalyst = (
        CatalystEvidenceBridge
        .finalise(
            catalyst
        )
    )

    print()
    print("CATALYST WITH EVIDENCE")
    print(catalyst)

    print()
    print("EVIDENCE CONFIDENCE:")
    print(
        catalyst[
            "evidence_confidence"
        ]
    )

    print()
    print("THESIS IMPACT:")
    print(
        catalyst[
            "thesis_impact"
        ]
    )

    print()
    print("EVIDENCE STATUS:")
    print(
        catalyst[
            "evidence_status"
        ]
    )

    print()
    print("=" * 80)
    print("CATALYST EVIDENCE BRIDGE OK")
    print("=" * 80)
