from __future__ import annotations

import json
from datetime import datetime, timezone


class InvestmentDecisionEngine:

    def __init__(self):

        self.weights = {
            "fundamental_quality": 0.35,
            "valuation": 0.35,
            "forward_expectations": 0.20,
            "data_confidence": 0.10,
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
    # SAFE NUMBER
    # ========================================================

    @staticmethod
    def number(
        value,
        default=None,
    ):

        try:

            if value is None:
                return default

            value = float(value)

            if value != value:
                return default

            return value

        except (
            TypeError,
            ValueError,
        ):

            return default

    # ========================================================
    # CONFIDENCE SCORE
    # ========================================================

    def confidence_score(
        self,
        confidence,
    ):

        mapping = {
            "HIGH": 100,
            "MEDIUM": 65,
            "LOW": 30,
            "REVIEW": 40,
        }

        return mapping.get(
            str(
                confidence
            ).upper(),
            50,
        )

    # ========================================================
    # VALUATION SCORE
    # ========================================================

    def valuation_score(
        self,
        current_price,
        intrinsic_value,
    ):

        current_price = self.number(
            current_price
        )

        intrinsic_value = self.number(
            intrinsic_value
        )

        if (
            current_price is None
            or intrinsic_value is None
            or current_price <= 0
            or intrinsic_value <= 0
        ):

            return {
                "score": 50,
                "status": "INSUFFICIENT_DATA",
                "expected_return": None,
            }

        expected_return = (
            intrinsic_value
            / current_price
            - 1
        )

        if expected_return >= 0.50:

            score = 100
            status = "DEEPLY_UNDERVALUED"

        elif expected_return >= 0.25:

            score = 90
            status = "UNDERVALUED"

        elif expected_return >= 0.10:

            score = 75
            status = "ATTRACTIVE"

        elif expected_return >= 0:

            score = 60
            status = "FAIR_VALUE"

        elif expected_return >= -0.10:

            score = 45
            status = "SLIGHTLY_EXPENSIVE"

        elif expected_return >= -0.25:

            score = 30
            status = "EXPENSIVE"

        else:

            score = 15
            status = "DEEPLY_EXPENSIVE"

        return {
            "score": score,
            "status": status,
            "expected_return": expected_return,
        }

    # ========================================================
    # FORWARD EXPECTATIONS
    # ========================================================

    def forward_expectation_score(
        self,
        revenue_growth,
        eps_growth,
        forecast_confidence,
    ):

        revenue_growth = self.number(
            revenue_growth
        )

        eps_growth = self.number(
            eps_growth
        )

        scores = []

        if revenue_growth is not None:

            if revenue_growth >= 0.30:
                scores.append(100)

            elif revenue_growth >= 0.20:
                scores.append(85)

            elif revenue_growth >= 0.10:
                scores.append(70)

            elif revenue_growth >= 0:
                scores.append(55)

            else:
                scores.append(25)

        if eps_growth is not None:

            if eps_growth >= 0.30:
                scores.append(100)

            elif eps_growth >= 0.20:
                scores.append(85)

            elif eps_growth >= 0.10:
                scores.append(70)

            elif eps_growth >= 0:
                scores.append(55)

            else:
                scores.append(25)

        if not scores:

            return 50

        score = sum(scores) / len(scores)

        confidence_multiplier = {
            "HIGH": 1.00,
            "MEDIUM": 0.90,
            "LOW": 0.75,
            "REVIEW": 0.80,
        }.get(
            str(
                forecast_confidence
            ).upper(),
            0.85,
        )

        return score * confidence_multiplier

    # ========================================================
    # DECISION
    # ========================================================

    def decision(
        self,
        score,
    ):

        if score >= 80:

            return "STRONG_BUY"

        if score >= 70:

            return "BUY"

        if score >= 60:

            return "WATCHLIST"

        if score >= 45:

            return "HOLD"

        return "AVOID"

    # ========================================================
    # ANALYSE
    # ========================================================

    def analyse(
        self,
        fundamental_analysis,
        valuation_analysis,
    ):

        if not isinstance(
            fundamental_analysis,
            dict,
        ):

            return {
                "status": "FAILED",
                "reason": (
                    "Fundamental analysis unavailable."
                ),
            }

        if not isinstance(
            valuation_analysis,
            dict,
        ):

            return {
                "status": "FAILED",
                "reason": (
                    "Valuation analysis unavailable."
                ),
            }

        ticker = (
            fundamental_analysis
            .get(
                "ticker"
            )
            or valuation_analysis
            .get(
                "Ticker"
            )
        )

        fundamental_score = self.number(
            fundamental_analysis.get(
                "fundamental_score"
            ),
            50,
        )

        fundamental_confidence = (
            fundamental_analysis
            .get(
                "validation",
                {}
            )
            .get(
                "overall_confidence"
            )
        )

        forecast_validation = (
            fundamental_analysis
            .get(
                "forecast_validation",
                {}
            )
        )

        forecast_confidence = (
            forecast_validation
            .get(
                "overall_confidence"
            )
        )

        current_price = self.number(
            valuation_analysis.get(
                "Current Price"
            )
        )

        # ValuationEngine stores the base DCF value
        # under "Base Value".
        base_value = self.number(
            valuation_analysis.get(
                "Base Value"
            )
        )

        # Compatibility fallbacks for future/legacy
        # valuation engines.
        if base_value is None:

            base_value = self.number(
                valuation_analysis.get(
                    "Base Intrinsic Value"
                )
            )

        if base_value is None:

            base_value = self.number(
                valuation_analysis.get(
                    "Intrinsic Value Per Share"
                )
            )

        forward_revenue_growth = (
            self.number(
                fundamental_analysis
                .get(
                    "growth",
                    {}
                )
                .get(
                    "forward_revenue_growth"
                )
            )
        )

        forward_eps_growth = (
            self.number(
                fundamental_analysis
                .get(
                    "growth",
                    {}
                )
                .get(
                    "forward_eps_growth"
                )
            )
        )

        valuation = self.valuation_score(
            current_price,
            base_value,
        )

        forward_score = (
            self.forward_expectation_score(
                forward_revenue_growth,
                forward_eps_growth,
                forecast_confidence,
            )
        )

        data_confidence = (
            self.confidence_score(
                fundamental_confidence
            )
        )

        weighted_score = (

            fundamental_score
            * self.weights[
                "fundamental_quality"
            ]

            +

            valuation["score"]
            * self.weights[
                "valuation"
            ]

            +

            forward_score
            * self.weights[
                "forward_expectations"
            ]

            +

            data_confidence
            * self.weights[
                "data_confidence"
            ]

        )

        weighted_score = max(
            0,
            min(
                weighted_score,
                100,
            ),
        )

        decision = self.decision(
            weighted_score
        )

        # ----------------------------------------------------
        # VALUATION GUARDRAIL
        #
        # A company can have excellent fundamentals while
        # still being too expensive to buy.
        #
        # Therefore a materially negative DCF expected return
        # prevents the system from issuing BUY / STRONG_BUY.
        # ----------------------------------------------------

        expected_return = valuation.get(
            "expected_return"
        )

        if (
            expected_return is not None
            and expected_return <= -0.10
        ):

            if decision in (
                "BUY",
                "STRONG_BUY",
            ):

                decision = "WATCHLIST"

        elif (
            expected_return is not None
            and expected_return <= -0.25
        ):

            decision = "AVOID"

        result = {

            "ticker":
                ticker,

            "status":
                "COMPLETE",

            "analysed_at":
                self.now(),

            "scores": {

                "fundamental_quality":
                    fundamental_score,

                "valuation":
                    valuation["score"],

                "forward_expectations":
                    forward_score,

                "data_confidence":
                    data_confidence,

                "overall":
                    weighted_score,

            },

            "valuation": {

                "current_price":
                    current_price,

                "base_intrinsic_value":
                    base_value,

                "expected_return":
                    valuation[
                        "expected_return"
                    ],

                "status":
                    valuation[
                        "status"
                    ],

            },

            "expectations": {

                "forward_revenue_growth":
                    forward_revenue_growth,

                "forward_eps_growth":
                    forward_eps_growth,

                "forecast_confidence":
                    forecast_confidence,

            },

            "confidence": {

                "fundamental":
                    fundamental_confidence,

                "forecast":
                    forecast_confidence,

            },

            "decision":
                decision,

            "weights":
                self.weights,

        }

        return result


if __name__ == "__main__":

    from core.fundamental_analysis_engine import (
        FundamentalAnalysisEngine
    )

    from core.valuation_engine import (
        ValuationEngine
    )

    fundamentals = (
        FundamentalAnalysisEngine()
        .analyse("NVDA")
    )

    valuation = (
        ValuationEngine()
        .analyse("NVDA")
    )

    result = (
        InvestmentDecisionEngine()
        .analyse(
            fundamentals,
            valuation,
        )
    )

    print()
    print("=" * 80)
    print("INVESTMENT DECISION ENGINE")
    print("=" * 80)

    print(
        "Ticker:",
        result.get(
            "ticker"
        ),
    )

    print(
        "Overall score:",
        result.get(
            "scores",
            {}
        ).get(
            "overall"
        ),
    )

    print(
        "Fundamental score:",
        result.get(
            "scores",
            {}
        ).get(
            "fundamental_quality"
        ),
    )

    print(
        "Valuation score:",
        result.get(
            "scores",
            {}
        ).get(
            "valuation"
        ),
    )

    print(
        "Forward expectations:",
        result.get(
            "scores",
            {}
        ).get(
            "forward_expectations"
        ),
    )

    print(
        "Valuation status:",
        result.get(
            "valuation",
            {}
        ).get(
            "status"
        ),
    )

    print(
        "Expected return:",
        result.get(
            "valuation",
            {}
        ).get(
            "expected_return"
        ),
    )

    print(
        "DECISION:",
        result.get(
            "decision"
        ),
    )

    print()
    print(
        json.dumps(
            result,
            indent=2,
            default=str,
        )
    )
