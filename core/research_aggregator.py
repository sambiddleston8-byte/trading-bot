import json
import os
from datetime import datetime, timezone


from core.multi_factor_engine import (
    MultiFactorEngine,
)

from core.valuation_engine import (
    ValuationEngine,
)

from core.catalyst_engine import (
    CatalystEngine,
)

from core.investment_committee import (
    InvestmentCommittee,
)


class ResearchAggregator:

    def __init__(
        self,
        output_directory="data/research",
    ):

        self.output_directory = (
            output_directory
        )

        os.makedirs(
            self.output_directory,
            exist_ok=True,
        )

        # ========================================================
        # RESEARCH ENGINES
        # ========================================================

        self.factor_engine = (
            MultiFactorEngine()
        )

        self.valuation_engine = (
            ValuationEngine()
        )

        self.catalyst_engine = (
            CatalystEngine()
        )

        self.committee = (
            InvestmentCommittee()
        )

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

            return float(
                value
            )

        except (
            TypeError,
            ValueError,
        ):

            return default

    # ============================================================
    # LOAD / SAVE
    # ============================================================

    def save_json(
        self,
        path,
        data,
    ):

        directory = (
            os.path.dirname(
                path
            )
        )

        if directory:

            os.makedirs(
                directory,
                exist_ok=True,
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
    # RESEARCH HEADER
    # ============================================================

    def build_header(
        self,
        symbol,
        factor_result,
        valuation_result,
        catalyst_result,
    ):

        return {

            "Ticker":
                symbol,

            "Company":
                factor_result.get(
                    "Company"
                ),

            "Sector":
                factor_result.get(
                    "Sector"
                ),

            "Industry":
                factor_result.get(
                    "Industry"
                ),

            "Research Timestamp":
                self.utc_now(),

            "Research Status":
                "COMPLETE",

            "Engines Used": [

                "Multi-Factor Analysis",

                "Intrinsic Value / DCF",

                "Catalyst Analysis",

            ],

            "Data Sources": [

                "Yahoo Finance",

                "SEC EDGAR",

                "Google News",

            ],

        }

    # ============================================================
    # FACTOR SUMMARY
    # ============================================================

    def build_factor_summary(
        self,
        factor_result,
    ):

        if not factor_result:

            return {

                "Status":
                    "Unavailable",

                "Overall Score":
                    None,

                "Factor Scores":
                    {},

            }

        factor_scores = (
            factor_result.get(
                "Factor Scores",
                {},
            )
        )

        ranked_factors = sorted(
            factor_scores.items(),
            key=lambda item:
                item[1],
            reverse=True,
        )

        strengths = []

        weaknesses = []

        for factor, score in (
            ranked_factors
        ):

            score = self.safe_float(
                score,
                50,
            )

            if score >= 80:

                strengths.append({

                    "Factor":
                        factor,

                    "Score":
                        score,

                })

            elif score < 50:

                weaknesses.append({

                    "Factor":
                        factor,

                    "Score":
                        score,

                })

        return {

            "Status":
                "Available",

            "Overall Score":
                factor_result.get(
                    "Overall Score"
                ),

            "Factor Scores":
                factor_scores,

            "Weights":
                factor_result.get(
                    "Weights",
                    {},
                ),

            "Strengths":
                strengths,

            "Weaknesses":
                weaknesses,

        }

    # ============================================================
    # VALUATION SUMMARY
    # ============================================================

    def build_valuation_summary(
        self,
        valuation_result,
    ):

        if not valuation_result:

            return {

                "Status":
                    "Unavailable",

            }

        if valuation_result.get(
            "Status"
        ) != "OK":

            return {

                "Status":
                    "Unavailable",

                "Reason":
                    valuation_result.get(
                        "Reason"
                    ),

            }

        scenarios = (
            valuation_result.get(
                "Scenarios",
                {},
            )
        )

        base = (
            scenarios.get(
                "Base",
                {},
            )
        )

        bear = (
            scenarios.get(
                "Bear",
                {},
            )
        )

        bull = (
            scenarios.get(
                "Bull",
                {},
            )
        )

        return {

            "Status":
                "Available",

            "Current Price":
                valuation_result.get(
                    "Current Price"
                ),

            "Intrinsic Value":

                base.get(
                    "Intrinsic Value"
                ),

            "Bear Value":

                bear.get(
                    "Intrinsic Value"
                ),

            "Base Value":

                base.get(
                    "Intrinsic Value"
                ),

            "Bull Value":

                bull.get(
                    "Intrinsic Value"
                ),

            "Valuation Range":
                valuation_result.get(
                    "Valuation Range",
                    {},
                ),

            "Bear Return":
                bear.get(
                    "Expected Return"
                ),

            "Base Return":
                base.get(
                    "Expected Return"
                ),

            "Bull Return":
                bull.get(
                    "Expected Return"
                ),

            "Bear Annualised Return":
                bear.get(
                    "Annualised Return"
                ),

            "Base Annualised Return":
                base.get(
                    "Annualised Return"
                ),

            "Bull Annualised Return":
                bull.get(
                    "Annualised Return"
                ),

            "Horizon Years":
                base.get(
                    "Horizon Years"
                ),

            "Horizon Returns":
                valuation_result.get(
                    "Horizon Returns",
                    {},
                ),

            "Growth Assumption":
                valuation_result.get(
                    "Base Growth Assumption"
                ),

            "FCF Margin":
                valuation_result.get(
                    "Base FCF Margin"
                ),

            "Sensitivity":
                valuation_result.get(
                    "Sensitivity",
                    {},
                ),

        }

    # ============================================================
    # CATALYST SUMMARY
    # ============================================================

    def build_catalyst_summary(
        self,
        catalyst_result,
    ):

        if not catalyst_result:

            return {

                "Status":
                    "Unavailable",

                "Catalyst Score":
                    None,

            }

        upcoming = (
            catalyst_result.get(
                "Upcoming Catalysts",
                [],
            )
        )

        recent = (
            catalyst_result.get(
                "Recent Catalysts",
                [],
            )
        )

        return {

            "Status":
                "Available",

            "Catalyst Score":
                catalyst_result.get(
                    "Catalyst Score"
                ),

            "Catalyst Count":
                catalyst_result.get(
                    "Catalyst Count",
                    0,
                ),

            "Source Count":
                catalyst_result.get(
                    "Source Count",
                    0,
                ),

            "Sources":
                catalyst_result.get(
                    "Sources",
                    [],
                ),

            "Upcoming Catalysts":
                upcoming,

            "Recent Catalysts":
                recent,

        }

    # ============================================================
    # EXPECTED RETURN SUMMARY
    # ============================================================

    def build_return_summary(
        self,
        factor_result,
        valuation_summary,
    ):

        # --------------------------------------------------------
        # The multi-factor engine's expected return is a separate
        # model from the DCF-derived return.
        # --------------------------------------------------------

        factor_return = (
            self.safe_float(
                factor_result.get(
                    "Expected Return"
                )
            )
        )

        factor_confidence = (
            self.safe_float(
                factor_result.get(
                    "Expected Return Confidence"
                )
            )
        )

        factor_horizon = (
            factor_result.get(
                "Expected Return Horizon Days"
            )
        )

        if factor_horizon is None:

            factor_horizon = (
                factor_result.get(
                    "Horizon Days"
                )
            )

        if factor_horizon is None:

            factor_horizon = 252

        # --------------------------------------------------------
        # DCF return
        # --------------------------------------------------------

        dcf_return = (
            self.safe_float(
                valuation_summary.get(
                    "Base Return"
                )
            )
        )

        dcf_annualised = (
            self.safe_float(
                valuation_summary.get(
                    "Base Annualised Return"
                )
            )
        )

        dcf_horizon = (
            valuation_summary.get(
                "Horizon Years"
            )
        )

        # --------------------------------------------------------
        # Return structure.
        # --------------------------------------------------------

        return {

            "Factor Model": {

                "Expected Return":
                    factor_return,

                "Horizon Days":
                    factor_horizon,

                "Horizon Years":
                    (
                        round(
                            factor_horizon
                            / 252,
                            2,
                        )
                        if factor_horizon
                        else None
                    ),

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
    # RESEARCH THESIS
    # ============================================================

    def build_thesis(
        self,
        factor_summary,
        valuation_summary,
        catalyst_summary,
    ):

        strengths = []

        concerns = []

        # --------------------------------------------------------
        # Fundamental strengths
        # --------------------------------------------------------

        for item in (
            factor_summary.get(
                "Strengths",
                [],
            )
        ):

            strengths.append({

                "Area":
                    item.get(
                        "Factor"
                    ),

                "Score":
                    item.get(
                        "Score"
                    ),

                "Reason":
                    (
                        f"{item.get('Factor')} "
                        f"score is "
                        f"{item.get('Score')}/100."
                    ),

            })

        # --------------------------------------------------------
        # Fundamental weaknesses
        # --------------------------------------------------------

        for item in (
            factor_summary.get(
                "Weaknesses",
                [],
            )
        ):

            concerns.append({

                "Area":
                    item.get(
                        "Factor"
                    ),

                "Score":
                    item.get(
                        "Score"
                    ),

                "Reason":
                    (
                        f"{item.get('Factor')} "
                        f"score is only "
                        f"{item.get('Score')}/100."
                    ),

            })

        # --------------------------------------------------------
        # Valuation
        # --------------------------------------------------------

        intrinsic_value = (
            self.safe_float(
                valuation_summary.get(
                    "Intrinsic Value"
                )
            )
        )

        current_price = (
            self.safe_float(
                valuation_summary.get(
                    "Current Price"
                )
            )
        )

        if (
            intrinsic_value is not None
            and current_price is not None
            and current_price > 0
        ):

            upside = (
                intrinsic_value
                / current_price
            ) - 1

            if upside >= 0.20:

                strengths.append({

                    "Area":
                        "Valuation",

                    "Score":
                        None,

                    "Reason":
                        (
                            f"Base intrinsic value "
                            f"implies approximately "
                            f"{upside * 100:.1f}% upside."
                        ),

                })

            elif upside <= -0.20:

                concerns.append({

                    "Area":
                        "Valuation",

                    "Score":
                        None,

                    "Reason":
                        (
                            f"Base intrinsic value "
                            f"is approximately "
                            f"{abs(upside) * 100:.1f}% below "
                            f"the current price."
                        ),

                })

        # --------------------------------------------------------
        # Catalysts
        # --------------------------------------------------------

        catalyst_score = (
            self.safe_float(
                catalyst_summary.get(
                    "Catalyst Score"
                )
            )
        )

        if catalyst_score is not None:

            if catalyst_score >= 75:

                strengths.append({

                    "Area":
                        "Catalysts",

                    "Score":
                        catalyst_score,

                    "Reason":
                        (
                            "The catalyst engine "
                            "identifies a strong "
                            "potential catalyst setup."
                        ),

                })

            elif catalyst_score < 40:

                concerns.append({

                    "Area":
                        "Catalysts",

                    "Score":
                        catalyst_score,

                    "Reason":
                        (
                            "The catalyst engine "
                            "currently identifies "
                            "limited near-term catalysts."
                        ),

                })

        # --------------------------------------------------------
        # Thesis summary
        # --------------------------------------------------------

        positive_count = len(
            strengths
        )

        concern_count = len(
            concerns
        )

        if (
            positive_count >= 3
            and concern_count <= 1
        ):

            thesis = (
                "The research profile is "
                "predominantly positive, with "
                "multiple independent signals "
                "supporting the investment case."
            )

        elif (
            concern_count >= 3
        ):

            thesis = (
                "The research profile contains "
                "several material concerns that "
                "should reduce investment conviction."
            )

        else:

            thesis = (
                "The research profile is mixed, "
                "with both positive signals and "
                "material areas requiring further "
                "investigation."
            )

        return {

            "Summary":
                thesis,

            "Strengths":
                strengths,

            "Concerns":
                concerns,

        }

    # ============================================================
    # INVESTMENT COMMITTEE INPUT
    # ============================================================

    def build_committee_input(
        self,
        factor_result,
        factor_summary,
        valuation_summary,
        catalyst_summary,
    ):

        factor_scores = (
            factor_summary.get(
                "Factor Scores",
                {},
            )
        )

        # --------------------------------------------------------
        # We currently preserve the ten-factor model as the
        # committee's core quantitative framework.
        #
        # Valuation and catalysts are returned separately so we
        # can integrate them into the committee deliberately once
        # the evidence architecture is complete.
        # --------------------------------------------------------

        analysis = {

            "Ticker":
                factor_result.get(
                    "Ticker"
                ),

            "Company":
                factor_result.get(
                    "Company"
                ),

            "Sector":
                factor_result.get(
                    "Sector"
                ),

            "Industry":
                factor_result.get(
                    "Industry"
                ),

            "Overall Score":
                factor_result.get(
                    "Overall Score"
                ),

            "Factor Scores":
                factor_scores,

            "Weights":
                factor_result.get(
                    "Weights",
                    {},
                ),

            "Expected Return":
                factor_result.get(
                    "Expected Return"
                ),

            "Expected Return Confidence":
                factor_result.get(
                    "Expected Return Confidence"
                ),

            "Expected Return Horizon Days":
                factor_result.get(
                    "Expected Return Horizon Days",
                    252,
                ),

            "Valuation":
                valuation_summary,

            "Catalysts":
                catalyst_summary,

        }

        return analysis

    # ============================================================
    # FULL EQUITY RESEARCH
    # ============================================================

    def analyse(
        self,
        symbol,
        company_name=None,
        prices=None,
        info=None,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        print()
        print("=" * 80)
        print(
            f"RESEARCH AGGREGATOR — {symbol}"
        )
        print("=" * 80)

        # ========================================================
        # STEP 1 — MULTI FACTOR
        # ========================================================

        print()
        print(
            "1. Multi-factor analysis..."
        )

        factor_result = (
            self.factor_engine.analyse(
                symbol,
                prices=prices,
                info=info,
            )
        )

        if factor_result is None:

            return {

                "Ticker":
                    symbol,

                "Status":
                    "FAILED",

                "Reason":
                    "Multi-factor analysis failed.",

                "Research Timestamp":
                    self.utc_now(),

            }

        # ========================================================
        # STEP 2 — VALUATION
        # ========================================================

        print(
            "2. Intrinsic value analysis..."
        )

        valuation_result = (
            self.valuation_engine.analyse(
                symbol
            )
        )

        # ========================================================
        # STEP 3 — CATALYSTS
        # ========================================================

        print(
            "3. Catalyst analysis..."
        )

        catalyst_result = (
            self.catalyst_engine.analyse(
                symbol,
                company_name=
                    company_name
                    or factor_result.get(
                        "Company"
                    ),
            )
        )

        # ========================================================
        # STEP 4 — SUMMARIES
        # ========================================================

        factor_summary = (
            self.build_factor_summary(
                factor_result
            )
        )

        valuation_summary = (
            self.build_valuation_summary(
                valuation_result
            )
        )

        catalyst_summary = (
            self.build_catalyst_summary(
                catalyst_result
            )
        )

        return_summary = (
            self.build_return_summary(
                factor_result,
                valuation_summary,
            )
        )

        thesis = (
            self.build_thesis(
                factor_summary,
                valuation_summary,
                catalyst_summary,
            )
        )

        # ========================================================
        # STEP 5 — COMMITTEE
        # ========================================================

        print(
            "4. Investment Committee..."
        )

        committee_input = (
            self.build_committee_input(
                factor_result,
                factor_summary,
                valuation_summary,
                catalyst_summary,
            )
        )

        committee_result = (
            self.committee.review(
                committee_input
            )
        )

        # ========================================================
        # STEP 6 — COMPLETE RESEARCH DOSSIER
        # ========================================================

        research = {

            "Ticker":
                symbol,

            "Company":
                factor_result.get(
                    "Company"
                ),

            "Sector":
                factor_result.get(
                    "Sector"
                ),

            "Industry":
                factor_result.get(
                    "Industry"
                ),

            "Status":
                "COMPLETE",

            "Research Timestamp":
                self.utc_now(),

            # ----------------------------------------------------
            # Final decision
            # ----------------------------------------------------

            "Decision":
                committee_result,

            # ----------------------------------------------------
            # Research thesis
            # ----------------------------------------------------

            "Investment Thesis":
                thesis,

            # ----------------------------------------------------
            # Multi-factor research
            # ----------------------------------------------------

            "Fundamental Research": {

                "Overall Score":
                    factor_result.get(
                        "Overall Score"
                    ),

                "Factor Scores":
                    factor_summary.get(
                        "Factor Scores",
                        {},
                    ),

                "Weights":
                    factor_summary.get(
                        "Weights",
                        {},
                    ),

                "Strengths":
                    factor_summary.get(
                        "Strengths",
                        [],
                    ),

                "Weaknesses":
                    factor_summary.get(
                        "Weaknesses",
                        [],
                    ),

            },

            # ----------------------------------------------------
            # Valuation
            # ----------------------------------------------------

            "Valuation Research":
                valuation_summary,

            # ----------------------------------------------------
            # Catalysts
            # ----------------------------------------------------

            "Catalyst Research":
                catalyst_summary,

            # ----------------------------------------------------
            # Return forecasts
            # ----------------------------------------------------

            "Expected Returns":
                return_summary,

            # ----------------------------------------------------
            # Raw research
            #
            # This is deliberately retained. The eventual UI can
            # expose the detailed underlying evidence.
            # ----------------------------------------------------

            "Raw Research": {

                "Multi Factor":
                    factor_result,

                "Valuation":
                    valuation_result,

                "Catalysts":
                    catalyst_result,

            },

            # ----------------------------------------------------
            # Research provenance
            # ----------------------------------------------------

            "Research Sources": {

                "Fundamentals":
                    "Yahoo Finance",

                "Valuation":
                    "Yahoo Finance + DCF",

                "SEC":
                    "SEC EDGAR",

                "News":
                    "Google News RSS",

            },

        }

        # ========================================================
        # SAVE
        # ========================================================

        output_path = os.path.join(
            self.output_directory,
            f"{symbol}.json",
        )

        self.save_json(
            output_path,
            research,
        )

        research[
            "Output Path"
        ] = output_path

        # ========================================================
        # SUMMARY
        # ========================================================

        print()
        print(
            "=" * 80
        )

        print(
            f"{symbol} RESEARCH COMPLETE"
        )

        print(
            "=" * 80
        )

        print()

        print(
            f"Overall Score: "
            f"{committee_result.get('Committee Score')}"
        )

        print(
            f"Recommendation: "
            f"{committee_result.get('Recommendation')}"
        )

        print(
            f"Confidence: "
            f"{committee_result.get('Confidence')}%"
        )

        print()

        print(
            f"Factor Score: "
            f"{factor_result.get('Overall Score')}"
        )

        print(
            f"Catalyst Score: "
            f"{catalyst_summary.get('Catalyst Score')}"
        )

        print(
            f"Base Intrinsic Value: "
            f"{valuation_summary.get('Base Value')}"
        )

        print(
            f"Base DCF Return: "
            f"{valuation_summary.get('Base Return')}%"
        )

        print(
            f"DCF Horizon: "
            f"{valuation_summary.get('Horizon Years')} years"
        )

        print()

        print(
            f"Research saved to:"
        )

        print(
            output_path
        )

        return research

    # ============================================================
    # DEVELOPMENT RUN
    # ============================================================

    def development_run(
        self,
        symbol,
    ):

        return self.analyse(
            symbol
        )


if __name__ == "__main__":

    aggregator = (
        ResearchAggregator()
    )

    aggregator.development_run(
        "NVDA"
    )