from __future__ import annotations

"""Transparent master decision layer for portfolio construction.

This is deliberately a rules-based orchestrator, not a black-box score.  It
combines the specialist research already collected by the pipeline while
preserving the non-negotiable evidence, valuation and thesis safeguards.
"""

import math
from typing import Any

from core.portfolio.portfolio_confidence_engine import PortfolioConfidenceEngine
from core.research.catalyst_validation_engine import CatalystValidationEngine
from core.research.factor_lineage import active_factor_lineage_policy


class MasterPortfolioDecisionEngine:
    VERSION = "2.8-single-authoritative-opportunity-score"
    MAX_OPPORTUNITY_SCORE = 100.0
    MIN_ELIGIBLE_OPPORTUNITY = 65.0
    MIN_ELIGIBLE_CONFIDENCE = 60.0
    MIN_EXPECTED_RETURN = 0.05
    MAX_ELIGIBLE_EXPECTED_RETURN = 2.00
    MAX_TERMINAL_VALUE_FOR_CONDITIONAL_ELIGIBILITY = 0.85
    MAX_MILD_THESIS_NEGATIVES = 1

    @staticmethod
    def mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def number(value: Any) -> float | None:
        try:
            if value is None:
                return None
            resolved = float(value)
            return resolved if math.isfinite(resolved) else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def specialist_coverage(cls, specialist_research: Any) -> tuple[int, int]:
        research = cls.mapping(specialist_research)
        completed = int(cls.number(research.get("completed_count")) or 0)
        requested = int(cls.number(research.get("requested_count")) or 0)
        return completed, requested

    @classmethod
    def catalyst_balance(cls, catalysts: Any) -> float:
        """Return a bounded signal from material, validated event evidence.

        Raw news categorisation is useful for discovery but must not change an
        allocation on its own.  A catalyst contributes only after its evidence
        validation clears a minimum bar, and its impact is discounted by its
        evidence-led event probability and pricing uncertainty.
        """
        research = cls.mapping(catalysts)
        items = research.get("validated_catalysts")
        if not isinstance(items, list):
            return 0.0

        positive = 0.0
        negative = 0.0
        for raw in items:
            item = cls.mapping(raw)
            contribution = CatalystValidationEngine.validated_contribution(item)
            if contribution <= 0:
                continue
            direction = str(item.get("direction") or "").upper()
            if direction == "POSITIVE":
                positive += contribution
            elif direction == "NEGATIVE":
                negative += contribution

        return round(max(-10.0, min(10.0, positive - negative)), 2)

    @classmethod
    def market_context_adjustment(cls, record: Any) -> tuple[float, list[str]]:
        """Apply a small, visible downside overlay from broad market context.

        The company case remains primary.  This adjustment only prevents the
        model from ignoring a clearly restrictive macro or risk-off backdrop.
        """
        item = cls.mapping(record)
        context = cls.mapping(item.get("market_context"))
        market = cls.mapping(context.get("market_regime"))
        macro = cls.mapping(context.get("macro_environment"))
        adjustment = 0.0
        reasons: list[str] = []

        if str(market.get("regime") or "").upper() == "RISK_OFF":
            adjustment -= 2.0
            reasons.append("The S&P 500 market-regime signal is risk-off, so the opportunity score is reduced slightly.")
        macro_regime = str(macro.get("regime") or "").upper()
        if macro_regime == "CONTRACTIONARY":
            adjustment -= 2.0
            reasons.append("The macro snapshot is contractionary, so the opportunity score is reduced slightly.")
        elif macro_regime == "RESTRICTIVE":
            adjustment -= 1.0
            reasons.append("The macro snapshot is restrictive, so the opportunity score is reduced slightly.")
        return adjustment, reasons

    @classmethod
    def specialist_coverage_adjustment(cls, completed: int, requested: int) -> float:
        """Reward completed specialist work without rewarding empty scope.

        The adjustment is deliberately small: completing the requested review
        improves ranking, but cannot compensate for a failed audit or an
        unvalidated valuation.
        """
        if requested <= 0:
            return 0.0
        coverage = max(0.0, min(1.0, completed / requested))
        return round(max(-3.0, min(3.0, (coverage - 0.70) * 10.0)), 2)

    @classmethod
    def provider_evidence_adjustment(cls, provider_evidence: Any) -> float:
        """Give a small transparent credit for genuinely independent coverage."""
        evidence = cls.mapping(provider_evidence)
        independent_sources = cls.number(
            evidence.get("independent_company_source_count")
        ) or 0.0
        return round(max(0.0, min(2.0, independent_sources)), 2)

    @classmethod
    def valuation_reliability_adjustment(cls, valuation_quality: Any) -> float:
        quality = cls.mapping(valuation_quality)
        assessment = str(quality.get("assessment") or "").upper()
        confidence = str(quality.get("confidence") or "").upper()
        if assessment == "PASS" and confidence in {"HIGH", "VERY_HIGH"}:
            return 2.0
        if assessment == "PASS":
            return 1.0
        if assessment == "REVIEW":
            return -3.0
        return 0.0

    @classmethod
    def hard_gate_reasons(cls, record: Any) -> list[str]:
        item = cls.mapping(record)
        reasons: list[str] = []
        audit = cls.mapping(item.get("audit"))
        thesis = cls.mapping(item.get("thesis"))
        expected_return = cls.number(item.get("expected_return"))
        valuation_input_consistency = str(
            item.get("valuation_input_consistency") or ""
        ).upper()
        valuation_quality = cls.mapping(item.get("valuation_quality"))

        if item.get("research_status") != "COMPLETE":
            reasons.append("Research is incomplete.")
        if audit.get("status") not in {"PASS", "PASS_WITH_WARNINGS"}:
            reasons.append("Evidence audit has not cleared the research.")
        thesis_result = str(thesis.get("result") or "").upper()
        material_negative = cls.number(thesis.get("material_negative")) or 0
        if thesis_result in {"THESIS_REJECTED", "THESIS_INVALIDATED"} or material_negative >= 3:
            reasons.append("Adversarial thesis review found a fatal investment-case issue.")
        if expected_return is None:
            reasons.append("Expected return is unavailable.")
        elif expected_return <= 0:
            reasons.append("Expected return is not positive after valuation assumptions.")
        if valuation_input_consistency in {"INSUFFICIENT_DATA", "UNAVAILABLE"}:
            reasons.append("Valuation estimate consistency is not sufficient for portfolio use.")
        if valuation_quality.get("assessment") == "FAIL":
            reasons.append("Valuation quality review has not cleared the model for portfolio use.")
        if valuation_quality.get("portfolio_expected_return_eligible") is False:
            reasons.append(
                "DCF expected return lacks complete point-in-time assumption and sensitivity evidence."
            )
        return reasons

    @classmethod
    def calibrate_opportunity_score(cls, raw_score: float) -> float:
        """Map the ranking signal onto a genuine 0-100 scale.

        The logistic curve approaches 100 as evidence strengthens but does
        not mechanically assign 100 to any company. This preserves the full
        scale without inventing certainty or imposing an arbitrary ceiling.
        """

        bounded_raw = max(-50.0, min(150.0, raw_score))
        value = cls.MAX_OPPORTUNITY_SCORE / (1.0 + math.exp(-(bounded_raw - 50.0) / 13.0))
        return round(value, 2)

    @classmethod
    def evaluate(
        cls,
        canonical: Any,
        catalysts: Any = None,
        learning_adjustment: Any = None,
    ) -> dict[str, Any]:
        """Give an auditable portfolio recommendation for one researched company.

        ``learning_adjustment`` is optional and must be supplied by a separate
        outcome-evaluation service.  It is bounded and disclosed; no learning
        result is invented when there is no closed paper-trade evidence.
        """
        item = cls.mapping(canonical)
        audit = cls.mapping(item.get("audit"))
        thesis = cls.mapping(item.get("thesis"))
        signals = cls.mapping(item.get("market_signals"))
        sentiment = cls.mapping(item.get("sentiment"))
        valuation_quality = cls.mapping(item.get("valuation_quality"))
        completed, requested = cls.specialist_coverage(item.get("specialist_research"))
        specialist_coverage_adjustment = cls.specialist_coverage_adjustment(
            completed,
            requested,
        )
        provider_evidence_adjustment = cls.provider_evidence_adjustment(
            item.get("provider_evidence")
        )
        valuation_reliability_adjustment = cls.valuation_reliability_adjustment(
            valuation_quality
        )
        market_context_adjustment, market_context_reasons = cls.market_context_adjustment(item)
        reasons = cls.hard_gate_reasons(item)

        base = cls.number(item.get("investment_case_score")) or 0.0
        expected_return = cls.number(item.get("expected_return")) or 0.0
        technical = cls.number(signals.get("technical_score"))
        risk = cls.number(signals.get("risk_score"))
        sentiment_score = cls.number(sentiment.get("score"))
        catalyst = cls.catalyst_balance(catalysts)
        thesis_result = str(thesis.get("result") or "").upper()
        material_negative = cls.number(thesis.get("material_negative")) or 0.0
        terminal_value = cls.number(valuation_quality.get("terminal_value_contribution"))
        caution_reasons: list[str] = []

        # Synthesis owns the only opportunity-ranking score. Everything here
        # is a gate or an explanation; re-adding the same economics would
        # manufacture extra votes from correlated evidence.
        raw_score = max(0.0, min(100.0, base))

        learning = cls.mapping(learning_adjustment)
        if learning.get("status") != "READY":
            learning = {"status": "INSUFFICIENT_EVIDENCE", "adjustment": 0.0}
        else:
            learning = {**learning, "adjustment_applied_to_ranking": False}

        opportunity = round(raw_score, 2)
        confidence_detail = PortfolioConfidenceEngine.evaluate(item)
        confidence = confidence_detail["score"]

        mild_thesis_weakening = (
            thesis_result == "THESIS_WEAKENED"
            and thesis.get("thesis_survives") is False
            and material_negative <= cls.MAX_MILD_THESIS_NEGATIVES
        )
        if mild_thesis_weakening:
            caution_reasons.append(
                "The thesis was weakened by a limited adverse finding; allocation is reduced and monitoring is required."
            )

        terminal_sensitive = (
            str(valuation_quality.get("assessment") or "").upper() == "REVIEW"
            and terminal_value is not None
            and terminal_value > 0.75
        )
        if terminal_sensitive and terminal_value <= cls.MAX_TERMINAL_VALUE_FOR_CONDITIONAL_ELIGIBILITY:
            caution_reasons.append(
                "The valuation is terminal-value sensitive; allocation is reduced and the model requires monitoring."
            )

        if reasons:
            recommendation = "EXCLUDE"
        elif expected_return < cls.MIN_EXPECTED_RETURN:
            recommendation = "WATCHLIST"
        elif expected_return > cls.MAX_ELIGIBLE_EXPECTED_RETURN:
            recommendation = "WATCHLIST"
            caution_reasons.append(
                "The valuation gap is unusually large and needs manual assumption review before allocation."
            )
        elif (
            terminal_sensitive
            and terminal_value > cls.MAX_TERMINAL_VALUE_FOR_CONDITIONAL_ELIGIBILITY
        ):
            recommendation = "WATCHLIST"
        elif thesis.get("thesis_survives") is not True and not mild_thesis_weakening:
            recommendation = "WATCHLIST"
        elif confidence < cls.MIN_ELIGIBLE_CONFIDENCE:
            recommendation = "WATCHLIST"
        elif opportunity >= cls.MIN_ELIGIBLE_OPPORTUNITY:
            recommendation = "ELIGIBLE"
        else:
            recommendation = "WATCHLIST"

        return {
            "version": cls.VERSION,
            "status": "COMPLETE",
            "portfolio_recommendation": recommendation,
            "opportunity_score": opportunity,
            "conviction_score": opportunity,
            "confidence": confidence,
            "research_confidence": confidence_detail,
            "hard_gate_reasons": reasons,
            "caution_reasons": caution_reasons,
            "components": {
                "factor_lineage_policy": active_factor_lineage_policy(),
                "investment_case_score": base,
                "expected_return": expected_return,
                "valuation_input_consistency": item.get("valuation_input_consistency"),
                "forecast_accuracy_status": item.get("forecast_accuracy_status"),
                "valuation_quality": valuation_quality,
                "technical_score": technical,
                "risk_score": risk,
                "sentiment_score": sentiment_score,
                "catalyst_balance": catalyst,
                "specialist_coverage": {"completed": completed, "requested": requested},
                "specialist_coverage_adjustment": specialist_coverage_adjustment,
                "provider_evidence_adjustment": provider_evidence_adjustment,
                "valuation_reliability_adjustment": valuation_reliability_adjustment,
                "market_context_adjustment": market_context_adjustment,
                "market_context_reasons": market_context_reasons,
                "learning": learning,
                "raw_opportunity_score": round(raw_score, 2),
                "downstream_ranking_adjustments_applied": False,
            },
        }
