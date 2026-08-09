from __future__ import annotations

"""Bring the established specialist analysers into the modern research record.

The project already contains useful business-quality, management, moat, industry
and financial-intelligence analysers.  They previously lived only in the legacy
``DecisionEngine`` path, which meant the portfolio workflow could not display
their work alongside the evidence-audited research record.

This adapter deliberately shares the company context assembled by the
fundamental engine.  It does not make a second, inconsistent financial-data
request for the subject company, and the supplementary signals do not override
the evidence, valuation, or adversarial-thesis gates used for portfolio
eligibility.
"""

from importlib import import_module
from typing import Any


class SpecialistResearchEngine:
    """Run context-based specialist research safely and preserve its coverage."""

    VERSION = "1.1-complete-specialist-coverage"

    ANALYSERS = {
        "business_quality": ("bots.business_quality.analyser", "BusinessQualityAnalyser"),
        "financial_intelligence": (
            "bots.financial_intelligence.analyser",
            "FinancialIntelligenceAnalyser",
        ),
        "management": ("bots.management.analyser", "ManagementAnalyser"),
        "moat": ("bots.moat.analyser", "MoatAnalyser"),
        "industry": ("bots.industry.analyser", "IndustryAnalyser"),
        "earnings_events": ("bots.earnings.analyser", "EarningsAnalyser"),
        "competitor_comparison": (
            "bots.competitors.analyser",
            "CompetitorAnalyser",
        ),
    }

    DEFERRED_ANALYSES = {
        "management_transcript_review": (
            "Transcript-level management review requires an attributable transcript "
            "source and is not inferred from general company data."
        ),
    }

    @staticmethod
    def mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @classmethod
    def load_analyser(cls, definition: tuple[str, str]) -> type:
        module_name, class_name = definition
        module = import_module(module_name)
        return getattr(module, class_name)

    @classmethod
    def analyse(
        cls,
        context: Any,
        analyser_classes: dict[str, type] | None = None,
    ) -> dict[str, Any]:
        """Run supported analysers without allowing one to stop research.

        The raw output is saved so a holding can show the reasoning behind it.
        An error becomes visible coverage information rather than an invented
        neutral score.
        """

        analysers = analyser_classes or {
            name: cls.load_analyser(definition)
            for name, definition in cls.ANALYSERS.items()
        }
        signals: dict[str, dict[str, Any]] = {}
        completed = 0

        for name, analyser_class in analysers.items():
            try:
                result = analyser_class().analyse(context)
                if not isinstance(result, dict):
                    raise TypeError("Specialist analyser returned a non-object result.")
                signals[name] = {"status": "COMPLETE", "result": result}
                completed += 1
            except Exception as exc:
                signals[name] = {
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }

        return {
            "version": cls.VERSION,
            "status": "COMPLETE" if completed == len(analysers) else "PARTIAL",
            "completed_count": completed,
            "requested_count": len(analysers),
            "signals": signals,
            "deferred_analyses": cls.DEFERRED_ANALYSES,
        }
