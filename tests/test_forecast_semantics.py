from core.investment_decision_engine import InvestmentDecisionEngine
from core.portfolio.portfolio_engine import PortfolioEngine
from core.validation.forecast_validator import ForecastValidator


def estimates(revenue_growth, eps_growth):
    revenue = {
        period: {"growth": revenue_growth, "numberOfAnalysts": 10}
        for period in ("0q", "+1q", "0y", "+1y")
    }
    earnings = {
        period: {"growth": eps_growth, "analysts": 10}
        for period in ("0q", "+1q", "0y", "+1y")
    }
    return revenue, earnings


def test_estimate_agreement_is_explicitly_not_forecast_accuracy():
    revenue, earnings = estimates(0.20, 0.20)
    result = ForecastValidator(revenue, earnings).build()
    assert result["estimate_consistency"] == "HIGH"
    assert result["forecast_accuracy_status"] == (
        "UNCALIBRATED_NO_REALISED_OUTCOME_EVIDENCE"
    )
    assert result["decision_score_multiplier_permitted"] is False
    assert "not forecast accuracy" in result["semantics"]


def test_estimate_consistency_cannot_change_forward_score():
    engine = InvestmentDecisionEngine()
    scores = {
        engine.forward_expectation_score(0.20, 0.15, consistency)
        for consistency in ("HIGH", "MEDIUM", "REVIEW", "LOW", None)
    }
    assert scores == {77.5}


def test_missing_estimates_remain_unavailable_not_low_accuracy():
    result = ForecastValidator({}, {}).build()
    assert result["estimate_consistency"] == "INSUFFICIENT_DATA"
    assert result["forecast_accuracy_status"] == (
        "UNCALIBRATED_NO_REALISED_OUTCOME_EVIDENCE"
    )


def test_estimate_consistency_cannot_change_portfolio_candidate_ranking():
    candidate = {
        "investment_case_score": 75,
        "expected_return": 0.20,
        "audit": {"status": "PASS", "medium": 0},
        "thesis": {"thesis_survives": True, "material_negative": 0},
        "decision": "BUY",
        "valuation_quality": {"assessment": "PASS"},
        "market_signals": {"risk_score": 70, "technical_score": 65},
        "sentiment": {"score": 50, "confidence": "LOW"},
        "provider_evidence": {},
    }
    scores = {
        PortfolioEngine.candidate_score(
            {**candidate, "valuation_input_consistency": consistency}
        )
        for consistency in ("HIGH", "MEDIUM", "REVIEW", "LOW", "UNAVAILABLE")
    }
    assert len(scores) == 1
