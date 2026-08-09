from datetime import datetime, timezone


class ResearchSynthesisEngine:

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
    # SAFE NUMBER
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
    # SAFE SCORE
    # ========================================================

    @classmethod
    def score(
        cls,
        value,
        default=50,
    ):

        value = cls.number(
            value,
            default,
        )

        return max(
            0,
            min(
                100,
                value,
            ),
        )

    # ========================================================
    # CONFIDENCE VALUE
    # ========================================================

    @staticmethod
    def confidence_value(
        confidence,
    ):

        mapping = {

            "VERY_HIGH":
                100,

            "HIGH":
                85,

            "MEDIUM":
                65,

            "LOW":
                40,

            "REVIEW":
                20,

        }

        return mapping.get(
            str(
                confidence
            ).upper(),
            50,
        )

    # ========================================================
    # FUNDAMENTAL SYNTHESIS
    # ========================================================

    @classmethod
    def fundamental_synthesis(
        cls,
        analysis,
    ):

        scores = analysis.get(
            "scores",
            {}
        )

        fundamental_score = cls.score(
            scores.get(
                "fundamental_quality"
            )
        )

        fundamentals = analysis.get(
            "fundamentals",
            {}
        )

        drivers = []

        if isinstance(
            fundamentals,
            dict,
        ):

            drivers = fundamentals.get(
                "drivers",
                []
            )

            if isinstance(
                drivers,
                str,
            ):

                drivers = [
                    drivers
                ]

        if fundamental_score >= 90:

            conclusion = (
                "Exceptional fundamental quality."
            )

        elif fundamental_score >= 80:

            conclusion = (
                "Strong fundamental quality."
            )

        elif fundamental_score >= 70:

            conclusion = (
                "Good fundamental quality."
            )

        elif fundamental_score >= 60:

            conclusion = (
                "Mixed fundamental quality."
            )

        else:

            conclusion = (
                "Weak fundamental quality."
            )

        return {

            "score":
                fundamental_score,

            "conclusion":
                conclusion,

            "drivers":
                drivers,

        }

    # ========================================================
    # VALUATION SYNTHESIS
    # ========================================================

    @classmethod
    def valuation_synthesis(
        cls,
        analysis,
    ):

        valuation = analysis.get(
            "valuation",
            {}
        )

        expected_return = (
            valuation.get(
                "expected_return"
            )
        )

        expected_return = (
            None
            if expected_return is None
            else cls.number(
                expected_return
            )
        )

        valuation_score = cls.score(
            analysis.get(
                "scores",
                {}
            ).get(
                "valuation"
            )
        )

        status = (
            valuation.get(
                "status"
            )
            or
            "UNKNOWN"
        )

        if expected_return is None:

            conclusion = (
                "Valuation cannot be reliably assessed."
            )

        elif expected_return >= 0.25:

            conclusion = (
                "Material upside appears to exist."
            )

        elif expected_return >= 0.10:

            conclusion = (
                "Valuation appears attractive."
            )

        elif expected_return >= 0:

            conclusion = (
                "Valuation appears broadly fair."
            )

        elif expected_return >= -0.10:

            conclusion = (
                "Valuation appears somewhat expensive."
            )

        elif expected_return >= -0.25:

            conclusion = (
                "Valuation appears materially expensive."
            )

        else:

            conclusion = (
                "Valuation appears severely expensive."
            )

        return {

            "score":
                valuation_score,

            "expected_return":
                expected_return,

            "status":
                status,

            "conclusion":
                conclusion,

        }

    # ========================================================
    # CATALYST SYNTHESIS
    # ========================================================

    @classmethod
    def catalyst_synthesis(
        cls,
        analysis,
    ):

        catalysts = analysis.get(
            "catalysts",
            {}
        )

        if not isinstance(
            catalysts,
            dict,
        ):

            catalysts = {}

        positive = cls.number(
            catalysts.get(
                "positive_score"
            )
        )

        negative = cls.number(
            catalysts.get(
                "negative_score"
            )
        )

        net = (
            positive
            - negative
        )

        if net >= 5:

            conclusion = (
                "Catalyst balance is strongly positive."
            )

        elif net >= 2:

            conclusion = (
                "Catalyst balance is positive."
            )

        elif net > -2:

            conclusion = (
                "Catalyst balance is mixed."
            )

        elif net > -5:

            conclusion = (
                "Catalyst balance is negative."
            )

        else:

            conclusion = (
                "Catalyst balance is strongly negative."
            )

        return {

            "positive_score":
                positive,

            "negative_score":
                negative,

            "net_score":
                round(
                    net,
                    2,
                ),

            "conclusion":
                conclusion,

        }

    # ========================================================
    # THESIS CHALLENGE SYNTHESIS
    # ========================================================

    @classmethod
    def thesis_synthesis(
        cls,
        analysis,
    ):

        thesis = analysis.get(
            "thesis_challenge",
            {}
        )

        if not isinstance(
            thesis,
            dict,
        ):

            thesis = {}

        result = (
            thesis.get(
                "overall_challenge_result"
            )
            or
            thesis.get(
                "result"
            )
            or
            "INSUFFICIENT_CHALLENGE"
        )

        challenge_count = cls.number(
            thesis.get(
                "challenge_count"
            )
        )

        material_negative = cls.number(
            thesis.get(
                "material_negative"
            )
        )

        if result == "THESIS_SURVIVES":

            conclusion = (
                "The investment thesis survives adversarial review."
            )

            score = 100

        elif result == "THESIS_REQUIRES_REVIEW":

            conclusion = (
                "The investment thesis contains unresolved weaknesses."
            )

            score = 60

        elif result == "THESIS_WEAKENED":

            conclusion = (
                "The investment thesis is materially weakened by adversarial review."
            )

            score = 20

        else:

            conclusion = (
                "The investment thesis has not yet been sufficiently challenged."
            )

            score = 50

        return {

            "result":
                result,

            "score":
                score,

            "challenge_count":
                challenge_count,

            "material_negative":
                material_negative,

            "conclusion":
                conclusion,

        }

    # ========================================================
    # DATA QUALITY SYNTHESIS
    # ========================================================

    @classmethod
    def market_signal_synthesis(
        cls,
        analysis,
    ):
        signals = analysis.get(
            "market_signals",
            {},
        )

        if not isinstance(signals, dict):
            signals = {}

        technical = signals.get("technical", {})
        risk = signals.get("risk", {})

        if not isinstance(technical, dict):
            technical = {}

        if not isinstance(risk, dict):
            risk = {}

        return {
            "technical_score": cls.number(technical.get("score"), None),
            "momentum_score": cls.number(technical.get("momentum_score"), None),
            "risk_score": cls.number(risk.get("score"), None),
            "beta": cls.number(risk.get("beta"), None),
            "annualised_volatility": cls.number(
                risk.get("annualised_volatility"),
                None,
            ),
        }

    @classmethod
    def sentiment_synthesis(
        cls,
        analysis,
    ):
        sentiment = analysis.get("sentiment", {})

        if not isinstance(sentiment, dict):
            sentiment = {}

        return {
            "score": cls.number(sentiment.get("score"), None),
            "label": sentiment.get("label") or "UNAVAILABLE",
            "confidence": sentiment.get("confidence") or "REVIEW",
            "independent_source_count": cls.number(
                sentiment.get("independent_source_count"),
                0,
            ),
        }

    @classmethod
    def market_context_synthesis(
        cls,
        analysis,
    ):
        market_regime = analysis.get("market_regime", {})
        macro_environment = analysis.get("macro_environment", {})

        if not isinstance(market_regime, dict):
            market_regime = {}
        if not isinstance(macro_environment, dict):
            macro_environment = {}

        return {
            "market_regime": {
                "status": market_regime.get("status") or "UNAVAILABLE",
                "regime": market_regime.get("regime") or "UNAVAILABLE",
                "score": cls.number(market_regime.get("score"), None),
            },
            "macro_environment": {
                "status": macro_environment.get("status") or "UNAVAILABLE",
                "regime": macro_environment.get("regime") or "UNAVAILABLE",
                "policy_rate": cls.number(macro_environment.get("policy_rate"), None),
                "inflation_yoy": cls.number(macro_environment.get("inflation_yoy"), None),
                "real_gdp_yoy": cls.number(macro_environment.get("real_gdp_yoy"), None),
            },
        }

    @classmethod
    def data_quality_synthesis(
        cls,
        analysis,
    ):

        confidence = (
            analysis.get(
                "confidence",
                {}
            ).get(
                "fundamental"
            )
        )

        if confidence is None:

            confidence = (
                analysis.get(
                    "validation",
                    {}
                ).get(
                    "overall_confidence"
                )
            )

        confidence_score = (
            cls.confidence_value(
                confidence
            )
        )

        quality = analysis.get(
            "data_quality",
            {}
        )

        unresolved = 0

        if isinstance(
            quality,
            dict,
        ):

            unresolved = cls.number(
                quality.get(
                    "unresolved_discrepancies"
                )
            )

        if unresolved > 0:

            confidence_score *= 0.75

            conclusion = (
                "Unresolved data discrepancies reduce confidence."
            )

        elif confidence_score >= 85:

            conclusion = (
                "Data quality is strong."
            )

        elif confidence_score >= 65:

            conclusion = (
                "Data quality is acceptable but requires monitoring."
            )

        else:

            conclusion = (
                "Data quality requires review."
            )

        return {

            "confidence":
                confidence,

            "score":
                round(
                    confidence_score,
                    2,
                ),

            "unresolved_discrepancies":
                unresolved,

            "conclusion":
                conclusion,

        }

    # ========================================================
    # BULL CASE
    # ========================================================

    @classmethod
    def build_bull_case(
        cls,
        analysis,
        fundamental,
        valuation,
        catalysts,
        thesis,
    ):

        points = []

        if fundamental[
            "score"
        ] >= 80:

            points.append(
                fundamental[
                    "conclusion"
                ]
            )

        if (
            valuation[
                "expected_return"
            ] is not None
            and valuation[
                "expected_return"
            ] >= 0.10
        ):

            points.append(
                valuation[
                    "conclusion"
                ]
            )

        if catalysts[
            "net_score"
        ] > 0:

            points.append(
                catalysts[
                    "conclusion"
                ]
            )

        if thesis[
            "result"
        ] == "THESIS_SURVIVES":

            points.append(
                "The core investment thesis survives adversarial testing."
            )

        if not points:

            points.append(
                "A credible bullish case has not yet been established."
            )

        return points

    # ========================================================
    # BEAR CASE
    # ========================================================

    @classmethod
    def build_bear_case(
        cls,
        analysis,
        fundamental,
        valuation,
        catalysts,
        thesis,
    ):

        points = []

        if (
            valuation[
                "expected_return"
            ] is not None
            and valuation[
                "expected_return"
            ] < 0
        ):

            points.append(
                valuation[
                    "conclusion"
                ]
            )

        if catalysts[
            "net_score"
        ] < 0:

            points.append(
                catalysts[
                    "conclusion"
                ]
            )

        if thesis[
            "result"
        ] == "THESIS_WEAKENED":

            points.append(
                thesis[
                    "conclusion"
                ]
            )

        if (
            fundamental[
                "score"
            ] < 70
        ):

            points.append(
                fundamental[
                    "conclusion"
                ]
            )

        if not points:

            points.append(
                "No dominant bearish factor has yet been identified."
            )

        return points

    # ========================================================
    # WHAT WOULD CHANGE OUR MIND?
    # ========================================================

    @classmethod
    def invalidation_conditions(
        cls,
        analysis,
        valuation,
        catalysts,
        thesis,
    ):

        conditions = []

        if (
            valuation[
                "expected_return"
            ] is not None
            and valuation[
                "expected_return"
            ] > 0
        ):

            conditions.append(
                "A material deterioration in expected returns or intrinsic value would weaken the investment case."
            )

        conditions.append(
            "A sustained deterioration in revenue growth or earnings expectations would weaken the thesis."
        )

        conditions.append(
            "New evidence contradicting the principal investment thesis would trigger a reassessment."
        )

        if catalysts[
            "negative_score"
        ] > 0:

            conditions.append(
                "A high-impact negative catalyst becoming more probable would require reassessment."
            )

        if thesis[
            "material_negative"
        ] > 0:

            conditions.append(
                "Additional material negative findings from adversarial research would weaken the thesis."
            )

        return conditions

    # ========================================================
    # OVERALL INVESTMENT CASE SCORE
    # ========================================================

    @classmethod
    def investment_case_score(
        cls,
        fundamental,
        valuation,
        catalysts,
        thesis,
        data_quality,
    ):

        score = (

            fundamental[
                "score"
            ] * 0.25

            + valuation[
                "score"
            ] * 0.25

            + cls.score(
                50
                + catalysts[
                    "net_score"
                ] * 5
            ) * 0.15

            + thesis[
                "score"
            ] * 0.20

            + data_quality[
                "score"
            ] * 0.15

        )

        if (
            thesis[
                "result"
            ] == "THESIS_WEAKENED"
        ):

            score *= 0.80

        if (
            data_quality[
                "unresolved_discrepancies"
            ] > 0
        ):

            score *= 0.90

        return round(
            max(
                0,
                min(
                    100,
                    score,
                ),
            ),
            2,
        )

    # ========================================================
    # FINAL INVESTMENT CONCLUSION
    # ========================================================

    @classmethod
    def final_decision(
        cls,
        score,
        valuation,
        thesis,
        data_quality,
    ):
        """Make the post-research recommendation after every specialist has reported."""

        expected_return = valuation.get(
            "expected_return"
        )

        if thesis.get("result") != "THESIS_SURVIVES":
            return {
                "decision": "WATCHLIST",
                "reason": (
                    "The thesis has not survived adversarial review, so the "
                    "earlier core recommendation cannot be used for a portfolio."
                ),
            }

        if data_quality.get("unresolved_discrepancies", 0) > 0:
            return {
                "decision": "WATCHLIST",
                "reason": (
                    "Unresolved source-data discrepancies require review before "
                    "a portfolio recommendation can be made."
                ),
            }

        if expected_return is None:
            return {
                "decision": "WATCHLIST",
                "reason": "Expected return is unavailable.",
            }

        if expected_return < -0.10:
            return {
                "decision": "AVOID",
                "reason": "Base valuation implies a materially negative expected return.",
            }

        if expected_return < 0.05:
            return {
                "decision": "WATCHLIST",
                "reason": "Expected return is below the portfolio entry threshold.",
            }

        if score >= 75 and expected_return >= 0.25:
            return {
                "decision": "STRONG_BUY",
                "reason": (
                    "Strong aggregate research score, attractive valuation, and "
                    "a surviving thesis support a high-conviction recommendation."
                ),
            }

        if score >= 65:
            return {
                "decision": "BUY",
                "reason": (
                    "The research case clears the portfolio entry threshold after "
                    "synthesis and adversarial review."
                ),
            }

        return {
            "decision": "WATCHLIST",
            "reason": (
                "The research case is not yet strong enough for a portfolio "
                "recommendation."
            ),
        }

    @classmethod
    def conclusion(
        cls,
        score,
        valuation,
        thesis,
    ):

        if (
            thesis[
                "result"
            ] == "THESIS_WEAKENED"
        ):

            return (
                "REQUIRES_REVIEW"
            )

        if (
            valuation[
                "expected_return"
            ] is not None
            and valuation[
                "expected_return"
            ] < -0.10
        ):

            return (
                "VALUATION_CONSTRAINED"
            )

        if score >= 85:

            return "STRONG_INVESTMENT_CASE"

        if score >= 75:

            return "ATTRACTIVE_INVESTMENT_CASE"

        if score >= 65:

            return "MODERATE_INVESTMENT_CASE"

        if score >= 50:

            return "WEAK_INVESTMENT_CASE"

        return "UNATTRACTIVE_INVESTMENT_CASE"

    # ========================================================
    # SYNTHESISE
    # ========================================================

    @classmethod
    def synthesise(
        cls,
        analysis,
    ):

        ticker = analysis.get(
            "ticker",
            "UNKNOWN",
        )

        fundamental = (
            cls.fundamental_synthesis(
                analysis
            )
        )

        valuation = (
            cls.valuation_synthesis(
                analysis
            )
        )

        catalysts = (
            cls.catalyst_synthesis(
                analysis
            )
        )

        thesis = (
            cls.thesis_synthesis(
                analysis
            )
        )

        data_quality = (
            cls.data_quality_synthesis(
                analysis
            )
        )

        market_signals = cls.market_signal_synthesis(
            analysis
        )

        sentiment = cls.sentiment_synthesis(
            analysis
        )

        market_context = cls.market_context_synthesis(
            analysis
        )

        overall_score = (
            cls.investment_case_score(
                fundamental,
                valuation,
                catalysts,
                thesis,
                data_quality,
            )
        )

        final_decision = cls.final_decision(
            overall_score,
            valuation,
            thesis,
            data_quality,
        )

        result = {

            "ticker":
                ticker,

            "status":
                "COMPLETE",

            "created_at":
                cls.now(),

            "investment_case_score":
                overall_score,

            "decision": final_decision["decision"],

            "decision_reason": final_decision["reason"],

            "conclusion":
                cls.conclusion(
                    overall_score,
                    valuation,
                    thesis,
                ),

            "fundamental":
                fundamental,

            "valuation":
                valuation,

            "catalysts":
                catalysts,

            "thesis_challenge":
                thesis,

            "data_quality":
                data_quality,

            "market_signals":
                market_signals,

            "sentiment":
                sentiment,

            "market_context":
                market_context,

            "bull_case":
                cls.build_bull_case(
                    analysis,
                    fundamental,
                    valuation,
                    catalysts,
                    thesis,
                ),

            "bear_case":
                cls.build_bear_case(
                    analysis,
                    fundamental,
                    valuation,
                    catalysts,
                    thesis,
                ),

            "what_would_change_our_mind":
                cls.invalidation_conditions(
                    analysis,
                    valuation,
                    catalysts,
                    thesis,
                ),

        }

        return result


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 80)
    print("RESEARCH SYNTHESIS ENGINE TEST")
    print("=" * 80)

    analysis = {

        "ticker":
            "NVDA",

        "scores": {

            "fundamental_quality":
                98,

            "valuation":
                30,

            "forward_expectations":
                90,

            "data_confidence":
                100,

        },

        "fundamentals": {

            "drivers": [
                "strong FCF margin",
                "high ROIC",
                "net cash position",
                "strong forward growth",
            ],

        },

        "valuation": {

            "expected_return":
                -0.1905,

            "status":
                "EXPENSIVE",

        },

        "catalysts": {

            "positive_score":
                6.75,

            "negative_score":
                7.50,

        },

        "thesis_challenge": {

            "overall_challenge_result":
                "THESIS_WEAKENED",

            "challenge_count":
                15,

            "material_negative":
                4,

        },

        "confidence": {

            "fundamental":
                "HIGH",

        },

        "data_quality": {

            "unresolved_discrepancies":
                0,

        },

    }

    result = (
        ResearchSynthesisEngine
        .synthesise(
            analysis
        )
    )

    print()
    print("INVESTMENT CASE SCORE:")
    print(
        result[
            "investment_case_score"
        ]
    )

    print()
    print("CONCLUSION:")
    print(
        result[
            "conclusion"
        ]
    )

    print()
    print("BULL CASE:")

    for item in result[
        "bull_case"
    ]:

        print(
            "-",
            item
        )

    print()
    print("BEAR CASE:")

    for item in result[
        "bear_case"
    ]:

        print(
            "-",
            item
        )

    print()
    print("WHAT WOULD CHANGE OUR MIND:")

    for item in result[
        "what_would_change_our_mind"
    ]:

        print(
            "-",
            item
        )

    print()
    print("=" * 80)
    print("RESEARCH SYNTHESIS ENGINE OK")
    print("=" * 80)
