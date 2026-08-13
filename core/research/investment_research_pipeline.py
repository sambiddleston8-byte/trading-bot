from datetime import datetime, timezone
import json
from pathlib import Path
from core.decision_ledger import current_git_revision
from core.research.research_contract import ResearchContract


class InvestmentResearchPipeline:

    VERSION = "1.4-uncalibrated-forecast-semantics"

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

        from core.research.catalyst_probability_engine import (
            CatalystProbabilityEngine,
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

        from core.research.market_signal_engine import (
            MarketSignalEngine,
        )

        from core.research.sentiment_signal_engine import (
            SentimentSignalEngine,
        )

        from core.research.market_regime_engine import (
            MarketRegimeEngine,
        )

        from core.research.macro_environment_engine import (
            MacroEnvironmentEngine,
        )

        from core.research.specialist_research_engine import (
            SpecialistResearchEngine,
        )

        from core.research.master_portfolio_decision_engine import (
            MasterPortfolioDecisionEngine,
        )

        from core.research.outcome_learning_adapter import (
            OutcomeLearningAdapter,
        )

        from core.research.valuation_quality_engine import (
            ValuationQualityEngine,
        )

        from core.research.research_failure_diagnostics_engine import (
            ResearchFailureDiagnosticsEngine,
        )

        from core.research.supplemental_provider_evidence_service import (
            SupplementalProviderEvidenceService,
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

            "catalyst_probability":
                CatalystProbabilityEngine,

            "thesis":
                ThesisChallenger,

            "synthesis":
                ResearchSynthesisEngine,

            "audit":
                EvidenceAuditEngine,

            "market_signals":
                MarketSignalEngine,

            "sentiment":
                SentimentSignalEngine,

            "market_regime":
                MarketRegimeEngine,

            "macro_environment":
                MacroEnvironmentEngine,

            "specialist_research":
                SpecialistResearchEngine,

            "master_decision":
                MasterPortfolioDecisionEngine,

            "outcome_learning":
                OutcomeLearningAdapter,

            "valuation_quality":
                ValuationQualityEngine,

            "diagnostics":
                ResearchFailureDiagnosticsEngine,

            "supplemental_evidence": SupplementalProviderEvidenceService,
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

        fundamental_engine = engines["fundamental"]()

        fundamental = cls.call(
            fundamental_engine,
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

        valuation_quality = cls.call(
            engines["valuation_quality"],
            "assess",
            valuation,
            decision,
        )

        market_signals = cls.call(
            engines["market_signals"](),
            "analyse",
            ticker,
            context=getattr(fundamental_engine, "last_context", None),
        )

        specialist_research = cls.call(
            engines["specialist_research"],
            "analyse",
            context=getattr(fundamental_engine, "last_context", None),
        )

        return {
            "fundamental":
                fundamental,

            "valuation":
                valuation,

            "decision":
                decision,

            "valuation_quality":
                valuation_quality,

            "market_signals":
                market_signals,

            "specialist_research":
                specialist_research,
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

    @classmethod
    def analyse_sentiment(
        cls,
        news,
    ):

        return cls.load_engines()["sentiment"].analyse(news)

    @classmethod
    def analyse_market_context(cls):
        engines = cls.load_engines()

        return {
            "market_regime": engines["market_regime"]().analyse(),
            "macro_environment": engines["macro_environment"]().analyse(),
        }

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

                "estimate_consistency":
                    fundamental.get(
                        "forecast_validation",
                        {},
                    ).get(
                        "estimate_consistency",
                        fundamental.get("forecast_validation", {}).get(
                            "overall_confidence"
                        ),
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

        probability_engine = (
            cls.load_engines()
            ["catalyst_probability"]
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

                assessed = dict(catalyst)
                probability = probability_engine.assess(assessed)
                assessed["probability"] = probability["probability"]
                assessed["probability_assessment"] = probability

                validated.append(
                    validator.validate(
                        assessed
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

        validated_summary = validator.summary(validated)
        result["summary"] = validated_summary
        result["positive_score"] = validated_summary["positive_score"]
        result["negative_score"] = validated_summary["negative_score"]

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
        market_signals,
        sentiment,
        market_context,
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

            "sentiment":
                sentiment,

            "market_regime":
                market_context.get("market_regime", {}),

            "macro_environment":
                market_context.get("macro_environment", {}),

            "market_signals":
                market_signals,

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

        market_signals = core.get(
            "market_signals",
            {},
        )

        specialist_research = core.get(
            "specialist_research",
            {},
        )

        valuation_quality = core.get(
            "valuation_quality",
            {},
        )

        market_context = cls.analyse_market_context()

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

        sentiment = cls.analyse_sentiment(news)

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
                market_signals,
                sentiment,
                market_context,
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

        # Synthesis is the final recommendation because it is the first stage
        # to see valuation, catalysts, evidence quality, and the adversarial
        # thesis challenge together.  Audit this final recommendation rather
        # than the earlier core-only decision.
        synthesis_input = dict(synthesis_input)
        synthesis_input["decision"] = synthesis.get("decision")

        # ----------------------------------------------------
        # 8. FINAL EVIDENCE AUDIT
        # ----------------------------------------------------

        audit = cls.audit(
            synthesis_input
        )

        # ----------------------------------------------------
        # 9. FINAL RESULT
        # ----------------------------------------------------

        supplemental_evidence = cls.load_engines()["supplemental_evidence"].collect(ticker)

        result = {

            "ticker":
                ticker,

            "status":
                "COMPLETE",

            "pipeline_version":
                cls.VERSION,

            "source_git_revision":
                current_git_revision(Path(__file__).resolve().parents[2]),

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

                "valuation_quality":
                    valuation_quality,

            },

            "research": {

                "news":
                    news,

                "sentiment":
                    sentiment,

                "market_regime":
                    market_context.get("market_regime", {}),

                "macro_environment":
                    market_context.get("macro_environment", {}),

                "catalysts":
                    catalysts,

                "thesis_challenge":
                    thesis,

                "market_signals":
                    market_signals,

                "specialist_research":
                    specialist_research,

                "supplemental_provider_evidence": supplemental_evidence,

                "provider_evidence_summary": supplemental_evidence.get(
                    "summary",
                    {},
                ),

            },

            "synthesis":
                synthesis,

            "audit":
                audit,

            "synthesis_input":
                synthesis_input,

        }

        result["canonical"] = (
            ResearchContract
            .from_pipeline_result(
                result
            )
        )

        # The master decision layer combines all completed specialist work,
        # without permitting it to override the evidence audit, valuation,
        # risk, or adversarial-thesis gates.  It is saved separately so both
        # the portfolio and a holding's research page can show its reasoning.
        result["master_decision"] = (
            cls.load_engines()["master_decision"].evaluate(
                result["canonical"],
                catalysts=catalysts,
                learning_adjustment=(
                    cls.load_engines()["outcome_learning"].for_decision(
                        result["canonical"].get("decision")
                    )
                ),
            )
        )

        result["diagnostics"] = (
            cls.load_engines()["diagnostics"].analyse(
                result
            )
        )

        # Rebuild once more so the canonical portfolio-facing record contains
        # the master decision just calculated above.
        result["canonical"] = (
            ResearchContract
            .from_pipeline_result(
                result
            )
        )

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
