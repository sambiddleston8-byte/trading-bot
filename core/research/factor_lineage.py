from __future__ import annotations

"""Declared ownership of factors in the active investment decision route."""

from typing import Any


FACTOR_LINEAGE_VERSION = "1.0-single-opportunity-score"
ACTIVE_FACTOR_LINEAGE = {
    "synthesis_opportunity_rank": {
        "role": "AUTHORITATIVE_RANKING_SCORE",
        "output": "investment_case_score",
        "factors": {
            "fundamental_quality": "core.fundamental.fundamental_score",
            "valuation_attractiveness": "core.decision.valuation.expected_return",
            "validated_catalyst_case": "research.catalysts",
            "adversarial_thesis": "research.thesis_challenge",
            "data_quality": "core.fundamental.data_quality",
        },
    },
    "master_eligibility": {
        "role": "GATE_AND_EXPLANATION_ONLY",
        "output": "master_decision.portfolio_recommendation",
        "factors": {
            "research_complete": "canonical.research_status",
            "audit_clear": "canonical.audit.status",
            "thesis_not_fatal": "canonical.thesis",
            "positive_expected_return": "canonical.expected_return",
            "estimate_inputs_present": "canonical.valuation_input_consistency",
            "valuation_quality_clear": "canonical.valuation_quality",
            "evidence_quality_floor": "master_decision.research_confidence",
        },
    },
    "portfolio_ranking": {
        "role": "PASS_THROUGH_AUTHORITATIVE_RANKING",
        "output": "portfolio_conviction",
        "factors": {
            "authoritative_opportunity": "master_decision.opportunity_score",
        },
    },
    "position_sizing": {
        "role": "RISK_AND_SIZE_ONLY_NOT_RANKING",
        "output": "position_weight",
        "factors": {
            "opportunity": "master_decision.opportunity_score",
            "evidence_quality": "master_decision.research_confidence.score",
            "expected_return": "canonical.expected_return",
            "volatility": "canonical.market_signals.annualised_volatility",
            "risk_quality": "canonical.market_signals.risk_score",
            "valuation_reliability": "canonical.valuation_quality",
        },
    },
    "decision_rating": {
        "role": "DISPLAY_ONLY_PASS_THROUGH",
        "output": "decision_rating.score",
        "factors": {
            "authoritative_opportunity": "master_decision.opportunity_score",
        },
    },
}


def active_factor_lineage_policy(lineage: Any = None) -> dict[str, Any]:
    resolved = ACTIVE_FACTOR_LINEAGE if lineage is None else lineage
    if not isinstance(resolved, dict) or set(resolved) != set(ACTIVE_FACTOR_LINEAGE):
        raise ValueError("Active factor lineage must declare every active calculation stage")
    authoritative = [
        name
        for name, stage in resolved.items()
        if isinstance(stage, dict) and stage.get("role") == "AUTHORITATIVE_RANKING_SCORE"
    ]
    if authoritative != ["synthesis_opportunity_rank"]:
        raise ValueError("Exactly one authoritative ranking score is permitted")
    ranking_sources = resolved["synthesis_opportunity_rank"].get("factors")
    if not isinstance(ranking_sources, dict) or len(set(ranking_sources.values())) != len(
        ranking_sources
    ):
        raise ValueError("Authoritative ranking factors must have distinct source lineages")
    expected_roles = {
        "master_eligibility": "GATE_AND_EXPLANATION_ONLY",
        "portfolio_ranking": "PASS_THROUGH_AUTHORITATIVE_RANKING",
        "position_sizing": "RISK_AND_SIZE_ONLY_NOT_RANKING",
        "decision_rating": "DISPLAY_ONLY_PASS_THROUGH",
    }
    if any(resolved[name].get("role") != role for name, role in expected_roles.items()):
        raise ValueError("Downstream stages cannot create another ranking score")
    return {
        "version": FACTOR_LINEAGE_VERSION,
        "status": "DECLARED_POLICY_VALID",
        "scope": "GLOBAL_SCORING_POLICY_NOT_PER_DECISION_PROVENANCE",
        "authoritative_ranking_stage": authoritative[0],
        "ranking_factor_count": len(ranking_sources),
        "downstream_ranking_recalculation_permitted": False,
        "lineage": resolved,
    }
