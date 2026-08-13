from copy import deepcopy

import pytest

from core.portfolio.portfolio_engine import PortfolioEngine
from core.research import active_factor_lineage_policy
from core.research.factor_lineage import ACTIVE_FACTOR_LINEAGE
from core.research.master_portfolio_decision_engine import MasterPortfolioDecisionEngine


def canonical():
    return {
        "research_status": "COMPLETE",
        "investment_case_score": 72.5,
        "current_price": 100,
        "base_intrinsic_value": 130,
        "expected_return": 0.30,
        "audit": {"status": "PASS", "medium": 0},
        "thesis": {"result": "THESIS_SURVIVES", "thesis_survives": True},
        "market_signals": {"technical_score": 65, "risk_score": 70},
        "sentiment": {"score": 60, "confidence": "HIGH"},
        "specialist_research": {"completed_count": 5, "requested_count": 5},
        "valuation_input_consistency": "HIGH",
        "valuation_quality": {"assessment": "PASS"},
    }


def test_declares_exactly_one_authoritative_ranking_stage():
    result = active_factor_lineage_policy()
    assert result["status"] == "DECLARED_POLICY_VALID"
    assert result["scope"] == "GLOBAL_SCORING_POLICY_NOT_PER_DECISION_PROVENANCE"
    assert result["authoritative_ranking_stage"] == "synthesis_opportunity_rank"
    assert result["downstream_ranking_recalculation_permitted"] is False


def test_duplicate_ranking_lineage_or_second_authority_is_rejected():
    duplicate = deepcopy(ACTIVE_FACTOR_LINEAGE)
    duplicate["synthesis_opportunity_rank"]["factors"]["valuation_attractiveness"] = (
        duplicate["synthesis_opportunity_rank"]["factors"]["fundamental_quality"]
    )
    with pytest.raises(ValueError, match="distinct"):
        active_factor_lineage_policy(duplicate)

    second = deepcopy(ACTIVE_FACTOR_LINEAGE)
    second["portfolio_ranking"]["role"] = "AUTHORITATIVE_RANKING_SCORE"
    with pytest.raises(ValueError, match="Exactly one"):
        active_factor_lineage_policy(second)


def test_master_passes_through_synthesis_score_without_readding_correlated_inputs():
    base = canonical()
    expected = MasterPortfolioDecisionEngine.evaluate(base)
    assert expected["opportunity_score"] == 72.5
    assert expected["components"]["downstream_ranking_adjustments_applied"] is False
    assert expected["components"]["factor_lineage_policy"]["scope"] == (
        "GLOBAL_SCORING_POLICY_NOT_PER_DECISION_PROVENANCE"
    )

    variants = []
    for changes in (
        {"expected_return": 0.90},
        {"market_signals": {"technical_score": 5, "risk_score": 5}},
        {"sentiment": {"score": 95, "confidence": "VERY_HIGH"}},
        {"provider_evidence": {"independent_company_source_count": 5}},
        {"market_context": {"market_regime": {"regime": "RISK_OFF"}}},
    ):
        item = {**base, **changes}
        variants.append(MasterPortfolioDecisionEngine.evaluate(item)["opportunity_score"])
    assert variants == [72.5] * len(variants)


def test_portfolio_ranking_and_display_use_authoritative_score_only():
    def candidate(ticker, opportunity, confidence):
        return {
            **canonical(),
            "ticker": ticker,
            "master_decision": {
                "version": MasterPortfolioDecisionEngine.VERSION,
                "status": "COMPLETE",
                "portfolio_recommendation": "ELIGIBLE",
                "opportunity_score": opportunity,
                "conviction_score": opportunity,
                "confidence": confidence,
                "research_confidence": {"score": confidence},
                "hard_gate_reasons": [],
            },
        }

    prepared = PortfolioEngine.prepare_candidates(
        {"results": [candidate("HIGH", 80, 61), candidate("LOW", 70, 99)]}
    )
    assert [item["ticker"] for item in prepared] == ["HIGH", "LOW"]
    assert prepared[0]["portfolio_conviction"] == 80
    assert prepared[0]["decision_rating"] == {
        "score": 80.0,
        "maximum_score": 100.0,
        "label": "Opportunity score",
        "meaning": "Display-only pass-through of the single authoritative synthesis opportunity score; not a probability of return.",
        "formula": "NO_RECALCULATION",
        "source": "master_decision.opportunity_score",
        "display_only": True,
    }


def test_equal_authoritative_scores_have_deterministic_ticker_tiebreak():
    base = {
        **canonical(),
        "master_decision": {
            "version": MasterPortfolioDecisionEngine.VERSION,
            "status": "COMPLETE",
            "portfolio_recommendation": "ELIGIBLE",
            "opportunity_score": 75,
            "research_confidence": {"score": 75},
            "hard_gate_reasons": [],
        },
    }
    prepared = PortfolioEngine.prepare_candidates(
        {"results": [{**base, "ticker": "ZZZ"}, {**base, "ticker": "AAA"}]}
    )
    assert [item["ticker"] for item in prepared] == ["AAA", "ZZZ"]


def test_sizing_may_use_risk_and_confidence_without_changing_rank():
    base = {
        "portfolio_conviction": 75,
        "opportunity_score": 75,
        "expected_return": 0.20,
        "valuation_quality": {"assessment": "PASS"},
        "thesis": {"thesis_survives": True},
    }
    safer = {
        **base,
        "research_confidence": 90,
        "market_signals": {"annualised_volatility": 0.15, "risk_score": 90},
    }
    riskier = {
        **base,
        "research_confidence": 60,
        "market_signals": {"annualised_volatility": 0.50, "risk_score": 50},
    }
    assert PortfolioEngine.decision_rating_detail(safer)["score"] == 75
    assert PortfolioEngine.decision_rating_detail(riskier)["score"] == 75
    assert PortfolioEngine.position_sizing_signal(safer) > (
        PortfolioEngine.position_sizing_signal(riskier)
    )
