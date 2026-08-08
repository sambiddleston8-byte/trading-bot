from datetime import datetime, timezone


class SourceEscalationEngine:

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
    # SOURCE PRIORITY
    #
    # Higher priority = greater authority for hard financial
    # figures.
    # ========================================================

    SOURCE_PRIORITY = {
        "SEC EDGAR": 100,
        "COMPANY FILINGS": 100,
        "COMPANY IR": 90,
        "EARNINGS RELEASE": 85,
        "REGULATORY FILING": 85,
        "REPUTABLE FINANCIAL DATA": 60,
        "YAHOO FINANCE": 50,
        "CNBC": 40,
        "OTHER NEWS": 20,
    }

    # ========================================================
    # SOURCE TYPE
    # ========================================================

    SOURCE_TYPES = {
        "SEC EDGAR":
            "PRIMARY",

        "COMPANY FILINGS":
            "PRIMARY",

        "COMPANY IR":
            "PRIMARY_CORPORATE",

        "EARNINGS RELEASE":
            "PRIMARY_CORPORATE",

        "REGULATORY FILING":
            "PRIMARY",

        "REPUTABLE FINANCIAL DATA":
            "SECONDARY_DATA",

        "YAHOO FINANCE":
            "SECONDARY_DATA",

        "CNBC":
            "SECONDARY_CONTEXT",

        "OTHER NEWS":
            "SECONDARY_CONTEXT",
    }

    # ========================================================
    # ESCALATION DECISION
    # ========================================================

    @classmethod
    def assess(
        cls,
        field,
        reconciliation,
    ):

        if not isinstance(
            reconciliation,
            dict,
        ):

            return {
                "field": field,
                "status": "ESCALATE",
                "reason": (
                    "No valid reconciliation "
                    "result was supplied."
                ),
                "next_sources": cls._recommended_sources(),
                "created_at": cls.now(),
            }

        status = reconciliation.get(
            "status"
        )

        confidence = reconciliation.get(
            "confidence"
        )

        # ----------------------------------------------------
        # Already resolved.
        # ----------------------------------------------------

        if status in {
            "AGREED",
            "RESOLVED",
            "DEFINITION_DIFFERENCE",
            "RESOLVED_DEFINITION_DIFFERENCE",
        }:

            return {
                "field": field,
                "status": "NO_ESCALATION_REQUIRED",
                "reconciliation_status":
                    status,
                "confidence":
                    confidence,
                "next_sources": [],
                "reason": (
                    "Primary reconciliation "
                    "successfully resolved the issue."
                ),
                "created_at": cls.now(),
            }

        # ----------------------------------------------------
        # Insufficient data.
        # ----------------------------------------------------

        if status == "INSUFFICIENT_DATA":

            return {
                "field": field,
                "status": "ESCALATE",
                "reconciliation_status":
                    status,
                "confidence":
                    confidence,
                "next_sources":
                    cls._recommended_sources(),
                "reason": (
                    "Insufficient primary-source data "
                    "was available."
                ),
                "created_at": cls.now(),
            }

        # ----------------------------------------------------
        # Unresolved disagreement.
        # ----------------------------------------------------

        return {
            "field": field,
            "status": "ESCALATE",
            "reconciliation_status":
                status,
            "confidence":
                confidence,
            "next_sources":
                cls._recommended_sources(),
            "reason": (
                "Primary sources disagree and the "
                "difference has not been defensibly "
                "explained."
            ),
            "created_at": cls.now(),
        }

    # ========================================================
    # RECOMMENDED SOURCES
    # ========================================================

    @classmethod
    def _recommended_sources(cls):

        return [
            {
                "source":
                    "COMPANY FILINGS",
                "priority":
                    cls.SOURCE_PRIORITY[
                        "COMPANY FILINGS"
                    ],
                "type":
                    cls.SOURCE_TYPES[
                        "COMPANY FILINGS"
                    ],
            },
            {
                "source":
                    "COMPANY IR",
                "priority":
                    cls.SOURCE_PRIORITY[
                        "COMPANY IR"
                    ],
                "type":
                    cls.SOURCE_TYPES[
                        "COMPANY IR"
                    ],
            },
            {
                "source":
                    "EARNINGS RELEASE",
                "priority":
                    cls.SOURCE_PRIORITY[
                        "EARNINGS RELEASE"
                    ],
                "type":
                    cls.SOURCE_TYPES[
                        "EARNINGS RELEASE"
                    ],
            },
            {
                "source":
                    "REPUTABLE FINANCIAL DATA",
                "priority":
                    cls.SOURCE_PRIORITY[
                        "REPUTABLE FINANCIAL DATA"
                    ],
                "type":
                    cls.SOURCE_TYPES[
                        "REPUTABLE FINANCIAL DATA"
                    ],
            },
            {
                "source":
                    "CNBC",
                "priority":
                    cls.SOURCE_PRIORITY[
                        "CNBC"
                    ],
                "type":
                    cls.SOURCE_TYPES[
                        "CNBC"
                    ],
            },
        ]

    # ========================================================
    # SOURCE EVIDENCE
    # ========================================================

    @classmethod
    def rank_evidence(
        cls,
        evidence,
    ):

        if not evidence:
            return []

        ranked = []

        for item in evidence:

            if not isinstance(
                item,
                dict,
            ):
                continue

            source = item.get(
                "source"
            )

            priority = cls.SOURCE_PRIORITY.get(
                source,
                0,
            )

            enriched = dict(item)

            enriched[
                "priority"
            ] = priority

            enriched[
                "source_type"
            ] = cls.SOURCE_TYPES.get(
                source,
                "UNKNOWN",
            )

            ranked.append(
                enriched
            )

        ranked.sort(
            key=lambda item:
                item.get(
                    "priority",
                    0,
                ),
            reverse=True,
        )

        return ranked

    # ========================================================
    # RESOLVE WITH EVIDENCE
    #
    # This does NOT automatically choose the highest source.
    # Evidence must support the resolution.
    # ========================================================

    @classmethod
    def resolve(
        cls,
        field,
        reconciliation,
        evidence,
    ):

        ranked = cls.rank_evidence(
            evidence
        )

        if not ranked:

            return {
                "field": field,
                "status": "UNRESOLVED",
                "confidence": "REVIEW",
                "selected": None,
                "selected_source": None,
                "evidence": [],
                "reason": (
                    "No additional evidence was "
                    "available to resolve the discrepancy."
                ),
                "resolved_at": cls.now(),
            }

        # ----------------------------------------------------
        # Evidence must explicitly provide a usable value.
        # ----------------------------------------------------

        usable = [
            item
            for item in ranked
            if item.get("value") is not None
        ]

        if not usable:

            return {
                "field": field,
                "status": "UNRESOLVED",
                "confidence": "REVIEW",
                "selected": None,
                "selected_source": None,
                "evidence": ranked,
                "reason": (
                    "Additional sources were found, "
                    "but none provided a usable value."
                ),
                "resolved_at": cls.now(),
            }

        # ----------------------------------------------------
        # Count support for each value.
        #
        # We deliberately do not blindly average values.
        # ----------------------------------------------------

        groups = {}

        for item in usable:

            value = item.get(
                "value"
            )

            try:
                key = round(
                    float(value),
                    6,
                )
            except (
                TypeError,
                ValueError,
            ):
                continue

            groups.setdefault(
                key,
                []
            ).append(
                item
            )

        if not groups:

            return {
                "field": field,
                "status": "UNRESOLVED",
                "confidence": "REVIEW",
                "selected": None,
                "selected_source": None,
                "evidence": ranked,
                "reason": (
                    "Evidence values could not "
                    "be compared."
                ),
                "resolved_at": cls.now(),
            }

        # ----------------------------------------------------
        # Select the value supported by the strongest
        # independent evidence.
        # ----------------------------------------------------

        candidates = []

        for value, items in groups.items():

            total_priority = sum(
                item.get(
                    "priority",
                    0,
                )
                for item in items
            )

            candidates.append(
                (
                    total_priority,
                    len(items),
                    value,
                    items,
                )
            )

        candidates.sort(
            reverse=True
        )

        best_priority, support_count, value, items = (
            candidates[0]
        )

        selected_source = (
            items[0].get(
                "source"
            )
        )

        # ----------------------------------------------------
        # Independence matters.
        #
        # Two sources agreeing does NOT automatically mean
        # independent confirmation. News sources may simply
        # repeat company filings or financial databases.
        # ----------------------------------------------------

        independent_primary = any(
            item.get("source")
            in {
                "SEC EDGAR",
                "COMPANY FILINGS",
                "COMPANY IR",
                "EARNINGS RELEASE",
                "REGULATORY FILING",
            }
            for item in items
        )

        independent_secondary = any(
            item.get("source")
            in {
                "REPUTABLE FINANCIAL DATA",
                "YAHOO FINANCE",
            }
            for item in items
        )

        contextual_only = all(
            item.get("source")
            in {
                "CNBC",
                "OTHER NEWS",
            }
            for item in items
        )

        if (
            independent_primary
            and independent_secondary
        ):

            confidence = "HIGH"

        elif independent_primary:

            confidence = "HIGH"

        elif (
            support_count >= 2
            and not contextual_only
        ):

            confidence = "MEDIUM"

        else:

            confidence = "MEDIUM"

        return {
            "field": field,
            "status": "RESOLVED",
            "confidence": confidence,
            "selected":
                value,
            "selected_source":
                selected_source,
            "evidence":
                ranked,
            "supporting_sources":
                [
                    item.get("source")
                    for item in items
                ],

            "independence": {
                "primary_source_present":
                    independent_primary,

                "independent_secondary_present":
                    independent_secondary,

                "contextual_only":
                    contextual_only,

                "supporting_source_count":
                    len(items),
            },
            "reason": (
                "Additional evidence supports "
                "the selected value."
            ),
            "resolved_at":
                cls.now(),
        }


if __name__ == "__main__":

    print()
    print("=" * 80)
    print("SOURCE ESCALATION ENGINE TEST")
    print("=" * 80)

    reconciliation = {
        "field":
            "Total Debt",

        "status":
            "UNRESOLVED",

        "confidence":
            "REVIEW",
    }

    assessment = (
        SourceEscalationEngine
        .assess(
            "Total Debt",
            reconciliation,
        )
    )

    print()
    print("ESCALATION ASSESSMENT")
    print(assessment)

    evidence = [
        {
            "source":
                "CNBC",
            "value":
                8468000000,
        },
        {
            "source":
                "COMPANY IR",
            "value":
                8468000000,
        },
        {
            "source":
                "YAHOO FINANCE",
            "value":
                11040000000,
        },
    ]

    resolution = (
        SourceEscalationEngine
        .resolve(
            "Total Debt",
            reconciliation,
            evidence,
        )
    )

    print()
    print("ESCALATION RESOLUTION")
    print(resolution)

    print()
    print("=" * 80)
    print("SOURCE ESCALATION ENGINE OK")
    print("=" * 80)
