from __future__ import annotations

"""Evidence-quality confidence for portfolio research.

This deliberately does not estimate the probability that a share price will
rise.  It answers a narrower and more defensible question: how complete,
independent and internally consistent is the research supporting the model's
view.  It uses the full 0–100 scale while retaining a small disclosed
uncertainty deduction rather than an arbitrary ceiling.
"""

from typing import Any


class PortfolioConfidenceEngine:
    VERSION = "1.2-true-hundred-point-evidence-scale"
    MAX_CONFIDENCE = 100.0
    RESIDUAL_UNCERTAINTY_DEDUCTION = 0.25

    @staticmethod
    def mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def number(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def evaluate(cls, record: Any) -> dict[str, Any]:
        item = cls.mapping(record)
        audit = cls.mapping(item.get("audit"))
        thesis = cls.mapping(item.get("thesis"))
        signals = cls.mapping(item.get("market_signals"))
        specialist = cls.mapping(item.get("specialist_research"))
        valuation_quality = cls.mapping(item.get("valuation_quality"))
        diagnostics = cls.mapping(item.get("diagnostics"))
        sentiment = cls.mapping(item.get("sentiment"))
        provider_evidence = cls.mapping(item.get("provider_evidence"))

        components: dict[str, float] = {}
        audit_status = str(audit.get("status") or "").upper()
        components["audit"] = {
            "PASS": 25.0,
            "PASS_WITH_WARNINGS": 18.0,
        }.get(audit_status, 0.0)

        completed = cls.number(specialist.get("completed_count")) or 0.0
        requested = cls.number(specialist.get("requested_count")) or 0.0
        coverage = 0.0 if requested <= 0 else min(1.0, completed / requested)
        components["specialist_coverage"] = 20.0 * coverage

        assessment = str(valuation_quality.get("assessment") or "").upper()
        valuation_status = str(valuation_quality.get("status") or "").upper()
        if assessment == "PASS":
            components["valuation_validation"] = 15.0
        elif assessment == "REVIEW" or valuation_status == "COMPLETE":
            components["valuation_validation"] = 6.0
        else:
            components["valuation_validation"] = 2.0

        technical = cls.number(signals.get("technical_score"))
        risk = cls.number(signals.get("risk_score"))
        components["market_coverage"] = 10.0 if technical is not None and risk is not None else 3.0

        if thesis.get("thesis_survives") is True:
            components["thesis_challenge"] = 12.0
        elif thesis.get("tested") or thesis.get("result"):
            components["thesis_challenge"] = 5.0
        else:
            components["thesis_challenge"] = 1.0

        independent_sources = cls.number(sentiment.get("independent_source_count")) or 0.0
        components["independent_news"] = min(6.0, independent_sources * 2.0)

        # Supplementary sources earn only a modest confidence credit and
        # cannot override a failed audit or valuation review.  This rewards
        # independently available estimates and market history without
        # pretending the raw provider response has been reconciled already.
        independent_company_sources = (
            cls.number(provider_evidence.get("independent_company_source_count"))
            or 0.0
        )
        completed_sources = (
            cls.number(provider_evidence.get("completed_source_count"))
            or 0.0
        )
        components["supplemental_provider_coverage"] = min(
            7.0,
            (2.0 * independent_company_sources)
            + max(0.0, completed_sources - independent_company_sources),
        )

        issues = diagnostics.get("issues")
        if not isinstance(issues, list):
            issues = []
        blockers = sum(
            1
            for issue in issues
            if isinstance(issue, dict) and str(issue.get("severity") or "").upper() == "BLOCKER"
        )
        components["diagnostic_cleanliness"] = 0.0 if blockers else max(1.0, 5.0 - len(issues))

        # The component framework can reach 100 when evidence is exceptionally
        # complete. A small residual deduction prevents the UI from claiming
        # that a research record is literally certain, without compressing the
        # scale behind an arbitrary 90/95-point maximum.
        raw_score = min(cls.MAX_CONFIDENCE, sum(components.values()))
        score = max(0.0, raw_score - cls.RESIDUAL_UNCERTAINTY_DEDUCTION)
        terminal_value = cls.number(valuation_quality.get("terminal_value_contribution"))
        if assessment == "REVIEW" and terminal_value is not None and terminal_value > 0.75:
            # A model dominated by terminal value may be useful for research,
            # but it cannot earn very-high evidence confidence without an
            # independently reconciled near-term forecast.
            score = min(score, 72.0)
        score = round(score, 1)
        if score >= 80:
            label = "VERY_STRONG"
        elif score >= 65:
            label = "STRONG"
        elif score >= 50:
            label = "MODERATE"
        else:
            label = "LIMITED"

        return {
            "version": cls.VERSION,
            "score": score,
            "maximum_score": cls.MAX_CONFIDENCE,
            "label": label,
            "components": {key: round(value, 1) for key, value in components.items()},
            "raw_score": round(raw_score, 2),
            "residual_uncertainty_deduction": cls.RESIDUAL_UNCERTAINTY_DEDUCTION,
            "note": "Evidence-quality confidence only; it is not a probability or certainty of investment return. A small residual uncertainty deduction prevents a false exact 100/100 certainty score.",
        }
