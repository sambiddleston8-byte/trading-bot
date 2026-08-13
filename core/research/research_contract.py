from __future__ import annotations

import math


class ResearchContract:
    """
    Stable, downstream-facing schema for pipeline results.

    The research engines may retain their own detailed schemas.
    The scanner and portfolio layers consume this normalized form.
    """

    VERSION = "1.4"

    @staticmethod
    def mapping(value):

        return value if isinstance(value, dict) else {}

    @staticmethod
    def number(value):

        try:

            if value is None:

                return None

            resolved = float(value)

            return resolved if math.isfinite(resolved) else None

        except (
            TypeError,
            ValueError,
        ):

            return None

    @classmethod
    def first_value(
        cls,
        *values,
    ):

        for value in values:

            if value is not None:

                return value

        return None

    @classmethod
    def first_number(
        cls,
        *values,
    ):

        for value in values:

            number = cls.number(value)

            if number is not None:

                return number

        return None

    @classmethod
    def from_pipeline_result(
        cls,
        result,
    ):

        result = cls.mapping(
            result
        )

        # Rebuild the downstream record from the saved source result every
        # time.  Older research files may contain a canonical snapshot from a
        # previous schema or recommendation policy; trusting it would leave
        # the portfolio layer using stale decisions after an engine upgrade.
        # The raw result remains the source of truth.

        core = cls.mapping(
            result.get(
                "core"
            )
        )

        decision = cls.mapping(
            core.get(
                "decision"
            )
        )

        decision_valuation = cls.mapping(
            decision.get(
                "valuation"
            )
        )

        legacy_valuation = cls.mapping(
            core.get(
                "valuation"
            )
        )

        valuation_quality = cls.mapping(
            core.get(
                "valuation_quality"
            )
        )

        decision_confidence = cls.mapping(
            decision.get(
                "confidence"
            )
        )

        legacy_forecast_validation = cls.mapping(
            legacy_valuation.get(
                "Forecast Validation"
            )
        )

        intrinsic_values = cls.mapping(
            legacy_valuation.get(
                "Intrinsic Value"
            )
        )

        legacy_returns = cls.mapping(
            legacy_valuation.get(
                "Expected Return"
            )
        )

        research = cls.mapping(
            result.get(
                "research"
            )
        )

        market_signals = cls.mapping(
            research.get(
                "market_signals"
            )
        )

        market_technical = cls.mapping(
            market_signals.get(
                "technical"
            )
        )

        market_risk = cls.mapping(
            market_signals.get(
                "risk"
            )
        )

        sentiment = cls.mapping(
            research.get(
                "sentiment"
            )
        )

        specialist_research = cls.mapping(
            research.get(
                "specialist_research"
            )
        )

        market_regime = cls.mapping(
            research.get(
                "market_regime"
            )
        )

        macro_environment = cls.mapping(
            research.get(
                "macro_environment"
            )
        )

        thesis_source = cls.mapping(
            research.get(
                "thesis_challenge"
            )
        )

        provider_evidence = cls.mapping(
            research.get(
                "provider_evidence_summary"
            )
        )

        thesis_summary = cls.mapping(
            thesis_source.get(
                "summary"
            )
        )

        synthesis = cls.mapping(
            result.get(
                "synthesis"
            )
        )

        synthesis_sentiment = cls.mapping(
            synthesis.get(
                "sentiment"
            )
        )

        synthesis_market_context = cls.mapping(
            synthesis.get(
                "market_context"
            )
        )

        monitoring_conditions = synthesis.get(
            "what_would_change_our_mind"
        )
        if not isinstance(monitoring_conditions, list):
            monitoring_conditions = []

        synthesis_market_regime = cls.mapping(
            synthesis_market_context.get(
                "market_regime"
            )
        )

        synthesis_macro_environment = cls.mapping(
            synthesis_market_context.get(
                "macro_environment"
            )
        )

        audit = cls.mapping(
            result.get(
                "audit"
            )
        )

        master_decision = cls.mapping(
            result.get(
                "master_decision"
            )
        )

        diagnostics = cls.mapping(
            result.get(
                "diagnostics"
            )
        )

        fundamental = cls.mapping(
            core.get(
                "fundamental"
            )
        )

        thesis_result = cls.first_value(
            thesis_source.get(
                "overall_challenge_result"
            ),
            thesis_source.get(
                "result"
            ),
            thesis_summary.get(
                "overall_challenge_result"
            ),
            thesis_summary.get(
                "result"
            ),
        )

        thesis_survives = cls.first_value(
            thesis_source.get(
                "thesis_survives"
            ),
            thesis_summary.get(
                "thesis_survives"
            ),
        )

        if thesis_survives is None:

            thesis_survives = (
                thesis_result
                == "THESIS_SURVIVES"
            )

        return {
            "contract_version":
                cls.VERSION,

            "ticker":
                result.get(
                    "ticker"
                ),

            "research_status":
                (
                    "COMPLETE"
                    if result.get(
                        "status"
                    )
                    == "COMPLETE"
                    else "ERROR"
                ),

            "investment_case_score":
                cls.first_number(
                    synthesis.get(
                        "investment_case_score"
                    ),
                    synthesis.get(
                        "score"
                    ),
                    result.get(
                        "investment_case_score"
                    ),
                ),

            "decision":
                cls.first_value(
                    synthesis.get(
                        "decision"
                    ),
                    decision.get(
                        "decision"
                    ),
                    result.get(
                        "decision"
                    ),
                ),

            "decision_reason":
                cls.first_value(
                    synthesis.get(
                        "decision_reason"
                    ),
                    result.get(
                        "decision_reason"
                    ),
                ),

            "data_as_of":
                cls.first_value(
                    result.get("data_as_of"),
                    result.get("completed_at"),
                ),

            "research_git_revision":
                cls.first_value(
                    result.get("source_git_revision"),
                    "UNKNOWN",
                ),

            "bull_case":
                synthesis.get("bull_case"),

            "bear_case":
                synthesis.get("bear_case"),

            "catalysts":
                synthesis.get("catalysts") or [],

            "current_price":
                cls.first_number(
                    decision_valuation.get(
                        "current_price"
                    ),
                    legacy_valuation.get(
                        "Current Price"
                    ),
                    legacy_valuation.get(
                        "current_price"
                    ),
                    result.get(
                        "current_price"
                    ),
                ),

            "base_intrinsic_value":
                cls.first_number(
                    decision_valuation.get(
                        "base_intrinsic_value"
                    ),
                    intrinsic_values.get(
                        "Base"
                    ),
                    legacy_valuation.get(
                        "base_intrinsic_value"
                    ),
                    result.get(
                        "base_intrinsic_value"
                    ),
                ),

            "expected_return":
                cls.first_number(
                    decision_valuation.get(
                        "expected_return"
                    ),
                    legacy_returns.get(
                        "Base"
                    ),
                    legacy_valuation.get(
                        "expected_return"
                    ),
                    result.get(
                        "expected_return"
                    ),
                ),

            "annualised_expected_return":
                cls.first_number(
                    decision_valuation.get(
                        "annualised_expected_return"
                    ),
                    legacy_returns.get(
                        "Annualised"
                    ),
                    legacy_returns.get(
                        "annualised"
                    ),
                ),

            "valuation_horizon_years":
                cls.first_number(
                    decision_valuation.get(
                        "valuation_horizon_years"
                    ),
                    decision_valuation.get(
                        "horizon_years"
                    ),
                    legacy_returns.get(
                        "Horizon Years"
                    ),
                    legacy_valuation.get(
                        "Forecast Years"
                    ),
                ),

            "valuation_input_consistency":
                cls.first_value(
                    decision_confidence.get("estimate_consistency"),
                    legacy_forecast_validation.get("estimate_consistency"),
                    legacy_forecast_validation.get(
                        "Overall Confidence"
                    ),
                ),

            "forecast_accuracy_status":
                cls.first_value(
                    decision_confidence.get("forecast_accuracy"),
                    legacy_forecast_validation.get("forecast_accuracy_status"),
                    "UNCALIBRATED_NO_REALISED_OUTCOME_EVIDENCE",
                ),

            "valuation_quality":
                valuation_quality,

            "thesis": {
                "result":
                    thesis_result,

                "tested":
                    cls.first_number(
                        thesis_source.get(
                            "tested"
                        ),
                        thesis_summary.get(
                            "tested"
                        ),
                    )
                    or 0,

                "material_negative":
                    cls.first_number(
                        thesis_source.get(
                            "material_negative"
                        ),
                        thesis_summary.get(
                            "material_negative"
                        ),
                    )
                    or 0,

                "thesis_survives":
                    thesis_survives,
            },

            "audit": {
                "status":
                    audit.get(
                        "status"
                    ),

                "finding_count":
                    audit.get(
                        "finding_count",
                        0,
                    ),

                "critical":
                    audit.get(
                        "critical",
                        0,
                    ),

                "high":
                    audit.get(
                        "high",
                        0,
                    ),

                "medium":
                    audit.get(
                        "medium",
                        0,
                    ),

                "findings":
                    audit.get(
                        "findings",
                        [],
                    ),
            },

            "data_quality":
                fundamental.get(
                    "data_quality",
                    {},
                ),

            "market_signals": {
                "technical_score":
                    cls.first_number(
                        market_technical.get(
                            "score"
                        ),
                    ),

                "momentum_score":
                    cls.first_number(
                        market_technical.get(
                            "momentum_score"
                        ),
                    ),

                "moving_average_score":
                    cls.first_number(
                        market_technical.get(
                            "moving_average_score"
                        ),
                    ),

                "drawdown_score":
                    cls.first_number(
                        market_technical.get(
                            "drawdown_score"
                        ),
                    ),

                "trend_persistence_score":
                    cls.first_number(
                        market_technical.get(
                            "trend_persistence_score"
                        ),
                    ),

                "support_resistance_score":
                    cls.first_number(
                        market_technical.get(
                            "support_resistance_score"
                        ),
                    ),

                "volume_confirmation_score":
                    cls.first_number(
                        market_technical.get(
                            "volume_confirmation_score"
                        ),
                    ),

                "return_20d":
                    cls.first_number(
                        market_technical.get(
                            "return_20d"
                        ),
                    ),

                "return_60d":
                    cls.first_number(
                        market_technical.get(
                            "return_60d"
                        ),
                    ),

                "return_120d":
                    cls.first_number(
                        market_technical.get(
                            "return_120d"
                        ),
                    ),

                "return_252d":
                    cls.first_number(
                        market_technical.get(
                            "return_252d"
                        ),
                    ),

                "drawdown_from_252d_high":
                    cls.first_number(
                        market_technical.get(
                            "drawdown_from_252d_high"
                        ),
                    ),

                "support_level":
                    cls.first_number(
                        market_technical.get(
                            "support_level"
                        ),
                    ),

                "resistance_level":
                    cls.first_number(
                        market_technical.get(
                            "resistance_level"
                        ),
                    ),

                "volume_ratio_20d_to_60d":
                    cls.first_number(
                        market_technical.get(
                            "volume_ratio_20d_to_60d"
                        ),
                    ),

                "nearest_fibonacci_level":
                    market_technical.get(
                        "nearest_fibonacci_level"
                    ),

                "distance_to_nearest_fibonacci_level":
                    cls.first_number(
                        market_technical.get(
                            "distance_to_nearest_fibonacci_level"
                        ),
                    ),

                "risk_score":
                    cls.first_number(
                        market_risk.get(
                            "score"
                        ),
                    ),

                "beta":
                    cls.first_number(
                        market_risk.get(
                            "beta"
                        ),
                    ),

                "annualised_volatility":
                    cls.first_number(
                        market_risk.get(
                            "annualised_volatility"
                        ),
                    ),

                "downside_volatility":
                    cls.first_number(
                        market_risk.get(
                            "downside_volatility"
                        ),
                    ),

                "maximum_drawdown":
                    cls.first_number(
                        market_risk.get(
                            "maximum_drawdown"
                        ),
                    ),

                "debt_to_cash":
                    cls.first_number(
                        market_risk.get(
                            "debt_to_cash"
                        ),
                    ),

                "risk_components":
                    market_risk.get(
                        "components",
                        [],
                    ),
            },

            "sentiment": {
                "score": cls.first_number(
                    sentiment.get("score"),
                    synthesis_sentiment.get("score"),
                ),
                "label": cls.first_value(
                    sentiment.get("label"),
                    synthesis_sentiment.get("label"),
                ),
                "confidence": cls.first_value(
                    sentiment.get("confidence"),
                    synthesis_sentiment.get("confidence"),
                ),
                "independent_source_count": cls.first_number(
                    sentiment.get("independent_source_count"),
                    synthesis_sentiment.get("independent_source_count"),
                ) or 0,
            },

            "specialist_research":
                specialist_research,

            "provider_evidence":
                provider_evidence,

            "master_decision":
                master_decision,

            "diagnostics":
                diagnostics,

            "monitoring_conditions":
                monitoring_conditions,

            "market_context": {
                "market_regime": {
                    "status": cls.first_value(
                        market_regime.get("status"),
                        synthesis_market_regime.get("status"),
                    ),
                    "regime": cls.first_value(
                        market_regime.get("regime"),
                        synthesis_market_regime.get("regime"),
                    ),
                    "score": cls.first_number(
                        market_regime.get("score"),
                        synthesis_market_regime.get("score"),
                    ),
                },
                "macro_environment": {
                    "status": cls.first_value(
                        macro_environment.get("status"),
                        synthesis_macro_environment.get("status"),
                    ),
                    "regime": cls.first_value(
                        macro_environment.get("regime"),
                        synthesis_macro_environment.get("regime"),
                    ),
                    "policy_rate": cls.first_number(
                        macro_environment.get("policy_rate"),
                        synthesis_macro_environment.get("policy_rate"),
                    ),
                    "inflation_yoy": cls.first_number(
                        macro_environment.get("inflation_yoy"),
                        synthesis_macro_environment.get("inflation_yoy"),
                    ),
                    "real_gdp_yoy": cls.first_number(
                        macro_environment.get("real_gdp_yoy"),
                        synthesis_macro_environment.get("real_gdp_yoy"),
                    ),
                },
            },
        }
