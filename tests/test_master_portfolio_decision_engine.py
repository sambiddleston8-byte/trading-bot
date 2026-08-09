from core.research.master_portfolio_decision_engine import MasterPortfolioDecisionEngine


def complete_record():
    return {
        "research_status": "COMPLETE",
        "investment_case_score": 70,
        "expected_return": 0.30,
        "audit": {"status": "PASS", "medium": 0},
        "thesis": {"thesis_survives": True},
        "market_signals": {"technical_score": 65, "risk_score": 70},
        "sentiment": {"score": 62, "confidence": "HIGH"},
        "specialist_research": {"completed_count": 5, "requested_count": 5},
    }


def test_eligible_result_keeps_all_safeguards_visible():
    result = MasterPortfolioDecisionEngine.evaluate(
        complete_record(),
        catalysts={"positive_score": 4, "negative_score": 1},
    )
    assert result["portfolio_recommendation"] == "ELIGIBLE"
    assert result["hard_gate_reasons"] == []
    assert result["components"]["specialist_coverage"]["completed"] == 5
    assert result["opportunity_score"] < 100
    assert result["research_confidence"]["maximum_score"] == 100.0


def test_failed_evidence_cannot_be_overridden_by_positive_signals():
    record = complete_record()
    record["audit"] = {"status": "FAIL"}
    result = MasterPortfolioDecisionEngine.evaluate(record, catalysts={"positive_score": 100})
    assert result["portfolio_recommendation"] == "EXCLUDE"
    assert "Evidence audit" in result["hard_gate_reasons"][0]


def test_learning_is_neutral_without_closed_outcomes():
    result = MasterPortfolioDecisionEngine.evaluate(complete_record())
    assert result["components"]["learning"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert result["components"]["learning"]["adjustment"] == 0.0


def test_unvalidated_valuation_cannot_enter_the_portfolio():
    record = complete_record()
    record["valuation_confidence"] = "INSUFFICIENT_DATA"
    result = MasterPortfolioDecisionEngine.evaluate(record)
    assert result["portfolio_recommendation"] == "EXCLUDE"
    assert "Valuation forecast confidence" in result["hard_gate_reasons"][-1]


def test_moderately_terminal_sensitive_model_can_be_conditionally_eligible():
    record = complete_record()
    record["valuation_quality"] = {
        "status": "COMPLETE",
        "assessment": "REVIEW",
        "terminal_value_contribution": 0.80,
    }
    result = MasterPortfolioDecisionEngine.evaluate(record)
    assert result["portfolio_recommendation"] == "ELIGIBLE"
    assert result["research_confidence"]["score"] <= 72.0
    assert result["caution_reasons"]


def test_extremely_terminal_value_dominated_model_stays_on_watchlist():
    record = complete_record()
    record["valuation_quality"] = {
        "status": "COMPLETE",
        "assessment": "REVIEW",
        "terminal_value_contribution": 0.90,
    }
    result = MasterPortfolioDecisionEngine.evaluate(record)
    assert result["portfolio_recommendation"] == "WATCHLIST"


def test_mild_thesis_weakening_is_visible_but_not_a_automatic_rejection():
    record = complete_record()
    record["thesis"] = {
        "result": "THESIS_WEAKENED",
        "thesis_survives": False,
        "material_negative": 1,
    }
    result = MasterPortfolioDecisionEngine.evaluate(record)
    assert result["portfolio_recommendation"] == "ELIGIBLE"
    assert result["caution_reasons"]


def test_verified_supplementary_sources_improve_confidence_not_audit_gates():
    baseline = MasterPortfolioDecisionEngine.evaluate(complete_record())
    record = complete_record()
    record["provider_evidence"] = {
        "completed_source_count": 3,
        "independent_company_source_count": 2,
    }
    enriched = MasterPortfolioDecisionEngine.evaluate(record)

    assert enriched["research_confidence"]["score"] > baseline["research_confidence"]["score"]
    assert enriched["components"]["provider_evidence_adjustment"] == 2.0
    assert enriched["portfolio_recommendation"] == "ELIGIBLE"


def test_evidence_coverage_adjustments_are_bounded_and_visible():
    record = complete_record()
    record["specialist_research"] = {"completed_count": 1, "requested_count": 5}
    record["valuation_quality"] = {"assessment": "REVIEW"}
    result = MasterPortfolioDecisionEngine.evaluate(record)

    assert result["components"]["specialist_coverage_adjustment"] == -3.0
    assert result["components"]["valuation_reliability_adjustment"] == -3.0
    assert result["opportunity_score"] < 100


def test_raw_catalyst_headlines_do_not_change_the_master_decision():
    result = MasterPortfolioDecisionEngine.evaluate(
        complete_record(),
        catalysts={"positive_score": 100, "negative_score": 0},
    )
    assert result["components"]["catalyst_balance"] == 0.0


def test_validated_catalyst_evidence_changes_the_master_decision_modestly():
    result = MasterPortfolioDecisionEngine.evaluate(
        complete_record(),
        catalysts={
            "validated_catalysts": [
                {
                    "direction": "POSITIVE",
                    "impact": 8,
                    "probability": 0.72,
                    "validation": {"score": 80, "pricing": {"score": 50}},
                }
            ]
        },
    )
    assert 0.0 < result["components"]["catalyst_balance"] < 10.0


def test_risk_off_macro_context_is_visible_and_conservative():
    record = complete_record()
    record["market_context"] = {
        "market_regime": {"regime": "RISK_OFF"},
        "macro_environment": {"regime": "RESTRICTIVE"},
    }
    result = MasterPortfolioDecisionEngine.evaluate(record)
    assert result["components"]["market_context_adjustment"] == -3.0
    assert result["components"]["market_context_reasons"]


if __name__ == "__main__":
    test_eligible_result_keeps_all_safeguards_visible()
    test_failed_evidence_cannot_be_overridden_by_positive_signals()
    test_learning_is_neutral_without_closed_outcomes()
    test_unvalidated_valuation_cannot_enter_the_portfolio()
    test_moderately_terminal_sensitive_model_can_be_conditionally_eligible()
    test_extremely_terminal_value_dominated_model_stays_on_watchlist()
    test_mild_thesis_weakening_is_visible_but_not_a_automatic_rejection()
    test_verified_supplementary_sources_improve_confidence_not_audit_gates()
    test_evidence_coverage_adjustments_are_bounded_and_visible()
    test_raw_catalyst_headlines_do_not_change_the_master_decision()
    test_validated_catalyst_evidence_changes_the_master_decision_modestly()
    test_risk_off_macro_context_is_visible_and_conservative()
    print("MASTER PORTFOLIO DECISION TESTS PASSED")
