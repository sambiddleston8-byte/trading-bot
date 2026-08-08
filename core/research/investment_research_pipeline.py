from datetime import datetime, timezone
import json
from pathlib import Path


class InvestmentResearchPipeline:

    VERSION = "1.1-research-integrated"

    # ========================================================
    # TIME
    # ========================================================

    @staticmethod
    def now():

        return datetime.now(
            timezone.utc
        ).isoformat()

    # ========================================================
    # IMPORT ENGINES
    # ========================================================

    @staticmethod
    def load_engines():

        from core.fundamental_analysis_engine import (
            FundamentalAnalysisEngine,
        )

        from core.valuation_engine import (
            ValuationEngine,
        )

        from core.investment_decision_engine import (
            InvestmentDecisionEngine,
        )

        from core.research.catalyst_engine import (
            CatalystEngine,
        )

        from core.research.news_research_engine import (
            NewsResearchEngine,
        )

        from core.research.catalyst_evidence_bridge import (
            CatalystEvidenceBridge,
        )

        from core.research.catalyst_validation_engine import (
            CatalystValidationEngine,
        )

        from core.research.thesis_challenger import (
            ThesisChallenger,
        )

        from core.research.research_synthesis_engine import (
            ResearchSynthesisEngine,
        )

        from core.research.evidence_audit_engine import (
            EvidenceAuditEngine,
        )

        return {
            "fundamental":
                FundamentalAnalysisEngine,

            "valuation":
                ValuationEngine,

            "decision":
                InvestmentDecisionEngine,

            "catalyst":
                CatalystEngine,

            "news":
                NewsResearchEngine,

            "catalyst_bridge":
                CatalystEvidenceBridge,

            "catalyst_validation":
                CatalystValidationEngine,

            "thesis":
                ThesisChallenger,

            "synthesis":
                ResearchSynthesisEngine,

            "audit":
                EvidenceAuditEngine,
        }

    # ========================================================
    # SAFE METHOD CALL
    # ========================================================

    @staticmethod
    def call(
        engine,
        method,
        *args,
        **kwargs,
    ):

        function = getattr(
            engine,
            method,
            None,
        )

        if function is None:

            raise AttributeError(
                f"{engine.__name__} has no method "
                f"'{method}'"
            )

        return function(
            *args,
            **kwargs,
        )

    # ========================================================
    # BUILD CORE ANALYSIS
    # ========================================================

    @classmethod
    def analyse_core(
        cls,
        ticker,
    ):

        engines = cls.load_engines()

        fundamental = cls.call(
            engines["fundamental"](),
            "analyse",
            ticker,
        )

        valuation = cls.call(
            engines["valuation"](),
            "analyse",
            ticker,
        )

        decision = cls.call(
            engines["decision"](),
            "analyse",
            fundamental,
            valuation,
        )

        return {
            "fundamental":
                fundamental,

            "valuation":
                valuation,

            "decision":
                decision,
        }

    # ========================================================
    # BUILD NEWS RESEARCH
    # ========================================================

    @classmethod
    def analyse_news(
        cls,
        ticker,
    ):

        engines = cls.load_engines()

        news_engine = (
            engines["news"]()
        )

        # ----------------------------------------------------
        # Most news engines expose analyse/research.
        # Try analyse first, then research.
        # ----------------------------------------------------

        if hasattr(
            news_engine,
            "analyse",
        ):

            return news_engine.analyse(
                ticker
            )

        if hasattr(
            news_engine,
            "research",
        ):

            return news_engine.research(
                ticker
            )

        raise AttributeError(
            "NewsResearchEngine does not expose "
            "analyse() or research()."
        )

    # ========================================================
    # BUILD CATALYST RESEARCH
    # ========================================================

    @classmethod
    def analyse_catalysts(
        cls,
        ticker,
    ):

        engines = cls.load_engines()

        catalyst_engine = (
            engines["catalyst"]()
        )

        if hasattr(
            catalyst_engine,
            "analyse",
        ):

            return catalyst_engine.analyse(
                ticker
            )

        if hasattr(
            catalyst_engine,
            "research",
        ):

            return catalyst_engine.research(
                ticker
            )

        raise AttributeError(
            "CatalystEngine does not expose "
            "analyse() or research()."
        )

    # ========================================================
    # THESIS CHALLENGE
    # ========================================================

    @classmethod
    def challenge_thesis(
        cls,
        ticker,
        fundamental,
        valuation,
        decision,
        catalysts,
        news,
    ):

        engine = (
            cls.load_engines()
            ["thesis"]
        )

        # ----------------------------------------------------
        # Build the investigation using the existing
        # ThesisChallenger interface.
        # ----------------------------------------------------

        investigation = engine.build(
            ticker=ticker,
            positive_thesis=(
                "Investment case supported by the fundamental "
                "analysis, valuation analysis and available "
                "research evidence."
            ),
            fundamentals=fundamental,
            valuation=valuation,
            expectations={
                "forward_revenue_growth":
                    fundamental.get(
                        "growth",
                        {},
                    ).get(
                        "forward_revenue_growth"
                    ),

                "forward_eps_growth":
                    fundamental.get(
                        "growth",
                        {},
                    ).get(
                        "forward_eps_growth"
                    ),

                "forecast_confidence":
                    fundamental.get(
                        "forecast_validation",
                        {},
                    ).get(
                        "overall_confidence"
                    ),
            },
        )

        # Additional evidence produced by the wider research
        # stack is attached after the challenger creates its
        # investigation structure.

        investigation["catalysts"] = (
            catalysts
        )

        investigation["news"] = (
            news
        )

        investigation["data_quality"] = (
            fundamental.get(
                "data_quality",
                {}
            )
        )

        # ----------------------------------------------------
        # Calculate the final adversarial result.
        # ----------------------------------------------------

        # ----------------------------------------------------
        # Populate the challenge areas using the evidence
        # already produced by the research stack.
        # ----------------------------------------------------

        investigation = engine.populate_findings(
            investigation
        )

        result = engine.calculate_result(
            investigation
        )

        # ----------------------------------------------------
        # Generate the canonical challenger summary.
        #
        # calculate_result() returns the investigation itself,
        # while summary() calculates tested/material-negative
        # counts used by downstream synthesis and auditing.
        # ----------------------------------------------------

        summary = engine.summary(
            result
        )

        # ----------------------------------------------------
        # Preserve the underlying investigation and expose the
        # summary explicitly.
        # ----------------------------------------------------

        if isinstance(
            result,
            dict,
        ):

            result = dict(
                result
            )

            result[
                "summary"
            ] = summary

            result[
                "investigation"
            ] = investigation

        return result

    # ========================================================
    # CATALYST VALIDATION
    # ========================================================

    @classmethod
    def validate_catalysts(
        cls,
        catalysts,
    ):

        validator = (
            cls.load_engines()
            ["catalyst_validation"]
        )

        if not isinstance(
            catalysts,
            dict,
        ):

            return catalysts

        items = catalysts.get(
            "catalysts",
            catalysts.get(
                "items",
                []
            ),
        )

        if not isinstance(
            items,
            list,
        ):

            return catalysts

        validated = []

        for catalyst in items:

            try:

                validated.append(
                    validator.validate(
                        catalyst
                    )
                )

            except Exception as exc:

                failed = dict(
                    catalyst
                )

                failed[
                    "validation"
                ] = {

                    "status":
                        "ERROR",

                    "error":
                        str(exc),

                }

                validated.append(
                    failed
                )

        result = dict(
            catalysts
        )

        result[
            "validated_catalysts"
        ] = validated

        return result

    # ========================================================
    # SYNTHESIS INPUT
    # ========================================================

    @classmethod
    def build_synthesis_input(
        cls,
        ticker,
        fundamental,
        valuation,
        decision,
        catalysts,
        thesis,
        news,
    ):

        decision_scores = (
            decision.get(
                "scores",
                {}
            )
        )

        valuation_data = (
            decision.get(
                "valuation",
                {}
            )
        )

        thesis_data = (
            thesis
            if isinstance(
                thesis,
                dict,
            )
            else {}
        )

        thesis_summary = (
            thesis_data.get(
                "summary",
                {}
            )
        )

        catalyst_data = (
            catalysts
            if isinstance(
                catalysts,
                dict,
            )
            else {}
        )

        fundamental_data = (
            fundamental
            if isinstance(
                fundamental,
                dict,
            )
            else {}
        )

        validation = (
            fundamental_data.get(
                "validation",
                {}
            )
        )

        provenance = (
            fundamental_data.get(
                "provenance",
                {}
            )
        )

        data_quality = (
            fundamental_data.get(
                "data_quality",
                {}
            )
        )

        # ----------------------------------------------------
        # Normalise catalyst scores.
        # ----------------------------------------------------

        catalyst_summary = (
            catalyst_data.get(
                "summary",
                {},
            )
            if isinstance(
                catalyst_data.get(
                    "summary",
                    {}
                ),
                dict,
            )
            else {}
        )

        positive_score = (
            catalyst_data.get(
                "positive_score",
                catalyst_summary.get(
                    "positive_score",
                    catalyst_summary.get(
                        "positive_catalysts",
                        0,
                    ),
                ),
            )
        )

        negative_score = (
            catalyst_data.get(
                "negative_score",
                catalyst_summary.get(
                    "negative_score",
                    catalyst_summary.get(
                        "negative_catalysts",
                        0,
                    ),
                ),
            )
        )

        # ----------------------------------------------------
        # Normalise thesis result.
        # ----------------------------------------------------

        thesis_result = (
            thesis_data.get(
                "overall_challenge_result"
            )
            or
            thesis_data.get(
                "result"
            )
            or
            "INSUFFICIENT_CHALLENGE"
        )

        return {

            "ticker":
                ticker,

            "scores": {

                "fundamental_quality":
                    decision_scores.get(
                        "fundamental_quality"
                    ),

                "valuation":
                    decision_scores.get(
                        "valuation"
                    ),

                "forward_expectations":
                    decision_scores.get(
                        "forward_expectations"
                    ),

                "data_confidence":
                    decision_scores.get(
                        "data_confidence"
                    ),

                "overall":
                    decision_scores.get(
                        "overall"
                    ),

            },

            "fundamentals": {

                "drivers":
                    fundamental_data.get(
                        "key_drivers",
                        fundamental_data.get(
                            "drivers",
                            []
                        ),
                    ),

            },

            "validation":
                validation,

            "provenance":
                provenance,

            "data_quality":
                data_quality,

            "valuation": {

                "current_price":
                    valuation_data.get(
                        "current_price"
                    ),

                "base_intrinsic_value":
                    valuation_data.get(
                        "base_intrinsic_value"
                    ),

                "expected_return":
                    valuation_data.get(
                        "expected_return"
                    ),

                "annualised_expected_return":
                    decision.get(
                        "annualised_expected_return"
                    ),

                "status":
                    valuation_data.get(
                        "status"
                    ),

                "validation_confidence":
                    decision.get(
                        "confidence",
                        {},
                    ).get(
                        "fundamental"
                    ),

            },

            "catalysts": {

                "positive_score":
                    positive_score,

                "negative_score":
                    negative_score,

                "items":
                    catalyst_data.get(
                        "validated_catalysts",
                        catalyst_data.get(
                            "catalysts",
                            []
                        ),
                    ),

            },

            "news":
                news,

            "thesis_challenge": {

                "overall_challenge_result":
                    thesis_result,

                "result":
                    thesis_result,

                "challenge_count":
                    thesis_summary.get(
                        "challenge_count",
                        0,
                    ),

                "tested":
                    thesis_summary.get(
                        "tested",
                        0,
                    ),

                "material_negative":
                    thesis_summary.get(
                        "material_negative",
                        0,
                    ),

                "thesis_survives":
                    thesis_summary.get(
                        "thesis_survives"
                    ),

                "challenges":
                    thesis_data.get(
                        "challenges",
                        [],
                    ),

                "investigation":
                    thesis_data.get(
                        "investigation",
                    ),

            },

            "decision":
                decision.get(
                    "decision"
                ),

            # Canonical valuation fields for downstream audit,
            # synthesis and portfolio ranking.

            "current_price":
                valuation_data.get(
                    "current_price"
                ),

            "base_intrinsic_value":
                valuation_data.get(
                    "base_intrinsic_value"
                ),

            "expected_return":
                valuation_data.get(
                    "expected_return"
                ),

        }

    # ========================================================
    # AUDIT
    # ========================================================

    @classmethod
    def audit(
        cls,
        synthesis_input,
    ):

        auditor = (
            cls.load_engines()
            ["audit"]
        )

        return auditor.audit(
            synthesis_input
        )

    # ========================================================
    # COMPLETE PIPELINE
    # ========================================================

    @classmethod
    def analyse(
        cls,
        ticker,
        save=True,
    ):

        started_at = cls.now()

        # ----------------------------------------------------
        # 1. CORE ANALYSIS
        # ----------------------------------------------------

        core = cls.analyse_core(
            ticker
        )

        fundamental = core[
            "fundamental"
        ]

        valuation = core[
            "valuation"
        ]

        decision = core[
            "decision"
        ]

        # ----------------------------------------------------
        # 2. NEWS
        # ----------------------------------------------------

        try:

            news = cls.analyse_news(
                ticker
            )

        except Exception as exc:

            news = {

                "status":
                    "ERROR",

                "error":
                    str(exc),

            }

        # ----------------------------------------------------
        # 3. CATALYSTS
        # ----------------------------------------------------

        try:

            catalysts = (
                cls.analyse_catalysts(
                    ticker
                )
            )

        except Exception as exc:

            catalysts = {

                "status":
                    "ERROR",

                "error":
                    str(exc),

                "catalysts":
                    [],

            }

        # ----------------------------------------------------
        # 4. CATALYST VALIDATION
        # ----------------------------------------------------

        catalysts = (
            cls.validate_catalysts(
                catalysts
            )
        )

        # ----------------------------------------------------
        # 5. THESIS CHALLENGER
        # ----------------------------------------------------

        try:

            thesis = (
                cls.challenge_thesis(
                    ticker,
                    fundamental,
                    valuation,
                    decision,
                    catalysts,
                    news,
                )
            )

        except Exception as exc:

            thesis = {

                "status":
                    "ERROR",

                "result":
                    "INSUFFICIENT_CHALLENGE",

                "challenge_count":
                    0,

                "material_negative":
                    0,

                "error":
                    str(exc),

            }

        # ----------------------------------------------------
        # 6. SYNTHESIS INPUT
        # ----------------------------------------------------

        synthesis_input = (
            cls.build_synthesis_input(
                ticker,
                fundamental,
                valuation,
                decision,
                catalysts,
                thesis,
                news,
            )
        )

        # ----------------------------------------------------
        # 7. SYNTHESIS
        # ----------------------------------------------------

        synthesizer = (
            cls.load_engines()
            ["synthesis"]
        )

        synthesis = (
            synthesizer.synthesise(
                synthesis_input
            )
        )

        # ----------------------------------------------------
        # 8. FINAL EVIDENCE AUDIT
        # ----------------------------------------------------

        audit = cls.audit(
            synthesis_input
        )

        # ----------------------------------------------------
        # 9. FINAL RESULT
        # ----------------------------------------------------

        result = {

            "ticker":
                ticker,

            "status":
                "COMPLETE",

            "pipeline_version":
                cls.VERSION,

            "started_at":
                started_at,

            "completed_at":
                cls.now(),

            "core": {

                "fundamental":
                    fundamental,

                "valuation":
                    valuation,

                "decision":
                    decision,

            },

            "research": {

                "news":
                    news,

                "catalysts":
                    catalysts,

                "thesis_challenge":
                    thesis,

            },

            "synthesis":
                synthesis,

            "audit":
                audit,

            "synthesis_input":
                synthesis_input,

        }

        # ----------------------------------------------------
        # 10. SAVE
        # ----------------------------------------------------

        if save:

            output_dir = Path(
                "data/research/pipeline"
            )

            output_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            output_path = (
                output_dir
                / f"{ticker}.json"
            )

            with output_path.open(
                "w",
                encoding="utf-8",
            ) as file:

                json.dump(
                    result,
                    file,
                    indent=2,
                    default=str,
                )

            result[
                "saved_to"
            ] = str(
                output_path
            )

        return result


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    ticker = "NVDA"

    print()
    print("=" * 80)
    print("MASTER INVESTMENT RESEARCH PIPELINE")
    print("=" * 80)

    try:

        result = (
            InvestmentResearchPipeline
            .analyse(
                ticker
            )
        )

        synthesis = result[
            "synthesis"
        ]

        audit = result[
            "audit"
        ]

        print()
        print("TICKER:")
        print(
            result[
                "ticker"
            ]
        )

        print()
        print("INVESTMENT CASE SCORE:")
        print(
            synthesis.get(
                "investment_case_score"
            )
        )

        print()
        print("CONCLUSION:")
        print(
            synthesis.get(
                "conclusion"
            )
        )

        print()
        print("DECISION:")
        print(
            result[
                "core"
            ][
                "decision"
            ].get(
                "decision"
            )
        )

        print()
        print("THESIS:")
        print(
            synthesis.get(
                "thesis_challenge"
            )
        )

        print()
        print("AUDIT:")
        print(
            audit.get(
                "status"
            )
        )

        print()
        print("AUDIT FINDINGS:")
        print(
            audit.get(
                "finding_count"
            )
        )

        print()
        print("SAVED:")
        print(
            result.get(
                "saved_to"
            )
        )

        print()
        print("=" * 80)
        print("MASTER INVESTMENT RESEARCH PIPELINE COMPLETE")
        print("=" * 80)

    except Exception as exc:

        print()
        print("=" * 80)
        print("PIPELINE ERROR")
        print("=" * 80)
        print(
            type(exc).__name__,
            str(exc),
        )
        raise
