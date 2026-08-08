import json
import os
from datetime import datetime, timezone


class EvidenceEngine:

    def __init__(
        self,
        output_directory="data/research/evidence",
    ):

        self.output_directory = (
            output_directory
        )

        os.makedirs(
            self.output_directory,
            exist_ok=True,
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
    # SAVE
    # ============================================================

    def save(
        self,
        symbol,
        evidence,
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
                evidence,
                file,
                indent=2,
                default=str,
            )

        return path

    # ============================================================
    # FACTOR EVIDENCE
    # ============================================================

    def factor_evidence(
        self,
        research,
    ):

        fundamental = (
            research.get(
                "Fundamental Research",
                {},
            )
        )

        scores = (
            fundamental.get(
                "Factor Scores",
                {},
            )
        )

        weights = (
            fundamental.get(
                "Weights",
                {},
            )
        )

        evidence = []

        for factor, score in (
            scores.items()
        ):

            score = self.safe_float(
                score,
                50,
            )

            weight = self.safe_float(
                weights.get(
                    factor
                ),
                0,
            )

            if score >= 80:

                conclusion = (
                    "Strong positive signal."
                )

            elif score >= 70:

                conclusion = (
                    "Positive supporting signal."
                )

            elif score >= 50:

                conclusion = (
                    "Neutral or mixed signal."
                )

            elif score >= 40:

                conclusion = (
                    "Negative signal requiring caution."
                )

            else:

                conclusion = (
                    "Materially negative signal."
                )

            evidence.append({

                "Type":
                    "Factor Analysis",

                "Factor":
                    factor,

                "Score":
                    score,

                "Weight":
                    weight,

                "Conclusion":
                    conclusion,

                "Source":
                    "Multi-Factor Engine",

                "Source Type":
                    "Quantitative Model",

            })

        return evidence

    # ============================================================
    # VALUATION EVIDENCE
    # ============================================================

    def valuation_evidence(
        self,
        research,
    ):

        valuation = (
            research.get(
                "Valuation Research",
                {},
            )
        )

        evidence = []

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

        base_value = (
            self.safe_float(
                valuation.get(
                    "Base Value"
                )
            )
        )

        bull_value = (
            self.safe_float(
                valuation.get(
                    "Bull Value"
                )
            )
        )

        growth = (
            self.safe_float(
                valuation.get(
                    "Growth Assumption"
                )
            )

        )

        fcf_margin = (
            self.safe_float(
                valuation.get(
                    "FCF Margin"
                )
            )
        )

        horizon = (
            valuation.get(
                "Horizon Years"
            )
        )

        # --------------------------------------------------------
        # Current price
        # --------------------------------------------------------

        if current_price is not None:

            evidence.append({

                "Type":
                    "Market Price",

                "Metric":
                    "Current Share Price",

                "Value":
                    current_price,

                "Source":
                    "Yahoo Finance",

                "Source Type":
                    "Market Data",

            })

        # --------------------------------------------------------
        # Bear case
        # --------------------------------------------------------

        if bear_value is not None:

            evidence.append({

                "Type":
                    "Intrinsic Value",

                "Scenario":
                    "Bear",

                "Value":
                    bear_value,

                "Horizon Years":
                    horizon,

                "Source":
                    "Valuation Engine",

                "Source Type":
                    "DCF Model",

            })

        # --------------------------------------------------------
        # Base case
        # --------------------------------------------------------

        if base_value is not None:

            evidence.append({

                "Type":
                    "Intrinsic Value",

                "Scenario":
                    "Base",

                "Value":
                    base_value,

                "Horizon Years":
                    horizon,

                "Source":
                    "Valuation Engine",

                "Source Type":
                    "DCF Model",

            })

        # --------------------------------------------------------
        # Bull case
        # --------------------------------------------------------

        if bull_value is not None:

            evidence.append({

                "Type":
                    "Intrinsic Value",

                "Scenario":
                    "Bull",

                "Value":
                    bull_value,

                "Horizon Years":
                    horizon,

                "Source":
                    "Valuation Engine",

                "Source Type":
                    "DCF Model",

            })

        # --------------------------------------------------------
        # Upside
        # --------------------------------------------------------

        if (
            current_price is not None
            and base_value is not None
            and current_price > 0
        ):

            upside = (
                base_value
                / current_price
            ) - 1

            evidence.append({

                "Type":
                    "Valuation Signal",

                "Metric":
                    "Base Case Upside",

                "Value":
                    round(
                        upside * 100,
                        2,
                    ),

                "Units":
                    "Percent",

                "Source":
                    "Valuation Engine",

                "Source Type":
                    "DCF Model",

            })

        # --------------------------------------------------------
        # Assumptions
        # --------------------------------------------------------

        if growth is not None:

            evidence.append({

                "Type":
                    "Valuation Assumption",

                "Metric":
                    "Growth Assumption",

                "Value":
                    growth,

                "Units":
                    "Percent",

                "Source":
                    "Valuation Engine",

                "Source Type":
                    "DCF Model",

            })

        if fcf_margin is not None:

            evidence.append({

                "Type":
                    "Valuation Assumption",

                "Metric":
                    "FCF Margin",

                "Value":
                    fcf_margin,

                "Units":
                    "Percent",

                "Source":
                    "Valuation Engine",

                "Source Type":
                    "DCF Model",

            })

        return evidence

    # ============================================================
    # RETURN EVIDENCE
    # ============================================================

    def return_evidence(
        self,
        research,
    ):

        returns = (
            research.get(
                "Expected Returns",
                {},
            )
        )

        evidence = []

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

        if factor_return is not None:

            evidence.append({

                "Type":
                    "Expected Return Forecast",

                "Model":
                    "Multi-Factor",

                "Expected Return":
                    factor_return,

                "Horizon Days":
                    factor_horizon,

                "Confidence":
                    factor_confidence,

                "Source":
                    "Multi-Factor Engine",

                "Source Type":
                    "Quantitative Forecast",

            })

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

        if dcf_return is not None:

            evidence.append({

                "Type":
                    "Expected Return Forecast",

                "Model":
                    "Intrinsic Value",

                "Expected Return":
                    dcf_return,

                "Annualised Return":
                    dcf_annualised,

                "Horizon Years":
                    dcf_horizon,

                "Source":
                    "Valuation Engine",

                "Source Type":
                    "DCF Forecast",

            })

        return evidence

    # ============================================================
    # CATALYST EVIDENCE
    # ============================================================

    def catalyst_evidence(
        self,
        research,
    ):

        catalysts = (
            research.get(
                "Catalyst Research",
                {},
            )
        )

        evidence = []

        score = (
            self.safe_float(
                catalysts.get(
                    "Catalyst Score"
                )
            )
        )

        if score is not None:

            evidence.append({

                "Type":
                    "Catalyst Score",

                "Score":
                    score,

                "Source":
                    "Catalyst Engine",

                "Source Type":
                    "Event Analysis",

            })

        # --------------------------------------------------------
        # Upcoming catalysts
        # --------------------------------------------------------

        for catalyst in (
            catalysts.get(
                "Upcoming Catalysts",
                [],
            )
        ):

            evidence.append({

                "Type":
                    "Upcoming Catalyst",

                "Date":
                    catalyst.get(
                        "Date"
                    ),

                "Catalyst Type":
                    catalyst.get(
                        "Type"
                    ),

                "Impact":
                    catalyst.get(
                        "Impact"
                    ),

                "Reason":
                    catalyst.get(
                        "Reason"
                    ),

                "Source":
                    catalyst.get(
                        "Source"
                    ),

                "Source Type":
                    "External Event",

            })

        # --------------------------------------------------------
        # Recent catalysts
        # --------------------------------------------------------

        for catalyst in (
            catalysts.get(
                "Recent Catalysts",
                [],
            )
        ):

            evidence.append({

                "Type":
                    "Recent Catalyst",

                "Date":
                    catalyst.get(
                        "Date"
                    ),

                "Catalyst Type":
                    catalyst.get(
                        "Type"
                    ),

                "Impact":
                    catalyst.get(
                        "Impact"
                    ),

                "Reason":
                    catalyst.get(
                        "Reason"
                    ),

                "Source":
                    catalyst.get(
                        "Source"
                    ),

                "Source Type":
                    "External Event",

            })

        return evidence

    # ============================================================
    # RESEARCH SOURCES
    # ============================================================

    def source_evidence(
        self,
        research,
    ):

        sources = (
            research.get(
                "Research Sources",
                {},
            )
        )

        evidence = []

        for category, source in (
            sources.items()
        ):

            evidence.append({

                "Category":
                    category,

                "Source":
                    source,

            })

        return evidence

    # ============================================================
    # EVIDENCE SUMMARY
    # ============================================================

    def build_summary(
        self,
        evidence,
    ):

        total = len(
            evidence
        )

        quantitative = 0

        market = 0

        external = 0

        model = 0

        for item in evidence:

            source_type = (
                str(
                    item.get(
                        "Source Type",
                        "",
                    )
                )
                .lower()
            )

            if (
                "quantitative"
                in source_type
            ):

                quantitative += 1

            if (
                "market"
                in source_type
            ):

                market += 1

            if (
                "external"
                in source_type
            ):

                external += 1

            if (
                "model"
                in source_type
                or "dcf"
                in source_type
            ):

                model += 1

        return {

            "Total Evidence Items":
                total,

            "Quantitative Evidence":
                quantitative,

            "Market Evidence":
                market,

            "External Event Evidence":
                external,

            "Model Evidence":
                model,

        }

    # ============================================================
    # BUILD
    # ============================================================

    def analyse(
        self,
        symbol,
        research=None,
        research_path=None,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        if research is None:

            if research_path is None:

                research_path = os.path.join(
                    "data",
                    "research",
                    f"{symbol}.json",
                )

            if not os.path.exists(
                research_path
            ):

                return {

                    "Ticker":
                        symbol,

                    "Status":
                        "FAILED",

                    "Reason":
                        (
                            "Research dossier "
                            "not found."
                        ),

                }

            with open(
                research_path,
                "r",
            ) as file:

                research = json.load(
                    file
                )

        # ========================================================
        # BUILD EVIDENCE
        # ========================================================

        factor = (
            self.factor_evidence(
                research
            )
        )

        valuation = (
            self.valuation_evidence(
                research
            )
        )

        returns = (
            self.return_evidence(
                research
            )

        )

        catalysts = (
            self.catalyst_evidence(
                research
            )

        )

        all_evidence = (
            factor
            + valuation
            + returns
            + catalysts
        )

        sources = (
            self.source_evidence(
                research
            )
        )

        summary = (
            self.build_summary(
                all_evidence
            )
        )

        # ========================================================
        # RESULT
        # ========================================================

        result = {

            "Ticker":
                symbol,

            "Company":
                research.get(
                    "Company"
                ),

            "Generated At":
                self.utc_now(),

            "Status":
                "COMPLETE",

            "Summary":
                summary,

            "Evidence": {

                "Fundamentals":
                    factor,

                "Valuation":
                    valuation,

                "Expected Returns":
                    returns,

                "Catalysts":
                    catalysts,

            },

            "Sources":
                sources,

        }

        path = (
            self.save(
                symbol,
                result,
            )
        )

        result[
            "Output Path"
        ] = path

        # ========================================================
        # PRINT
        # ========================================================

        print()
        print("=" * 80)
        print(
            f"EVIDENCE REPORT — {symbol}"
        )
        print("=" * 80)

        print()

        print(
            f"Evidence items: "
            f"{summary['Total Evidence Items']}"
        )

        print(
            f"Quantitative: "
            f"{summary['Quantitative Evidence']}"
        )

        print(
            f"Market: "
            f"{summary['Market Evidence']}"
        )

        print(
            f"External events: "
            f"{summary['External Event Evidence']}"
        )

        print(
            f"Model evidence: "
            f"{summary['Model Evidence']}"
        )

        print()

        print(
            f"Saved to: "
            f"{path}"
        )

        return result


if __name__ == "__main__":

    engine = (
        EvidenceEngine()
    )

    engine.analyse(
        "NVDA"
    )