from core.expected_return_engine import ExpectedReturnEngine


class InvestmentCommittee:

    def __init__(self):

        self.expected_return_engine = (
            ExpectedReturnEngine()
        )

        # Expected return is deliberately a
        # supplementary component rather than
        # replacing the fundamental factor model.

        self.factor_weight = 0.75
        self.return_weight = 0.25

    # ============================================================
    # SAFE NUMBER
    # ============================================================

    def safe_float(
        self,
        value,
        default=50,
    ):

        try:

            return float(
                value
            )

        except (
            TypeError,
            ValueError,
        ):

            return default

    # ============================================================
    # EXPECTED RETURN SCORE
    # ============================================================

    def expected_return_score(
        self,
        expected_return,
    ):

        expected_return = (
            self.safe_float(
                expected_return,
                0,
            )
        )

        # --------------------------------------------------------
        # Convert expected return into a 0-100 conviction score.
        #
        # 0% expected return = 50
        # +20% expected return = 70
        # +50% expected return = 100
        # -20% expected return = 30
        #
        # This is a bounded supplementary signal.
        # --------------------------------------------------------

        score = (
            50
            + expected_return
        )

        return max(
            0,
            min(
                100,
                score,
            ),
        )

    # ============================================================
    # REVIEW
    # ============================================================

    def review(
        self,
        analysis,
        specialist_performance=None,
    ):

        if specialist_performance is None:

            specialist_performance = {}

        # --------------------------------------------------------
        # Get factor scores
        # --------------------------------------------------------

        factor_scores = (
            analysis.get(
                "Factor Scores",
                {},
            )
        )

        factors = {

            "Business Quality":
                self.safe_float(
                    factor_scores.get(
                        "Business Quality",
                        50,
                    )
                ),

            "Financial Strength":
                self.safe_float(
                    factor_scores.get(
                        "Financial Strength",
                        50,
                    )
                ),

            "Valuation":
                self.safe_float(
                    factor_scores.get(
                        "Valuation",
                        50,
                    )
                ),

            "Growth":
                self.safe_float(
                    factor_scores.get(
                        "Growth",
                        50,
                    )
                ),

            "Profitability":
                self.safe_float(
                    factor_scores.get(
                        "Profitability",
                        50,
                    )
                ),

            "Momentum":
                self.safe_float(
                    factor_scores.get(
                        "Momentum",
                        50,
                    )
                ),

            "Risk":
                self.safe_float(
                    factor_scores.get(
                        "Risk",
                        50,
                    )
                ),

            "Size":
                self.safe_float(
                    factor_scores.get(
                        "Size",
                        50,
                    )
                ),

            "Balance Sheet":
                self.safe_float(
                    factor_scores.get(
                        "Balance Sheet",
                        50,
                    )
                ),

            "Dividend":
                self.safe_float(
                    factor_scores.get(
                        "Dividend",
                        50,
                    )
                ),

        }

        # --------------------------------------------------------
        # Use the weights produced by the Multi-Factor Engine.
        #
        # This means the Committee automatically receives the
        # latest learned weights.
        # --------------------------------------------------------

        weights = (
            analysis.get(
                "Weights",
                {},
            )
        )

        if not weights:

            weights = {

                "Business Quality": 0.15,
                "Financial Strength": 0.15,
                "Valuation": 0.10,
                "Growth": 0.15,
                "Profitability": 0.15,
                "Momentum": 0.15,
                "Risk": 0.10,
                "Size": 0.025,
                "Balance Sheet": 0.075,
                "Dividend": 0.00,

            }

        # --------------------------------------------------------
        # Normalise supplied weights.
        # --------------------------------------------------------

        total_weight = sum(
            weights.get(
                factor,
                0,
            )

            for factor
            in factors
        )

        if total_weight > 0:

            weights = {

                factor:
                    (
                        weights.get(
                            factor,
                            0,
                        )
                        / total_weight
                    )

                for factor
                in factors

            }

        # --------------------------------------------------------
        # Factor score
        # --------------------------------------------------------

        factor_score = 0.0

        for factor, score in (
            factors.items()
        ):

            weight = weights.get(
                factor,
                0,
            )

            factor_score += (
                score
                * weight
            )

        factor_score = round(
            factor_score,
            2,
        )

        # ========================================================
        # EXPECTED RETURN
        # ========================================================

        expected_return_data = (
            analysis.get(
                "Expected Return Analysis"
            )
        )

        # --------------------------------------------------------
        # If the pipeline has already calculated the forecast,
        # use it.
        # --------------------------------------------------------

        if expected_return_data:

            expected_return = (
                self.safe_float(
                    expected_return_data.get(
                        "Expected Return",
                        0,
                    ),
                    0,
                )
            )

            forecast_confidence = (
                self.safe_float(
                    expected_return_data.get(
                        "Confidence",
                        50,
                    ),
                    50,
                )
            )

        else:

            # ----------------------------------------------------
            # Otherwise use an explicitly supplied forecast.
            # ----------------------------------------------------

            expected_return = (
                self.safe_float(
                    analysis.get(
                        "Expected Return",
                        0,
                    ),
                    0,
                )
            )

            forecast_confidence = (
                self.safe_float(
                    analysis.get(
                        "Expected Return Confidence",
                        50,
                    ),
                    50,
                )
            )

        return_score = (
            self.expected_return_score(
                expected_return
            )
        )

        # ========================================================
        # COMBINED COMMITTEE SCORE
        # ========================================================

        committee_score = (

            factor_score
            * self.factor_weight

            +

            return_score
            * self.return_weight

        )

        committee_score = round(
            committee_score,
            2,
        )

        # ========================================================
        # STRENGTHS
        # ========================================================

        strengths = []

        for factor, score in (
            factors.items()
        ):

            if score >= 80:

                strengths.append(
                    f"{factor} is very strong."
                )

            elif score >= 70:

                strengths.append(
                    f"{factor} is supportive."
                )

        if expected_return >= 20:

            strengths.append(
                f"Expected return is strongly positive at "
                f"{expected_return:.1f}%."
            )

        elif expected_return >= 10:

            strengths.append(
                f"Expected return is positive at "
                f"{expected_return:.1f}%."
            )

        # ========================================================
        # CONCERNS
        # ========================================================

        concerns = []

        for factor, score in (
            factors.items()
        ):

            if score < 40:

                concerns.append(
                    f"{factor} is weak."
                )

            elif score < 50:

                concerns.append(
                    f"{factor} requires caution."
                )

        if expected_return < 0:

            concerns.append(
                f"Expected return is negative at "
                f"{expected_return:.1f}%."
            )

        elif expected_return < 5:

            concerns.append(
                f"Expected return is limited at "
                f"{expected_return:.1f}%."
            )

        if forecast_confidence < 50:

            concerns.append(
                "Expected-return forecast confidence is low."
            )

        # ========================================================
        # VALUATION OVERRIDE
        # ========================================================

        if (
            factors["Business Quality"]
            >= 80

            and factors["Financial Strength"]
            >= 80

            and factors["Profitability"]
            >= 80

            and factors["Valuation"]
            < 40
        ):

            committee_score = min(
                committee_score,
                69,
            )

            concerns.append(
                "Exceptional fundamentals are offset by "
                "an unattractive valuation."
            )

        # ========================================================
        # RISK OVERRIDE
        # ========================================================

        if factors["Risk"] < 30:

            committee_score = min(
                committee_score,
                59,
            )

            concerns.append(
                "Very high risk materially limits "
                "investment conviction."
            )

        # ========================================================
        # RECOMMENDATION
        # ========================================================

        if committee_score >= 85:

            recommendation = (
                "STRONG BUY"
            )

        elif committee_score >= 75:

            recommendation = "BUY"

        elif committee_score >= 65:

            recommendation = (
                "HOLD / WATCH"
            )

        elif committee_score >= 50:

            recommendation = (
                "REDUCE / AVOID"
            )

        else:

            recommendation = (
                "SELL / AVOID"
            )

        # ========================================================
        # CONFIDENCE
        # ========================================================

        scores = list(
            factors.values()
        )

        spread = (
            max(scores)
            - min(scores)
        )

        factor_confidence = (
            90
            - (
                spread
                * 0.30
            )
        )

        factor_confidence = max(
            50,
            min(
                factor_confidence,
                95,
            ),
        )

        # Blend factor consistency with
        # forecast confidence.

        confidence = (

            factor_confidence
            * 0.70

            +

            forecast_confidence
            * 0.30

        )

        confidence = max(
            50,
            min(
                confidence,
                95,
            ),
        )

        confidence = round(
            confidence,
            1,
        )

        # ========================================================
        # SUMMARY
        # ========================================================

        summary = (

            f"The Investment Committee assigns "
            f"{committee_score}/100 and recommends "
            f"{recommendation}. "
            f"Expected return is "
            f"{expected_return:.1f}% over "
            f"{self.expected_return_engine.horizon_days} "
            f"days. "
            f"Confidence is {confidence}%."

        )

        # ========================================================
        # RETURN
        # ========================================================

        return {

            "Committee Score":
                committee_score,

            "Factor Score":
                factor_score,

            "Expected Return Score":
                round(
                    return_score,
                    2,
                ),

            "Expected Return":
                expected_return,

            "Expected Return Confidence":
                forecast_confidence,

            "Expected Return Analysis":
                expected_return_data,

            "Recommendation":
                recommendation,

            "Confidence":
                confidence,

            "Adaptive Weights":
                weights,

            "Factor Scores":
                factors,

            "Specialist Scores":
                factors,

            "Learning Enabled":
                bool(
                    specialist_performance
                ),

            "Strengths":
                strengths,

            "Concerns":
                concerns,

            "Summary":
                summary,

        }