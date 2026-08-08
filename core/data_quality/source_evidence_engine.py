from datetime import datetime, timezone


class SourceEvidenceEngine:

    VERSION = "1.0"

    # ========================================================
    # SOURCE PRIORITY
    # ========================================================

    SOURCE_PRIORITY = {
        "SEC EDGAR": 100,
        "COMPANY FILINGS": 100,
        "COMPANY IR": 90,
        "EARNINGS RELEASE": 85,
        "REPUTABLE FINANCIAL DATA": 60,
        "YAHOO FINANCE": 50,
        "CNBC": 40,
        "OTHER NEWS": 20,
    }

    SOURCE_TYPES = {
        "SEC EDGAR":
            "PRIMARY_REGULATORY",

        "COMPANY FILINGS":
            "PRIMARY_REGULATORY",

        "COMPANY IR":
            "PRIMARY_CORPORATE",

        "EARNINGS RELEASE":
            "PRIMARY_CORPORATE",

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
    # TIME
    # ========================================================

    @staticmethod
    def now():

        return datetime.now(
            timezone.utc
        ).isoformat()

    # ========================================================
    # SOURCE NORMALISATION
    # ========================================================

    @classmethod
    def normalise_source(
        cls,
        source,
    ):

        if source is None:
            return None

        source = str(
            source
        ).strip().upper()

        aliases = {
            "SEC":
                "SEC EDGAR",

            "SEC FILING":
                "SEC EDGAR",

            "SEC FILINGS":
                "SEC EDGAR",

            "COMPANY":
                "COMPANY IR",

            "INVESTOR RELATIONS":
                "COMPANY IR",

            "IR":
                "COMPANY IR",

            "YAHOO":
                "YAHOO FINANCE",
        }

        return aliases.get(
            source,
            source,
        )

    # ========================================================
    # EVIDENCE RECORD
    # ========================================================

    @classmethod
    def create_evidence(
        cls,
        source,
        value=None,
        url=None,
        publication_date=None,
        retrieved_at=None,
        evidence_type=None,
        title=None,
        excerpt=None,
        field=None,
        period=None,
        notes=None,
    ):

        source = (
            cls.normalise_source(
                source
            )
        )

        priority = (
            cls.SOURCE_PRIORITY
            .get(
                source,
                10,
            )
        )

        source_type = (
            evidence_type
            or
            cls.SOURCE_TYPES.get(
                source,
                "UNKNOWN",
            )
        )

        return {
            "source":
                source,

            "source_type":
                source_type,

            "priority":
                priority,

            "field":
                field,

            "period":
                period,

            "value":
                value,

            "url":
                url,

            "title":
                title,

            "excerpt":
                excerpt,

            "publication_date":
                publication_date,

            "retrieved_at":
                retrieved_at
                or cls.now(),

            "notes":
                notes,

            "verification":
                {
                    "is_primary":
                        source_type.startswith(
                            "PRIMARY"
                        ),

                    "is_contextual":
                        source_type
                        == "SECONDARY_CONTEXT",

                    "independent":
                        source_type
                        in {
                            "PRIMARY_REGULATORY",
                            "PRIMARY_CORPORATE",
                            "SECONDARY_DATA",
                        },
                },
        }

    # ========================================================
    # SOURCE PLAN
    # ========================================================

    @classmethod
    def build_source_plan(
        cls,
        escalation=None,
    ):

        sources = []

        if escalation:

            for item in (
                escalation.get(
                    "next_sources",
                    [],
                )
            ):

                source = (
                    cls.normalise_source(
                        item.get(
                            "source"
                        )
                    )
                )

                if source:
                    sources.append(
                        {
                            "source":
                                source,

                            "priority":
                                cls.SOURCE_PRIORITY.get(
                                    source,
                                    item.get(
                                        "priority",
                                        10,
                                    ),
                                ),

                            "source_type":
                                cls.SOURCE_TYPES.get(
                                    source,
                                    item.get(
                                        "type",
                                        "UNKNOWN",
                                    ),
                                ),
                        }
                    )

        # Always include the major primary sources
        # if an escalation plan is incomplete.

        required_primary = [
            "SEC EDGAR",
            "COMPANY IR",
            "EARNINGS RELEASE",
        ]

        existing = {
            item["source"]
            for item in sources
        }

        for source in required_primary:

            if source not in existing:

                sources.append(
                    {
                        "source":
                            source,

                        "priority":
                            cls.SOURCE_PRIORITY[
                                source
                            ],

                        "source_type":
                            cls.SOURCE_TYPES[
                                source
                            ],
                    }
                )

        sources.sort(
            key=lambda item:
                item["priority"],
            reverse=True,
        )

        return sources

    # ========================================================
    # VALIDATE EVIDENCE
    # ========================================================

    @classmethod
    def validate_evidence(
        cls,
        evidence,
    ):

        required = [
            "source",
            "value",
            "retrieved_at",
        ]

        missing = [
            field
            for field in required
            if evidence.get(field) is None
        ]

        if missing:

            return {
                "valid":
                    False,

                "missing":
                    missing,

                "reason":
                    "Required evidence fields are missing.",
            }

        return {
            "valid":
                True,

            "missing":
                [],

            "reason":
                "Evidence record is structurally valid.",
        }

    # ========================================================
    # RANK EVIDENCE
    # ========================================================

    @classmethod
    def rank_evidence(
        cls,
        evidence,
    ):

        valid = []

        for item in evidence:

            validation = (
                cls.validate_evidence(
                    item
                )
            )

            if validation["valid"]:

                valid.append(
                    item
                )

        return sorted(
            valid,
            key=lambda item: (
                item.get(
                    "priority",
                    0,
                ),
                item.get(
                    "publication_date"
                )
                or "",
                item.get(
                    "retrieved_at"
                )
                or "",
            ),
            reverse=True,
        )

    # ========================================================
    # BUILD INVESTIGATION PACKAGE
    # ========================================================

    @classmethod
    def build_investigation_package(
        cls,
        ticker,
        field,
        period,
        reconciliation,
        escalation,
        evidence=None,
    ):

        evidence = (
            evidence
            or []
        )

        ranked = (
            cls.rank_evidence(
                evidence
            )
        )

        return {
            "ticker":
                ticker,

            "field":
                field,

            "period":
                period,

            "created_at":
                cls.now(),

            "status":
                "OPEN",

            "reconciliation":
                reconciliation,

            "escalation":
                escalation,

            "source_plan":
                cls.build_source_plan(
                    escalation
                ),

            "evidence":
                ranked,

            "evidence_count":
                len(ranked),

            "resolution_ready":
                len(ranked) > 0,
        }


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 80)
    print("SOURCE EVIDENCE ENGINE TEST")
    print("=" * 80)

    escalation = {
        "status":
            "ESCALATE",

        "next_sources": [
            {
                "source":
                    "COMPANY FILINGS",

                "priority":
                    100,

                "type":
                    "PRIMARY",
            },
            {
                "source":
                    "COMPANY IR",

                "priority":
                    90,

                "type":
                    "PRIMARY_CORPORATE",
            },
            {
                "source":
                    "EARNINGS RELEASE",

                "priority":
                    85,

                "type":
                    "PRIMARY_CORPORATE",
            },
            {
                "source":
                    "REPUTABLE FINANCIAL DATA",

                "priority":
                    60,

                "type":
                    "SECONDARY_DATA",
            },
            {
                "source":
                    "CNBC",

                "priority":
                    40,

                "type":
                    "SECONDARY_CONTEXT",
            },
        ],
    }

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

    evidence = [

        SourceEvidenceEngine.create_evidence(
            source="SEC EDGAR",
            value=8468000000,
            url="https://www.sec.gov/",
            field="Total Debt",
            period="Latest validated period",
            title="SEC filing",
            excerpt="Debt reported in regulatory filing.",
            notes="Primary regulatory evidence.",
        ),

        SourceEvidenceEngine.create_evidence(
            source="CNBC",
            value=8468000000,
            url="https://www.cnbc.com/",
            field="Total Debt",
            period="Latest validated period",
            title="Financial reporting",
            excerpt="Reported debt figure.",
            notes="Secondary contextual evidence.",
        ),

        SourceEvidenceEngine.create_evidence(
            source="YAHOO",
            value=11040000000,
            field="Total Debt",
            period="Latest validated period",
            notes="Secondary financial dataset.",
        ),
    ]

    package = (
        SourceEvidenceEngine
        .build_investigation_package(
            ticker="NVDA",
            field="Total Debt",
            period="Latest validated period",
            reconciliation=reconciliation,
            escalation=escalation,
            evidence=evidence,
        )
    )

    print()
    print("SOURCE PLAN")

    for source in package[
        "source_plan"
    ]:

        print(
            source
        )

    print()
    print("RANKED EVIDENCE")

    for item in package[
        "evidence"
    ]:

        print(
            item["source"],
            "→",
            item["value"],
            "|",
            item["source_type"],
            "| priority",
            item["priority"],
        )

    print()
    print("EVIDENCE COUNT:")
    print(
        package[
            "evidence_count"
        ]
    )

    print()
    print("RESOLUTION READY:")
    print(
        package[
            "resolution_ready"
        ]
    )

    print()
    print("=" * 80)
    print("SOURCE EVIDENCE ENGINE OK")
    print("=" * 80)
