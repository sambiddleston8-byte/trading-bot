from datetime import datetime, timezone


class CatalystValidationEngine:

    VERSION = "1.0"

    # ========================================================
    # TIME
    # ========================================================

    @staticmethod
    def now():

        return datetime.now(
            timezone.utc
        ).isoformat()

    # ========================================================
    # NUMBER
    # ========================================================

    @staticmethod
    def number(
        value,
        default=0.0,
    ):

        try:

            if value is None:
                return default

            return float(value)

        except (
            TypeError,
            ValueError,
        ):

            return default

    # ========================================================
    # EVIDENCE QUALITY
    # ========================================================

    @classmethod
    def evidence_quality(
        cls,
        catalyst,
    ):

        evidence = catalyst.get(
            "evidence",
            []
        )

        if not evidence:

            return {
                "score": 0,
                "confidence": "REVIEW",
                "reason":
                    "No supporting evidence attached.",
            }

        score = 0

        factual_count = 0
        primary_count = 0
        independent_groups = set()

        for item in evidence:

            evidence_type = (
                item.get(
                    "evidence_type"
                )
            )

            source_tier = (
                item.get(
                    "source_tier"
                )
            )

            underlying = (
                item.get(
                    "underlying_source"
                )
            )

            if evidence_type == "FACT":

                factual_count += 1
                score += 20

            elif evidence_type == "INTERPRETATION":

                score += 10

            elif evidence_type == "SPECULATION":

                score += 2

            if source_tier == 1:

                primary_count += 1
                score += 20

            elif source_tier == 2:

                score += 10

            if underlying:

                independent_groups.add(
                    underlying
                )

        independent_count = max(
            len(
                independent_groups
            ),
            cls.number(
                catalyst.get(
                    "independent_source_count"
                )
            ),
        )

        if independent_count >= 3:

            score += 30

        elif independent_count >= 2:

            score += 20

        elif independent_count >= 1:

            score += 5

        score = min(
            score,
            100,
        )

        if score >= 85:

            confidence = "VERY_HIGH"

        elif score >= 70:

            confidence = "HIGH"

        elif score >= 45:

            confidence = "MEDIUM"

        elif score > 0:

            confidence = "LOW"

        else:

            confidence = "REVIEW"

        return {

            "score":
                score,

            "confidence":
                confidence,

            "factual_evidence_count":
                factual_count,

            "primary_source_count":
                primary_count,

            "independent_evidence_count":
                independent_count,

        }

    # ========================================================
    # CONTRADICTION CHECK
    # ========================================================

    @classmethod
    def contradiction_check(
        cls,
        catalyst,
    ):

        evidence = catalyst.get(
            "evidence",
            []
        )

        direction = (
            catalyst.get(
                "direction"
            )
        )

        positive = 0
        negative = 0

        for item in evidence:

            impact = (
                item.get(
                    "impact"
                )
            )

            if impact == "POSITIVE":

                positive += 1

            elif impact == "NEGATIVE":

                negative += 1

        contradictory = False

        if (
            direction == "POSITIVE"
            and negative > positive
        ):

            contradictory = True

        if (
            direction == "NEGATIVE"
            and positive > negative
        ):

            contradictory = True

        if contradictory:

            status = "CONTRADICTED"

        elif positive > 0 and negative > 0:

            status = "MIXED"

        else:

            status = "SUPPORTED"

        return {

            "status":
                status,

            "positive_evidence":
                positive,

            "negative_evidence":
                negative,

        }

    # ========================================================
    # MATERIALITY
    # ========================================================

    @classmethod
    def materiality(
        cls,
        catalyst,
    ):

        impact = cls.number(
            catalyst.get(
                "impact"
            )
        )

        probability = cls.number(
            catalyst.get(
                "probability"
            )
        )

        expected_impact = (
            impact
            * probability
        )

        if expected_impact >= 7:

            classification = (
                "VERY_MATERIAL"
            )

        elif expected_impact >= 5:

            classification = (
                "MATERIAL"
            )

        elif expected_impact >= 3:

            classification = (
                "MODERATE"
            )

        else:

            classification = (
                "LOW"
            )

        return {

            "impact":
                impact,

            "probability":
                probability,

            "expected_impact":
                round(
                    expected_impact,
                    2,
                ),

            "classification":
                classification,

        }

    # ========================================================
    # PRICING CHECK
    # ========================================================

    @classmethod
    def pricing_check(
        cls,
        catalyst,
    ):

        already_priced = catalyst.get(
            "already_priced"
        )

        if already_priced is True:

            return {

                "status":
                    "ALREADY_PRICED",

                "score":
                    0,

                "reason":
                    "Catalyst appears to be reflected in the current valuation.",

            }

        if already_priced is False:

            return {

                "status":
                    "NOT_KNOWN_TO_BE_PRICED",

                "score":
                    100,

                "reason":
                    "No evidence currently indicates that the catalyst is fully reflected in valuation.",

            }

        return {

            "status":
                "UNKNOWN",

            "score":
                50,

            "reason":
                "Insufficient evidence to determine whether the catalyst is priced in.",

        }

    # ========================================================
    # OVERALL VALIDATION
    # ========================================================

    @classmethod
    def validate(
        cls,
        catalyst,
    ):

        evidence = (
            cls.evidence_quality(
                catalyst
            )
        )

        contradiction = (
            cls.contradiction_check(
                catalyst
            )
        )

        materiality = (
            cls.materiality(
                catalyst
            )
        )

        pricing = (
            cls.pricing_check(
                catalyst
            )
        )

        # ----------------------------------------------------
        # Base score
        # ----------------------------------------------------

        score = (

            evidence[
                "score"
            ] * 0.40

            + min(
                materiality[
                    "expected_impact"
                ] * 10,
                100,
            ) * 0.25

            + pricing[
                "score"
            ] * 0.15

        )

        # ----------------------------------------------------
        # Contradiction penalty
        # ----------------------------------------------------

        if (
            contradiction[
                "status"
            ]
            == "CONTRADICTED"
        ):

            score *= 0.50

        elif (
            contradiction[
                "status"
            ]
            == "MIXED"
        ):

            score *= 0.75

        # ----------------------------------------------------
        # No evidence = no catalyst confidence
        # ----------------------------------------------------

        if not evidence[
            "factual_evidence_count"
        ]:

            score *= 0.50

        score = max(
            0,
            min(
                100,
                score,
            ),
        )

        if score >= 85:

            confidence = "VERY_HIGH"

        elif score >= 70:

            confidence = "HIGH"

        elif score >= 50:

            confidence = "MEDIUM"

        elif score >= 30:

            confidence = "LOW"

        else:

            confidence = "REVIEW"

        result = dict(
            catalyst
        )

        result[
            "validation"
        ] = {

            "score":
                round(
                    score,
                    2,
                ),

            "confidence":
                confidence,

            "evidence":
                evidence,

            "contradiction":
                contradiction,

            "materiality":
                materiality,

            "pricing":
                pricing,

        }

        result[
            "validated_at"
        ] = cls.now()

        return result

    # ========================================================
    # PORTFOLIO-FACING EVENT CONTRIBUTION
    # ========================================================

    @classmethod
    def validated_contribution(cls, catalyst):
        """Return a conservative event contribution from validated evidence.

        Discovery headlines are intentionally excluded.  The value combines
        event impact, evidence-led event probability, validation quality and
        whether the event may already be reflected in valuation.
        """
        catalyst = catalyst if isinstance(catalyst, dict) else {}
        validation = catalyst.get("validation") or {}
        if not isinstance(validation, dict):
            return 0.0
        validation_score = cls.number(validation.get("score"))
        probability = cls.number(catalyst.get("probability"))
        impact = cls.number(catalyst.get("impact"))
        if (
            validation_score is None
            or validation_score < 50.0
            or probability is None
            or impact is None
        ):
            return 0.0
        pricing = validation.get("pricing") or {}
        pricing_score = cls.number(pricing.get("score"))
        pricing_multiplier = 0.50 if pricing_score is None else pricing_score / 100.0
        return round(
            max(0.0, impact * probability * (validation_score / 100.0) * pricing_multiplier),
            4,
        )

    @classmethod
    def summary(cls, catalysts):
        """Summarise only catalysts that clear the validation threshold."""
        items = catalysts if isinstance(catalysts, list) else []
        positive = 0.0
        negative = 0.0
        validated = 0
        for item in items:
            contribution = cls.validated_contribution(item)
            if contribution <= 0:
                continue
            validated += 1
            direction = str((item or {}).get("direction") or "").upper()
            if direction == "POSITIVE":
                positive += contribution
            elif direction == "NEGATIVE":
                negative += contribution
        return {
            "method": "VALIDATED_EVIDENCE_WEIGHTED_EVENT_CONTRIBUTION",
            "discovered_count": len(items),
            "validated_count": validated,
            "positive_score": round(positive, 2),
            "negative_score": round(negative, 2),
            "net_score": round(positive - negative, 2),
        }


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 80)
    print("CATALYST VALIDATION ENGINE TEST")
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

        "evidence": [

            {
                "headline":
                    "New export restrictions could pressure NVIDIA sales",

                "source":
                    "CNBC",

                "source_tier":
                    2,

                "underlying_source":
                    "GOVERNMENT-POLICY-001",

                "evidence_type":
                    "FACT",

                "impact":
                    "NEGATIVE",

            },

            {
                "headline":
                    "Government announces additional semiconductor restrictions",

                "source":
                    "COMPANY IR",

                "source_tier":
                    1,

                "underlying_source":
                    "GOVERNMENT-POLICY-001",

                "evidence_type":
                    "FACT",

                "impact":
                    "NEGATIVE",

            },

        ],

        "independent_source_count":
            1,
    }

    result = (
        CatalystValidationEngine
        .validate(
            catalyst
        )
    )

    print()
    print("VALIDATION RESULT")
    print(result)

    print()
    print("SCORE:")
    print(
        result[
            "validation"
        ][
            "score"
        ]
    )

    print()
    print("CONFIDENCE:")
    print(
        result[
            "validation"
        ][
            "confidence"
        ]
    )

    print()
    print("EVIDENCE:")
    print(
        result[
            "validation"
        ][
            "evidence"
        ]
    )

    print()
    print("CONTRADICTION:")
    print(
        result[
            "validation"
        ][
            "contradiction"
        ]
    )

    print()
    print("MATERIALITY:")
    print(
        result[
            "validation"
        ][
            "materiality"
        ]
    )

    print()
    print("PRICING:")
    print(
        result[
            "validation"
        ][
            "pricing"
        ]
    )

    print()
    print("=" * 80)
    print("CATALYST VALIDATION ENGINE OK")
    print("=" * 80)
