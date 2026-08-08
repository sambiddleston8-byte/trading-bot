from datetime import datetime, timezone

from core.data_quality.source_escalation_engine import (
    SourceEscalationEngine,
)


class SourceInvestigationEngine:

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
    # INVESTIGATION REQUEST
    # ========================================================

    @classmethod
    def create_investigation(
        cls,
        ticker,
        field,
        period,
        reconciliation,
    ):

        escalation = (
            SourceEscalationEngine
            .assess(
                field,
                reconciliation,
            )
        )

        return {
            "investigation_id":
                f"{ticker}_{field}_{cls.now()}",
            "ticker":
                ticker,
            "field":
                field,
            "period":
                period,
            "created_at":
                cls.now(),

            "original_reconciliation":
                reconciliation,

            "status":
                "OPEN",

            "escalation":
                escalation,

            "evidence":
                [],

            "resolution":
                None,
        }

    # ========================================================
    # ADD EVIDENCE
    # ========================================================

    @classmethod
    def add_evidence(
        cls,
        investigation,
        source,
        value=None,
        url=None,
        publication_date=None,
        evidence_type=None,
        notes=None,
    ):

        if not isinstance(
            investigation,
            dict,
        ):

            raise TypeError(
                "Investigation must be a dictionary."
            )

        evidence = {
            "source":
                source,

            "value":
                value,

            "url":
                url,

            "publication_date":
                publication_date,

            "evidence_type":
                evidence_type,

            "notes":
                notes,

            "added_at":
                cls.now(),
        }

        investigation.setdefault(
            "evidence",
            [],
        ).append(
            evidence
        )

        return investigation

    # ========================================================
    # RANK EVIDENCE
    # ========================================================

    @classmethod
    def ranked_evidence(
        cls,
        investigation,
    ):

        evidence = investigation.get(
            "evidence",
            [],
        )

        return (
            SourceEscalationEngine
            .rank_evidence(
                evidence
            )
        )

    # ========================================================
    # RESOLVE INVESTIGATION
    # ========================================================

    @classmethod
    def resolve(
        cls,
        investigation,
    ):

        field = investigation.get(
            "field"
        )

        reconciliation = (
            investigation.get(
                "original_reconciliation",
                {},
            )
        )

        evidence = investigation.get(
            "evidence",
            [],
        )

        resolution = (
            SourceEscalationEngine
            .resolve(
                field,
                reconciliation,
                evidence,
            )
        )

        investigation[
            "resolution"
        ] = resolution

        if resolution.get(
            "status"
        ) == "RESOLVED":

            investigation[
                "status"
            ] = "RESOLVED"

        else:

            investigation[
                "status"
            ] = "REVIEW_REQUIRED"

        investigation[
            "resolved_at"
        ] = cls.now()

        return investigation

    # ========================================================
    # SUMMARY
    # ========================================================

    @classmethod
    def summary(
        cls,
        investigation,
    ):

        resolution = (
            investigation.get(
                "resolution"
            )
            or {}
        )

        return {
            "ticker":
                investigation.get(
                    "ticker"
                ),

            "field":
                investigation.get(
                    "field"
                ),

            "period":
                investigation.get(
                    "period"
                ),

            "status":
                investigation.get(
                    "status"
                ),

            "evidence_count":
                len(
                    investigation.get(
                        "evidence",
                        [],
                    )
                ),

            "selected":
                resolution.get(
                    "selected"
                ),

            "selected_source":
                resolution.get(
                    "selected_source"
                ),

            "confidence":
                resolution.get(
                    "confidence"
                ),

            "resolution_status":
                resolution.get(
                    "status"
                ),
        }


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 80)
    print("SOURCE INVESTIGATION ENGINE TEST")
    print("=" * 80)

    reconciliation = {
        "field":
            "Total Debt",

        "status":
            "UNRESOLVED",

        "confidence":
            "REVIEW",

        "sources": {
            "SEC EDGAR":
                8468000000,

            "YAHOO FINANCE":
                11040000000,
        },
    }

    investigation = (
        SourceInvestigationEngine
        .create_investigation(
            ticker="NVDA",
            field="Total Debt",
            period="Latest validated period",
            reconciliation=reconciliation,
        )
    )

    print()
    print("INVESTIGATION CREATED")
    print(
        SourceInvestigationEngine
        .summary(
            investigation
        )
    )

    # --------------------------------------------------------
    # Simulated evidence.
    #
    # These values deliberately represent the type of
    # evidence the future retrieval layer will supply.
    # --------------------------------------------------------

    SourceInvestigationEngine.add_evidence(
        investigation,
        source="COMPANY IR",
        value=8468000000,
        evidence_type="PRIMARY_CORPORATE",
        notes=(
            "Company-reported debt figure."
        ),
    )

    SourceInvestigationEngine.add_evidence(
        investigation,
        source="CNBC",
        value=8468000000,
        evidence_type="SECONDARY_CONTEXT",
        notes=(
            "Financial reporting supporting "
            "the company figure."
        ),
    )

    SourceInvestigationEngine.add_evidence(
        investigation,
        source="YAHOO FINANCE",
        value=11040000000,
        evidence_type="SECONDARY_DATA",
        notes=(
            "Yahoo reported total debt."
        ),
    )

    print()
    print("RANKED EVIDENCE")

    print(
        SourceInvestigationEngine
        .ranked_evidence(
            investigation
        )
    )

    investigation = (
        SourceInvestigationEngine
        .resolve(
            investigation
        )
    )

    print()
    print("INVESTIGATION RESULT")

    print(
        SourceInvestigationEngine
        .summary(
            investigation
        )
    )

    print()
    print("FULL RESOLUTION")

    print(
        investigation[
            "resolution"
        ]
    )

    print()
    print("=" * 80)
    print("SOURCE INVESTIGATION ENGINE OK")
    print("=" * 80)
