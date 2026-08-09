from datetime import datetime, timezone


class EvidenceAuditEngine:

    VERSION = "1.0"

    @staticmethod
    def now():
        return datetime.now(
            timezone.utc
        ).isoformat()

    @staticmethod
    def number(
        value,
        default=0,
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
    # AUDIT FUNDAMENTALS
    # ========================================================

    @classmethod
    def audit_fundamentals(
        cls,
        analysis,
    ):

        findings = []

        scores = analysis.get(
            "scores",
            {}
        )

        fundamental_score = scores.get(
            "fundamental_quality"
        )

        if fundamental_score is None:

            findings.append(
                {
                    "severity": "HIGH",
                    "type": "MISSING_SCORE",
                    "field": "fundamental_quality",
                    "message":
                        "Fundamental score is missing.",
                }
            )

        validation = analysis.get(
            "validation",
            {}
        )

        confidence = validation.get(
            "overall_confidence"
        )

        if confidence is None:

            findings.append(
                {
                    "severity": "MEDIUM",
                    "type": "MISSING_CONFIDENCE",
                    "field": "fundamentals",
                    "message":
                        "Fundamental validation confidence is missing.",
                }
            )

        provenance = analysis.get(
            "provenance",
            {}
        )

        if not provenance:

            findings.append(
                {
                    "severity": "HIGH",
                    "type": "MISSING_PROVENANCE",
                    "field": "fundamentals",
                    "message":
                        "Fundamental analysis has no provenance.",
                }
            )

        return findings

    # ========================================================
    # AUDIT VALUATION
    # ========================================================

    @classmethod
    def audit_valuation(
        cls,
        analysis,
    ):

        findings = []

        valuation = analysis.get(
            "valuation",
            {}
        )

        current_price = valuation.get(
            "current_price"
        )

        if current_price is None:

            current_price = analysis.get(
                "current_price"
            )

        intrinsic_value = valuation.get(
            "base_intrinsic_value"
        )

        if intrinsic_value is None:

            intrinsic_value = analysis.get(
                "base_intrinsic_value"
            )

        expected_return = valuation.get(
            "expected_return"
        )

        if expected_return is None:

            expected_return = analysis.get(
                "expected_return"
            )

        if current_price is None:

            findings.append(
                {
                    "severity": "HIGH",
                    "type": "MISSING_DATA",
                    "field": "current_price",
                    "message":
                        "Current price is missing.",
                }
            )

        if intrinsic_value is None:

            findings.append(
                {
                    "severity": "HIGH",
                    "type": "MISSING_DATA",
                    "field": "base_intrinsic_value",
                    "message":
                        "Base intrinsic value is missing.",
                }
            )

        if (
            intrinsic_value is not None
            and expected_return is None
        ):

            findings.append(
                {
                    "severity": "HIGH",
                    "type": "INCONSISTENCY",
                    "field": "expected_return",
                    "message":
                        "Intrinsic value exists but expected return is missing.",
                }
            )

        return findings

    # ========================================================
    # AUDIT CATALYSTS
    # ========================================================

    @classmethod
    def audit_catalysts(
        cls,
        analysis,
    ):

        findings = []

        catalysts = analysis.get(
            "catalysts",
            []
        )

        if isinstance(
            catalysts,
            dict,
        ):

            catalysts = (
                catalysts.get(
                    "items",
                    []
                )
            )

        for index, catalyst in enumerate(
            catalysts
        ):

            evidence = catalyst.get(
                "evidence",
                []
            )

            if not evidence:

                findings.append(
                    {
                        "severity": "HIGH",
                        "type": "UNSUPPORTED_CATALYST",
                        "field":
                            f"catalysts[{index}]",
                        "message":
                            "Catalyst has no attached evidence.",
                    }
                )

            if not catalyst.get(
                "title"
            ):

                findings.append(
                    {
                        "severity": "MEDIUM",
                        "type": "MISSING_TITLE",
                        "field":
                            f"catalysts[{index}]",
                        "message":
                            "Catalyst has no title.",
                    }
                )

        return findings

    # ========================================================
    # AUDIT THESIS CHALLENGE
    # ========================================================

    @classmethod
    def audit_thesis(
        cls,
        analysis,
    ):

        findings = []

        thesis = analysis.get(
            "thesis_challenge",
            {}
        )

        if not thesis:

            findings.append(
                {
                    "severity": "HIGH",
                    "type": "MISSING_THESIS_CHALLENGE",
                    "field":
                        "thesis_challenge",
                    "message":
                        "No adversarial thesis analysis exists.",
                }
            )

            return findings

        challenge_count = cls.number(
            thesis.get(
                "challenge_count"
            )
        )

        if challenge_count <= 0:

            findings.append(
                {
                    "severity": "MEDIUM",
                    "type": "NO_CHALLENGES",
                    "field":
                        "thesis_challenge",
                    "message":
                        "No thesis challenges were recorded.",
                }
            )

        return findings

    # ========================================================
    # AUDIT DATA QUALITY
    # ========================================================

    @classmethod
    def audit_data_quality(
        cls,
        analysis,
    ):

        findings = []

        quality = analysis.get(
            "data_quality",
            {}
        )

        if not quality:

            findings.append(
                {
                    "severity": "HIGH",
                    "type": "MISSING_DATA_QUALITY",
                    "field":
                        "data_quality",
                    "message":
                        "No data-quality assessment exists.",
                }
            )

            return findings

        unresolved = cls.number(
            quality.get(
                "unresolved_discrepancies"
            )
        )

        if unresolved > 0:

            findings.append(
                {
                    "severity": "HIGH",
                    "type": "UNRESOLVED_DISCREPANCY",
                    "field":
                        "data_quality",
                    "message":
                        f"{int(unresolved)} unresolved data discrepancies remain.",
                }
            )

        return findings

    # ========================================================
    # GLOBAL CONSISTENCY CHECK
    # ========================================================

    @classmethod
    def consistency_check(
        cls,
        analysis,
    ):

        findings = []

        decision = analysis.get(
            "decision"
        )

        valuation = analysis.get(
            "valuation",
            {}
        )

        expected_return = valuation.get(
            "expected_return"
        )

        thesis = analysis.get(
            "thesis_challenge",
            {}
        )

        thesis_result = thesis.get(
            "overall_challenge_result"
        )

        # ----------------------------------------------------
        # Negative valuation + BUY
        # ----------------------------------------------------

        if (
            decision in {
                "BUY",
                "STRONG_BUY",
            }
            and expected_return is not None
            and cls.number(
                expected_return
            ) <= -0.10
        ):

            findings.append(
                {
                    "severity": "CRITICAL",
                    "type": "DECISION_CONTRADICTION",
                    "field":
                        "decision",
                    "message":
                        "Decision is BUY despite materially negative expected return.",
                }
            )

        # ----------------------------------------------------
        # Thesis weakened + strong buy
        # ----------------------------------------------------

        if (
            decision == "STRONG_BUY"
            and thesis_result
            == "THESIS_WEAKENED"
        ):

            findings.append(
                {
                    "severity": "CRITICAL",
                    "type": "THESIS_CONTRADICTION",
                    "field":
                        "decision",
                    "message":
                        "STRONG_BUY conflicts with a materially weakened thesis.",
                }
            )

        return findings

    # ========================================================
    # FULL AUDIT
    # ========================================================

    @classmethod
    def audit(
        cls,
        analysis,
    ):

        findings = []

        findings.extend(
            cls.audit_fundamentals(
                analysis
            )
        )

        findings.extend(
            cls.audit_valuation(
                analysis
            )
        )

        findings.extend(
            cls.audit_catalysts(
                analysis
            )
        )

        findings.extend(
            cls.audit_thesis(
                analysis
            )
        )

        findings.extend(
            cls.audit_data_quality(
                analysis
            )
        )

        findings.extend(
            cls.consistency_check(
                analysis
            )
        )

        critical = sum(
            1
            for finding in findings
            if finding[
                "severity"
            ] == "CRITICAL"
        )

        high = sum(
            1
            for finding in findings
            if finding[
                "severity"
            ] == "HIGH"
        )

        medium = sum(
            1
            for finding in findings
            if finding[
                "severity"
            ] == "MEDIUM"
        )

        if critical > 0:

            status = "FAIL"

        elif high > 0:

            status = "REVIEW"

        elif medium > 0:

            status = "PASS_WITH_WARNINGS"

        else:

            status = "PASS"

        return {

            "status":
                status,

            "audited_at":
                cls.now(),

            "finding_count":
                len(findings),

            "critical":
                critical,

            "high":
                high,

            "medium":
                medium,

            "findings":
                findings,

        }


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 80)
    print("EVIDENCE AUDIT ENGINE TEST")
    print("=" * 80)

    analysis = {

        "ticker":
            "NVDA",

        "scores": {

            "fundamental_quality":
                98,

        },

        "validation": {

            "overall_confidence":
                "HIGH",

        },

        "provenance": {

            "financial_data":
                "SEC EDGAR",

        },

        "valuation": {

            "current_price":
                223.96,

            "base_intrinsic_value":
                181.30,

            "expected_return":
                -0.1905,

        },

        "catalysts": [],

        "thesis_challenge": {

            "challenge_count":
                15,

            "overall_challenge_result":
                "THESIS_WEAKENED",

        },

        "data_quality": {

            "unresolved_discrepancies":
                0,

        },

        "decision":
            "WATCHLIST",

    }

    result = (
        EvidenceAuditEngine
        .audit(
            analysis
        )
    )

    print()
    print("AUDIT STATUS:")
    print(
        result[
            "status"
        ]
    )

    print()
    print("FINDINGS:")
    print(
        result[
            "finding_count"
        ]
    )

    print()
    print("CRITICAL:")
    print(
        result[
            "critical"
        ]
    )

    print()
    print("HIGH:")
    print(
        result[
            "high"
        ]
    )

    print()
    print("MEDIUM:")
    print(
        result[
            "medium"
        ]
    )

    print()
    print("DETAILS:")

    for finding in result[
        "findings"
    ]:

        print(
            finding
        )

    print()
    print("=" * 80)
    print("EVIDENCE AUDIT ENGINE OK")
    print("=" * 80)
