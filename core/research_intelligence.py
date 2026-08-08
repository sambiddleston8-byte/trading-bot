import json
import os
from datetime import datetime, timezone


class ResearchIntelligence:

    def __init__(
        self,
        output_directory="data/research/intelligence",
    ):

        self.output_directory = (
            output_directory
        )

        os.makedirs(
            self.output_directory,
            exist_ok=True,
        )

        # ========================================================
        # RETURN HORIZONS
        # ========================================================

        self.horizons = {

            "Short Term": {
                "days": 90,
            },

            "Medium Term": {
                "days": 252,
            },

            "Long Term": {
                "days": 756,
            },

        }

    # ============================================================
    # TIME
    # ============================================================

    def utc_now(
        self,
    ):

        return datetime.now(
            timezone.utc
        ).isoformat()

    # ============================================================
    # SAFE FLOAT
    # ============================================================

    def safe_float(
        self,
        value,
        default=None,
    ):

        try:

            if value is None:

                return default

            value = float(
                value
            )

            return value

        except (
            TypeError,
            ValueError,
        ):

            return default

    # ============================================================
    # CLAMP
    # ============================================================

    def clamp(
        self,
        value,
        minimum=0,
        maximum=100,
    ):

        return max(
            minimum,
            min(
                maximum,
                value,
            ),
        )

    # ============================================================
    # LOAD RESEARCH
    # ============================================================

    def load_research(
        self,
        symbol,
        path=None,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        if path is None:

            path = os.path.join(
                "data",
                "research",
                f"{symbol}.json",
            )

        if not os.path.exists(
            path
        ):

            return None

        try:

            with open(
                path,
                "r",
            ) as file:

                return json.load(
                    file
                )

        except Exception as error:

            print(
                f"Could not load research: "
                f"{error}"
            )

            return None

    # ============================================================
    # SAVE
    # ============================================================

    def save(
        self,
        symbol,
        data,
    ):

        path = os.path.join(
            self.output_directory,
            f"{symbol}.json",
        )

        with open(
            path,
            "w",
        ) as file:

            json.dump(
                data,
                file,
                indent=2,
                default=str,
            )

        return path

    # ============================================================
    # FACTOR CONVICTION
    # ============================================================

    def factor_conviction(
        self,
        research,
    ):

        fundamental = (
            research.get(
                "Fundamental Research",
                {},
            )
        )

        score = self.safe_float(
            fundamental.get(
                "Overall Score"
            ),
            50,
        )

        strengths = len(
            fundamental.get(
                "Strengths",
                [],
            )
        )

        weaknesses = len(
            fundamental.get(
                "Weaknesses",
                [],
            )
        )

        conviction = score

        conviction += (
            min(
                strengths,
                5,
            )
            * 1.0
        )

        conviction -= (
            min(
                weaknesses,
                5,
            )
            * 1.5
        )

        return self.clamp(
            conviction
        )

    # ============================================================
    # VALUATION CONVICTION
    # ============================================================

    def valuation_conviction(
        self,
        research,
    ):

        valuation = (
            research.get(
                "Valuation Research",
                {},
            )
        )

        current_price = (
            self.safe_float(
                valuation.get(
                    "Current Price"
                )
            )
        )

        intrinsic_value = (
            self.safe_float(
                valuation.get(
                    "Intrinsic Value"
                )
            )
        )

        if (
            current_price is None
            or intrinsic_value is None
            or current_price <= 0
        ):

            return {

                "Score":
                    50,

                "Upside":
                    None,

                "Status":
                    "INSUFFICIENT DATA",

            }

        upside = (
            intrinsic_value
            / current_price
        ) - 1

        # --------------------------------------------------------
        # Valuation score.
        #
        # This is deliberately a bounded signal. We do not want
        # a single extreme DCF assumption to overwhelm the entire
        # investment case.
        # --------------------------------------------------------

        score = (
            50
            + (
                upside
                * 100
            )
        )

        score = self.clamp(
            score,
            0,
            100,
        )

        return {

            "Score":
                round(
                    score,
                    2,
                ),

            "Upside":
                round(
                    upside * 100,
                    2,
                ),

            "Current Price":
                current_price,

            "Intrinsic Value":
                intrinsic_value,

            "Status":
                "AVAILABLE",

        }

    # ============================================================
    # CATALYST CONVICTION
    # ============================================================

    def catalyst_conviction(
        self,
        research,
    ):

        catalysts = (
            research.get(
                "Catalyst Research",
                {},
            )
        )

        score = self.safe_float(
            catalysts.get(
                "Catalyst Score"
            ),
            50,
        )

        upcoming = catalysts.get(
            "Upcoming Catalysts",
            [],
        )

        high_impact = 0

        for catalyst in upcoming:

            if (
                str(
                    catalyst.get(
                        "Impact",
                        "",
                    )
                ).upper()
                == "HIGH"
            ):

                high_impact += 1

        score += (
            min(
                high_impact,
                3,
            )
            * 3
        )

        score = self.clamp(
            score
        )

        return {

            "Score":
                round(
                    score,
                    2,
                ),

            "Upcoming Catalysts":
                len(
                    upcoming
                ),

            "High Impact Catalysts":
                high_impact,

        }

    # ============================================================
    # EXPECTED RETURN
    # ============================================================

    def return_analysis(
        self,
        research,
    ):

        returns = (
            research.get(
                "Expected Returns",
                {},
            )
        )

        factor_model = (
            returns.get(
                "Factor Model",
                {},
            )
        )

        intrinsic_model = (
            returns.get(
                "Intrinsic Value Model",
                {},
            )
        )

        factor_return = (
            self.safe_float(
                factor_model.get(
                    "Expected Return"
                )
            )
        )

        factor_horizon = (
            factor_model.get(
                "Horizon Days"
            )
        )

        factor_confidence = (
            self.safe_float(
                factor_model.get(
                    "Confidence"
                )
            )
        )

        dcf_return = (
            self.safe_float(
                intrinsic_model.get(
                    "Expected Return"
                )
            )
        )

        dcf_annualised = (
            self.safe_float(
                intrinsic_model.get(
                    "Annualised Return"
                )
            )
        )

        dcf_horizon = (
            intrinsic_model.get(
                "Horizon Years"
            )
        )

        # --------------------------------------------------------
        # Never merge returns with incompatible horizons.
        # --------------------------------------------------------

        return {

            "Factor Model": {

                "Expected Return":
                    factor_return,

                "Horizon Days":
                    factor_horizon,

                "Confidence":
                    factor_confidence,

            },

            "Intrinsic Value Model": {

                "Expected Return":
                    dcf_return,

                "Annualised Return":
                    dcf_annualised,

                "Horizon Years":
                    dcf_horizon,

            },

        }

    # ============================================================
    # RISK ANALYSIS
    # ============================================================

    def risk_analysis(
        self,
        research,
    ):

        factors = (
            research.get(
                "Fundamental Research",
                {},
            ).get(
                "Factor Scores",
                {},
            )
        )

        risk_score = self.safe_float(
            factors.get(
                "Risk"
            ),
            50,
        )

        balance_sheet = self.safe_float(
            factors.get(
                "Balance Sheet"
            ),
            50,
        )

        valuation = (
            research.get(
                "Valuation Research",
                {},
            )
        )

        current_price = (
            self.safe_float(
                valuation.get(
                    "Current Price"
                )
            )
        )

        bear_value = (
            self.safe_float(
                valuation.get(
                    "Bear Value"
                )
            )
        )

        valuation_risk = 50

        if (
            current_price is not None
            and bear_value is not None
            and current_price > 0
        ):

            downside = (
                bear_value
                / current_price
            ) - 1

            if downside < -0.40:

                valuation_risk = 20

            elif downside < -0.20:

                valuation_risk = 35

            elif downside < 0:

                valuation_risk = 50

            else:

                valuation_risk = 70

        return {

            "Risk Factor Score":
                risk_score,

            "Balance Sheet Score":
                balance_sheet,

            "Bear Case Valuation Score":
                valuation_risk,

            "Overall Risk Signal":
                round(
                    (
                        risk_score * 0.45
                        +
                        balance_sheet * 0.30
                        +
                        valuation_risk * 0.25
                    ),
                    2,
                ),

        }

    # ============================================================
    # EVIDENCE QUALITY
    # ============================================================

    def evidence_quality(
        self,
        research,
    ):

        raw = research.get(
            "Raw Research",
            {},
        )

        available = 0
        total = 3

        if raw.get(
            "Multi Factor"
        ):

            available += 1

        if raw.get(
            "Valuation"
        ):

            available += 1

        if raw.get(
            "Catalysts"
        ):

            available += 1

        score = (
            available
            / total
            * 100
        )

        return {

            "Available Research Modules":
                available,

            "Expected Research Modules":
                total,

            "Evidence Coverage":
                round(
                    score,
                    2,
                ),

        }

    # ============================================================
    # CONVICTION MODEL
    # ============================================================

    def conviction_model(
        self,
        factor_conviction,
        valuation_conviction,
        catalyst_conviction,
        risk_analysis,
        evidence_quality,
    ):

        factor_score = (
            self.safe_float(
                factor_conviction,
                50,
            )
        )

        valuation_score = (
            self.safe_float(
                valuation_conviction.get(
                    "Score"
                ),
                50,
            )
        )

        catalyst_score = (
            self.safe_float(
                catalyst_conviction.get(
                    "Score"
                ),
                50,
            )
        )

        risk_score = (
            self.safe_float(
                risk_analysis.get(
                    "Overall Risk Signal"
                ),
                50,
            )
        )

        evidence_score = (
            self.safe_float(
                evidence_quality.get(
                    "Evidence Coverage"
                ),
                0,
            )
        )

        # --------------------------------------------------------
        # Core conviction.
        #
        # Fundamentals receive the largest influence.
        # Valuation is next.
        # Catalysts influence timing rather than becoming the
        # dominant component of the investment thesis.
        # --------------------------------------------------------

        raw_score = (

            factor_score
            * 0.40

            +

            valuation_score
            * 0.25

            +

            catalyst_score
            * 0.15

            +

            risk_score
            * 0.20

        )

        # --------------------------------------------------------
        # Evidence penalty.
        #
        # If one of the research engines failed, conviction is
        # reduced rather than pretending the missing evidence
        # doesn't matter.
        # --------------------------------------------------------

        evidence_factor = (
            0.75
            +
            (
                evidence_score
                / 100
                * 0.25
            )
        )

        conviction = (
            raw_score
            * evidence_factor
        )

        return round(
            self.clamp(
                conviction
            ),
            2,
        )

    # ============================================================
    # RATING
    # ============================================================

    def rating(
        self,
        conviction,
        expected_return,
        expected_return_confidence,
        valuation_upside,
    ):

        score = (
            self.safe_float(
                conviction,
                50,
            )
        )

        expected_return = (
            self.safe_float(
                expected_return
            )
        )

        expected_return_confidence = (
            self.safe_float(
                expected_return_confidence
            )
        )

        valuation_upside = (
            self.safe_float(
                valuation_upside
            )
        )

        # --------------------------------------------------------
        # Strong expected returns increase conviction, but only
        # when the forecast has meaningful confidence.
        # --------------------------------------------------------

        if (
            expected_return is not None
            and expected_return_confidence
            is not None
            and expected_return_confidence
            >= 70
        ):

            if expected_return >= 30:

                score += 7

            elif expected_return >= 20:

                score += 4

            elif expected_return >= 10:

                score += 2

            elif expected_return < 0:

                score -= 5

        # --------------------------------------------------------
        # Intrinsic value confirmation.
        # --------------------------------------------------------

        if valuation_upside is not None:

            if valuation_upside >= 30:

                score += 5

            elif valuation_upside >= 15:

                score += 3

            elif valuation_upside < -20:

                score -= 5

        score = self.clamp(
            score
        )

        if score >= 85:

            rating = (
                "STRONG BUY"
            )

        elif score >= 75:

            rating = "BUY"

        elif score >= 65:

            rating = (
                "WATCH"
            )

        elif score >= 50:

            rating = (
                "LOW CONVICTION"
            )

        else:

            rating = (
                "AVOID"
            )

        return {

            "Score":
                round(
                    score,
                    2,
                ),

            "Rating":
                rating,

        }

    # ============================================================
    # INVESTMENT CASE
    # ============================================================

    def build_investment_case(
        self,
        research,
        factor_conviction,
        valuation_conviction,
        catalyst_conviction,
        risk_analysis,
        return_analysis,
    ):

        strengths = []

        concerns = []

        # --------------------------------------------------------
        # Fundamental strengths
        # --------------------------------------------------------

        fundamental = (
            research.get(
                "Fundamental Research",
                {},
            )
        )

        for item in (
            fundamental.get(
                "Strengths",
                [],
            )
        ):

            strengths.append({

                "Category":
                    "Fundamentals",

                "Factor":
                    item.get(
                        "Factor"
                    ),

                "Score":
                    item.get(
                        "Score"
                    ),

                "Explanation":
                    (
                        f"{item.get('Factor')} "
                        f"is a strong part of "
                        f"the investment case."
                    ),

            })

        # --------------------------------------------------------
        # Valuation
        # --------------------------------------------------------

        upside = (
            valuation_conviction.get(
                "Upside"
            )
        )

        if upside is not None:

            if upside >= 20:

                strengths.append({

                    "Category":
                        "Valuation",

                    "Factor":
                        "Intrinsic Value",

                    "Score":
                        valuation_conviction.get(
                            "Score"
                        ),

                    "Explanation":
                        (
                            f"The base intrinsic "
                            f"value indicates "
                            f"approximately "
                            f"{upside:.1f}% upside "
                            f"to the current price."
                        ),

                })

            elif upside < 0:

                concerns.append({

                    "Category":
                        "Valuation",

                    "Factor":
                        "Intrinsic Value",

                    "Score":
                        valuation_conviction.get(
                            "Score"
                        ),

                    "Explanation":
                        (
                            f"The base intrinsic "
                            f"value is approximately "
                            f"{abs(upside):.1f}% below "
                            f"the current price."
                        ),

                })

        # --------------------------------------------------------
        # Catalysts
        # --------------------------------------------------------

        if (
            catalyst_conviction.get(
                "Score",
                50,
            )
            >= 75
        ):

            strengths.append({

                "Category":
                    "Catalysts",

                "Factor":
                    "Catalyst Setup",

                "Score":
                    catalyst_conviction.get(
                        "Score"
                    ),

                "Explanation":
                    (
                        "The catalyst engine "
                        "identifies a favourable "
                        "upcoming catalyst setup."
                    ),

            })

        # --------------------------------------------------------
        # Risk
        # --------------------------------------------------------

        overall_risk = (
            risk_analysis.get(
                "Overall Risk Signal",
                50,
            )
        )

        if overall_risk < 40:

            concerns.append({

                "Category":
                    "Risk",

                "Factor":
                    "Overall Risk",

                "Score":
                    overall_risk,

                "Explanation":
                    (
                        "Risk indicators are "
                        "materially below the "
                        "system's preferred range."
                    ),

            })

        # --------------------------------------------------------
        # Returns
        # --------------------------------------------------------

        factor_model = (
            return_analysis.get(
                "Factor Model",
                {},
            )
        )

        factor_return = (
            factor_model.get(
                "Expected Return"
            )
        )

        factor_horizon = (
            factor_model.get(
                "Horizon Days"
            )
        )

        if (
            factor_return is not None
            and factor_return >= 20
        ):

            strengths.append({

                "Category":
                    "Expected Return",

                "Factor":
                    "Factor Model",

                "Score":
                    None,

                "Explanation":
                    (
                        f"The factor model "
                        f"forecasts approximately "
                        f"{factor_return:.1f}% total return "
                        f"over {factor_horizon} days."
                    ),

            })

        # --------------------------------------------------------
        # Overall narrative
        # --------------------------------------------------------

        if (
            len(strengths) >= 4
            and len(concerns) <= 1
        ):

            narrative = (
                "The investment case is supported "
                "by several independent research "
                "signals, with limited material "
                "contradictory evidence."
            )

        elif len(concerns) >= 3:

            narrative = (
                "The investment case contains "
                "multiple material risks or "
                "contradictory signals and requires "
                "a higher level of conviction before "
                "capital should be committed."
            )

        else:

            narrative = (
                "The investment case contains "
                "both positive and negative signals. "
                "The strongest supporting evidence "
                "should be weighed against the "
                "identified areas of uncertainty."
            )

        return {

            "Narrative":
                narrative,

            "Strengths":
                strengths,

            "Concerns":
                concerns,

        }

    # ============================================================
    # BUILD FULL INTELLIGENCE REPORT
    # ============================================================

    def analyse(
        self,
        symbol,
        research=None,
        path=None,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        if research is None:

            research = (
                self.load_research(
                    symbol,
                    path=path,
                )
            )

        if not research:

            return {

                "Ticker":
                    symbol,

                "Status":
                    "FAILED",

                "Reason":
                    "Research dossier could not be loaded.",

            }

        # ========================================================
        # COMPONENT ANALYSIS
        # ========================================================

        factor_conviction = (
            self.factor_conviction(
                research
            )
        )

        valuation_conviction = (
            self.valuation_conviction(
                research
            )
        )

        catalyst_conviction = (
            self.catalyst_conviction(
                research
            )
        )

        risk_analysis = (
            self.risk_analysis(
                research
            )
        )

        evidence_quality = (
            self.evidence_quality(
                research
            )
        )

        return_analysis = (
            self.return_analysis(
                research
            )
        )

        # ========================================================
        # PRIMARY EXPECTED RETURN
        # ========================================================

        factor_model = (
            return_analysis.get(
                "Factor Model",
                {},
            )
        )

        expected_return = (
            self.safe_float(
                factor_model.get(
                    "Expected Return"
                )
            )
        )

        expected_return_confidence = (
            self.safe_float(
                factor_model.get(
                    "Confidence"
                )
            )
        )

        valuation_upside = (
            self.safe_float(
                valuation_conviction.get(
                    "Upside"
                )
            )
        )

        # ========================================================
        # CONVICTION
        # ========================================================

        conviction = (
            self.conviction_model(
                factor_conviction,
                valuation_conviction,
                catalyst_conviction,
                risk_analysis,
                evidence_quality,
            )
        )

        # ========================================================
        # FINAL RATING
        # ========================================================

        rating = (
            self.rating(
                conviction,
                expected_return,
                expected_return_confidence,
                valuation_upside,
            )
        )

        # ========================================================
        # INVESTMENT CASE
        # ========================================================

        investment_case = (
            self.build_investment_case(
                research,
                factor_conviction,
                valuation_conviction,
                catalyst_conviction,
                risk_analysis,
                return_analysis,
            )
        )

        # ========================================================
        # FINAL REPORT
        # ========================================================

        result = {

            "Ticker":
                symbol,

            "Company":
                research.get(
                    "Company"
                ),

            "Sector":
                research.get(
                    "Sector"
                ),

            "Industry":
                research.get(
                    "Industry"
                ),

            "Status":
                "COMPLETE",

            "Generated At":
                self.utc_now(),

            # ----------------------------------------------------
            # PRIMARY DECISION
            # ----------------------------------------------------

            "Investment Rating": {

                "Score":
                    rating[
                        "Score"
                    ],

                "Rating":
                    rating[
                        "Rating"
                    ],

            },

            # ----------------------------------------------------
            # CONVICTION COMPONENTS
            # ----------------------------------------------------

            "Conviction": {

                "Overall":
                    conviction,

                "Fundamentals":
                    round(
                        factor_conviction,
                        2,
                    ),

                "Valuation":
                    valuation_conviction,

                "Catalysts":
                    catalyst_conviction,

                "Risk":
                    risk_analysis,

                "Evidence":
                    evidence_quality,

            },

            # ----------------------------------------------------
            # RETURNS
            # ----------------------------------------------------

            "Expected Returns":
                return_analysis,

            # ----------------------------------------------------
            # INVESTMENT CASE
            # ----------------------------------------------------

            "Investment Case":
                investment_case,

            # ----------------------------------------------------
            # ORIGINAL RESEARCH
            # ----------------------------------------------------

            "Research Dossier":
                research,

        }

        # ========================================================
        # SAVE
        # ========================================================

        output_path = (
            self.save(
                symbol,
                result,
            )
        )

        result[
            "Output Path"
        ] = output_path

        # ========================================================
        # PRINT
        # ========================================================

        print()
        print("=" * 80)
        print(
            f"RESEARCH INTELLIGENCE — {symbol}"
        )
        print("=" * 80)

        print()

        print(
            f"Rating: "
            f"{rating['Rating']}"
        )

        print(
            f"Score: "
            f"{rating['Score']}"
        )

        print(
            f"Fundamentals: "
            f"{factor_conviction:.2f}"
        )

        print(
            f"Valuation: "
            f"{valuation_conviction.get('Score')}"
        )

        print(
            f"Catalysts: "
            f"{catalyst_conviction.get('Score')}"
        )

        print(
            f"Risk Signal: "
            f"{risk_analysis.get('Overall Risk Signal')}"
        )

        print()

        if expected_return is not None:

            print(
                f"Expected Return: "
                f"{expected_return:.2f}%"
            )

            print(
                f"Expected Return Horizon: "
                f"{factor_model.get('Horizon Days')} days"
            )

            if (
                expected_return_confidence
                is not None
            ):

                print(
                    f"Forecast Confidence: "
                    f"{expected_return_confidence:.1f}%"
                )

        print()

        if valuation_upside is not None:

            print(
                f"DCF / Intrinsic Value Upside: "
                f"{valuation_upside:.2f}%"
            )

        print()

        print(
            investment_case[
                "Narrative"
            ]
        )

        print()

        print(
            f"Saved to: "
            f"{output_path}"
        )

        return result


if __name__ == "__main__":

    engine = (
        ResearchIntelligence()
    )

    engine.analyse(
        "NVDA"
    )