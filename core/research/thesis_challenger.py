from datetime import datetime, timezone


class ThesisChallenger:

    VERSION = "1.0"

    CHALLENGE_AREAS = {
        "growth": {
            "question": "What could cause future growth to disappoint?",
            "severity": "HIGH",
        },
        "earnings": {
            "question": "What could cause earnings to miss expectations?",
            "severity": "HIGH",
        },
        "valuation": {
            "question": "What assumptions are required to justify the valuation?",
            "severity": "HIGH",
        },
        "competition": {
            "question": "Could competitors or substitutes weaken the company's position?",
            "severity": "HIGH",
        },
        "margins": {
            "question": "What could cause margins to deteriorate?",
            "severity": "HIGH",
        },
        "capital_allocation": {
            "question": "Could management allocate capital poorly?",
            "severity": "MEDIUM",
        },
        "balance_sheet": {
            "question": "Could balance-sheet risks become material?",
            "severity": "MEDIUM",
        },
        "management": {
            "question": "What management or execution failures could invalidate the thesis?",
            "severity": "MEDIUM",
        },
        "regulation": {
            "question": "Could regulation or legal action impair the thesis?",
            "severity": "HIGH",
        },
        "macro": {
            "question": "Which macro variables could materially affect the investment?",
            "severity": "MEDIUM",
        },
        "industry": {
            "question": "Could structural industry changes weaken the investment case?",
            "severity": "HIGH",
        },
        "geopolitical": {
            "question": "Could geopolitical developments materially affect the company?",
            "severity": "HIGH",
        },
        "execution": {
            "question": "What operational failures could cause the thesis to fail?",
            "severity": "HIGH",
        },
        "catalyst_failure": {
            "question": "Which expected catalysts could fail, be delayed, or already be priced in?",
            "severity": "HIGH",
        },
        "consensus": {
            "question": "What is the market consensus assuming that could prove incorrect?",
            "severity": "HIGH",
        },
    }

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def build(
        cls,
        ticker,
        positive_thesis=None,
        fundamentals=None,
        valuation=None,
        expectations=None,
    ):

        challenges = []

        for area, definition in cls.CHALLENGE_AREAS.items():

            challenges.append(
                {
                    "area": area,
                    "question": definition["question"],
                    "severity": definition["severity"],
                    "status": "UNTESTED",
                    "evidence": [],
                    "findings": [],
                    "thesis_impact": None,
                    "confidence": None,
                }
            )

        return {
            "ticker": ticker,
            "created_at": cls.now(),
            "status": "OPEN",
            "positive_thesis": positive_thesis,
            "fundamentals": fundamentals,
            "valuation": valuation,
            "expectations": expectations,
            "challenges": challenges,
            "overall_challenge_result": None,
            "thesis_survives": None,
        }

    @classmethod
    def add_finding(
        cls,
        challenge,
        finding,
        impact,
        confidence,
        evidence=None,
    ):

        challenge["findings"].append(
            {
                "finding": finding,
                "impact": impact,
                "confidence": confidence,
                "evidence": evidence or [],
                "recorded_at": cls.now(),
            }
        )

        challenge["status"] = "TESTED"
        challenge["thesis_impact"] = impact
        challenge["confidence"] = confidence

        return challenge

    @classmethod
    def populate_findings(
        cls,
        investigation,
    ):
        """
        Test the investment thesis against the evidence already
        available in the research pipeline.

        This method deliberately uses evidence-driven rules.
        It does not assume that every challenge is negative.
        """

        fundamentals = investigation.get(
            "fundamentals",
            {}
        )

        valuation = investigation.get(
            "valuation",
            {}
        )

        expectations = investigation.get(
            "expectations",
            {}
        )

        catalysts = investigation.get(
            "catalysts",
            {}
        )

        news = investigation.get(
            "news",
            {}
        )

        data_quality = investigation.get(
            "data_quality",
            {}
        )

        challenges = investigation.get(
            "challenges",
            []
        )

        # ----------------------------------------------------
        # Helper
        # ----------------------------------------------------

        def test_area(
            area,
            finding,
            impact,
            confidence,
            evidence=None,
        ):

            for challenge in challenges:

                if (
                    challenge.get(
                        "area"
                    ) == area
                    and challenge.get(
                        "status"
                    ) == "UNTESTED"
                ):

                    cls.add_finding(
                        challenge,
                        finding=finding,
                        impact=impact,
                        confidence=confidence,
                        evidence=evidence,
                    )

                    return True

            return False

        # ----------------------------------------------------
        # 1. VALUATION
        # ----------------------------------------------------

        expected_return = valuation.get(
            "expected_return"
        )

        if (
            expected_return is not None
            and float(
                expected_return
            ) <= -0.10
        ):

            test_area(
                "valuation",
                "Base-case intrinsic value implies materially negative expected return.",
                "SEVERE_NEGATIVE",
                "HIGH",
                [
                    {
                        "type": "VALUATION",
                        "expected_return":
                            expected_return,
                    }
                ],
            )

        elif (
            expected_return is not None
            and float(
                expected_return
            ) < 0
        ):

            test_area(
                "valuation",
                "Base-case valuation does not provide positive expected return.",
                "MATERIAL_NEGATIVE",
                "HIGH",
                [
                    {
                        "type": "VALUATION",
                        "expected_return":
                            expected_return,
                    }
                ],
            )

        else:

            test_area(
                "valuation",
                "Current valuation does not present a material negative expected return.",
                "POSITIVE",
                "MEDIUM",
            )

        # ----------------------------------------------------
        # 2. GROWTH ASSUMPTIONS
        # ----------------------------------------------------

        revenue_growth = (
            expectations.get(
                "forward_revenue_growth"
            )
        )

        eps_growth = (
            expectations.get(
                "forward_eps_growth"
            )
        )

        if (
            revenue_growth is not None
            and float(
                revenue_growth
            ) >= 0.30
        ):

            test_area(
                "growth",
                "The investment case relies on exceptionally strong forward growth.",
                "MATERIAL_NEGATIVE",
                "MEDIUM",
                [
                    {
                        "forward_revenue_growth":
                            revenue_growth,
                        "forward_eps_growth":
                            eps_growth,
                    }
                ],
            )

        else:

            test_area(
                "growth",
                "Forward growth assumptions do not appear exceptionally aggressive.",
                "POSITIVE",
                "MEDIUM",
            )

        # ----------------------------------------------------
        # 3. FORECAST RELIABILITY
        # ----------------------------------------------------

        estimate_consistency = (
            expectations.get(
                "estimate_consistency",
                expectations.get("forecast_confidence"),
            )
        )

        if estimate_consistency in {
            "LOW",
        }:

            test_area(
                "earnings",
                "Forward revenue and EPS estimates have weak internal consistency.",
                "MATERIAL_NEGATIVE",
                "HIGH",
            )

        elif estimate_consistency in {
            "MEDIUM",
            "REVIEW",
        }:

            test_area(
                "earnings",
                "Forward estimates have mixed internal consistency; this does not measure accuracy.",
                "CAUTION",
                "MEDIUM",
            )

        else:

            test_area(
                "earnings",
                "Forecast evidence has sufficient confidence.",
                "POSITIVE",
                "MEDIUM",
            )

        # ----------------------------------------------------
        # 4. CATALYST RISK
        # ----------------------------------------------------

        negative_catalysts = catalysts.get(
            "negative_score",
            0,
        )

        positive_catalysts = catalysts.get(
            "positive_score",
            0,
        )

        if (
            float(
                negative_catalysts
            )
            >
            float(
                positive_catalysts
            )
        ):

            test_area(
                "catalyst_failure",
                "Negative catalysts currently outweigh positive catalysts.",
                "MATERIAL_NEGATIVE",
                "MEDIUM",
            )

        else:

            test_area(
                "catalyst_failure",
                "Positive catalysts are at least as significant as identified negative catalysts.",
                "POSITIVE",
                "MEDIUM",
            )

        # ----------------------------------------------------
        # 5. DATA QUALITY
        # ----------------------------------------------------

        unresolved = data_quality.get(
            "unresolved_discrepancies",
            0,
        )

        if float(
            unresolved
        ) > 0:

            test_area(
                "execution",
                "Unresolved financial-data discrepancies remain.",
                "MATERIAL_NEGATIVE",
                "HIGH",
            )

        else:

            test_area(
                "execution",
                "No unresolved financial-data discrepancies remain.",
                "POSITIVE",
                "HIGH",
            )

        # ----------------------------------------------------
        # 6. CONSENSUS RISK
        # ----------------------------------------------------

        if (
            eps_growth is not None
            and float(
                eps_growth
            ) >= 0.30
        ):

            test_area(
                "consensus",
                "The thesis may depend materially on continued high earnings expectations.",
                "MATERIAL_NEGATIVE",
                "MEDIUM",
                [
                    {
                        "forward_eps_growth":
                            eps_growth,
                    }
                ],
            )

        else:

            test_area(
                "consensus",
                "The thesis does not currently depend on exceptionally high consensus earnings growth.",
                "POSITIVE",
                "LOW",
            )

        # ----------------------------------------------------
        # 7. BALANCE SHEET
        # ----------------------------------------------------

        net_debt = fundamentals.get(
            "net_debt"
        )

        leverage = (
            fundamentals.get(
                "balance_sheet",
                {},
            ).get(
                "net_debt_to_fcf"
            )
        )

        if (
            net_debt is not None
            and float(
                net_debt
            ) < 0
        ):

            test_area(
                "balance_sheet",
                "The company has a net cash position, reducing balance-sheet risk.",
                "POSITIVE",
                "HIGH",
            )

        elif (
            leverage is not None
            and float(leverage) >= 3.0
        ):

            test_area(
                "balance_sheet",
                "Net debt is high relative to free cash flow.",
                "MATERIAL_NEGATIVE",
                "MEDIUM",
            )

        elif net_debt is not None:

            test_area(
                "balance_sheet",
                "The company carries net debt, but leverage is not currently extreme.",
                "CAUTION",
                "MEDIUM",
            )

        # ----------------------------------------------------
        # 8. PROFITABILITY
        # ----------------------------------------------------

        fcf_margin = fundamentals.get(
            "fcf_margin"
        )

        roic = fundamentals.get(
            "roic"
        )

        if (
            fcf_margin is not None
            and float(
                fcf_margin
            ) >= 0.30
            and roic is not None
            and float(
                roic
            ) >= 0.20
        ):

            test_area(
                "margins",
                "Strong free-cash-flow generation and capital returns support the investment thesis.",
                "POSITIVE",
                "HIGH",
            )

        # ----------------------------------------------------
        # 9. RESEARCH EVIDENCE MAPPING
        #
        # Use actual headlines to test previously-uncovered
        # challenge areas. Areas remain UNTESTED when there is
        # no relevant evidence rather than being manufactured.
        # ----------------------------------------------------

        evidence_items = []

        if isinstance(
            news,
            dict,
        ):

            evidence_items = news.get(
                "evidence",
                [],
            )

        keyword_areas = {

            "competition": {
                "amd",
                "intel",
                "custom chip",
                "custom silicon",
                "asic",
                "tpu",
                "competitor",
                "competition",
            },

            "capital_allocation": {
                "buyback",
                "repurchase",
                "acquisition",
                "capex",
                "capital allocation",
            },

            "management": {
                "ceo",
                "cfo",
                "management",
                "executive",
                "jensen",
            },

            "regulation": {
                "regulation",
                "regulatory",
                "antitrust",
                "export restriction",
                "export control",
                "government",
                "probe",
            },

            "macro": {
                "interest rate",
                "rates",
                "inflation",
                "recession",
                "economy",
                "economic slowdown",
            },

            "industry": {
                "industry",
                "semiconductor",
                "ai",
                "datacenter",
                "cloud",
                "hyperscaler",
            },

            "geopolitical": {
                "china",
                "taiwan",
                "geopolitical",
                "trade war",
                "sanction",
            },

            "execution": {
                "supply",
                "production",
                "manufacturing",
                "capacity",
                "delay",
                "delivery",
                "execution",
            },
        }

        for area, keywords in (
            keyword_areas.items()
        ):

            relevant = []

            for evidence in evidence_items:

                headline = (
                    evidence.get(
                        "headline"
                    )
                    or ""
                ).lower()

                if any(
                    keyword in headline
                    for keyword in keywords
                ):

                    relevant.append(
                        evidence
                    )

            if not relevant:
                continue

            negative = sum(
                1
                for item in relevant
                if item.get(
                    "impact"
                ) == "NEGATIVE"
            )

            positive = sum(
                1
                for item in relevant
                if item.get(
                    "impact"
                ) == "POSITIVE"
            )

            if negative > positive:

                impact = "CAUTION"

            elif positive > negative:

                impact = "POSITIVE"

            else:

                impact = "NEUTRAL"

            test_area(
                area,
                (
                    f"{len(relevant)} relevant research "
                    f"evidence item(s) were identified for "
                    f"the {area} challenge."
                ),
                impact,
                "MEDIUM",
                relevant[:5],
            )

        return investigation

    @classmethod
    def calculate_result(cls, investigation):

        challenges = investigation.get(
            "challenges",
            [],
        )

        tested = [
            item
            for item in challenges
            if item.get("status") == "TESTED"
        ]

        material_negative = [
            item
            for item in tested
            if item.get("thesis_impact")
            in {
                "MATERIAL_NEGATIVE",
                "SEVERE_NEGATIVE",
            }
        ]

        positive = [
            item
            for item in tested
            if item.get("thesis_impact") == "POSITIVE"
        ]

        if material_negative:

            result = "THESIS_WEAKENED"
            survives = False

        elif len(tested) < 5:

            result = "INSUFFICIENT_CHALLENGE"
            survives = None

        elif len(positive) > len(material_negative):

            result = "THESIS_SURVIVES"
            survives = True

        else:

            result = "THESIS_REQUIRES_REVIEW"
            survives = None

        investigation["overall_challenge_result"] = result
        investigation["thesis_survives"] = survives
        investigation["status"] = "COMPLETE"
        investigation["completed_at"] = cls.now()

        return investigation

    @classmethod
    def summary(cls, investigation):

        challenges = investigation.get(
            "challenges",
            [],
        )

        return {
            "ticker": investigation.get("ticker"),
            "status": investigation.get("status"),
            "challenge_count": len(challenges),
            "tested": sum(
                1
                for item in challenges
                if item.get("status") == "TESTED"
            ),
            "material_negative": sum(
                1
                for item in challenges
                if item.get("thesis_impact")
                in {
                    "MATERIAL_NEGATIVE",
                    "SEVERE_NEGATIVE",
                }
            ),
            "result": investigation.get(
                "overall_challenge_result"
            ),
            "thesis_survives": investigation.get(
                "thesis_survives"
            ),
        }


if __name__ == "__main__":

    print()
    print("=" * 80)
    print("THESIS CHALLENGER TEST")
    print("=" * 80)

    investigation = ThesisChallenger.build(
        ticker="NVDA",
        positive_thesis=(
            "Strong AI demand, exceptional margins, "
            "strong balance sheet and high expected growth."
        ),
    )

    print(
        "Challenge areas:",
        len(investigation["challenges"]),
    )

    ThesisChallenger.add_finding(
        investigation["challenges"][0],
        "AI infrastructure spending could slow.",
        "MATERIAL_NEGATIVE",
        "MEDIUM",
    )

    ThesisChallenger.add_finding(
        investigation["challenges"][3],
        "Custom accelerators could pressure market share.",
        "MATERIAL_NEGATIVE",
        "MEDIUM",
    )

    ThesisChallenger.add_finding(
        investigation["challenges"][4],
        "Competition could pressure margins.",
        "MATERIAL_NEGATIVE",
        "MEDIUM",
    )

    ThesisChallenger.add_finding(
        investigation["challenges"][13],
        "Expected catalysts may already be priced in.",
        "MATERIAL_NEGATIVE",
        "HIGH",
    )

    result = ThesisChallenger.calculate_result(
        investigation
    )

    print()
    print(
        ThesisChallenger.summary(result)
    )

    print()
    print("=" * 80)
    print("THESIS CHALLENGER OK")
    print("=" * 80)
