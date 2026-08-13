from core.research.valuation_quality_engine import ValuationQualityEngine


def assumption_evidence():
    values = {
        "risk_free_rate": 0.04,
        "equity_risk_premium": 0.05,
        "beta": 1.0,
        "pre_tax_cost_of_debt": 0.05,
        "marginal_tax_rate": 0.20,
        "market_value_equity_weight": 0.75,
        "market_value_debt_weight": 0.25,
        "terminal_growth_rate": 0.025,
    }
    return {
        name: {
            "value": value,
            "effective_at": "2025-01-01T00:00:00+00:00",
            "publicly_available_at": "2025-01-02T00:00:00+00:00",
            "retrieved_at": "2025-01-03T00:00:00+00:00",
            "source_uri": f"https://evidence.example/{name}",
            "source_input_sha256": f"{index:x}" * 64,
            "source_locator": f"$.{name}",
        }
        for index, (name, value) in enumerate(values.items(), start=1)
    }


def valid_inputs():
    return (
        {
            "Current Price": 100,
            "Intrinsic Value": {"Base": 130},
            "Expected Return": {"Base": 0.30},
            "Forecast Validation": {"Overall Confidence": "HIGH"},
            "Terminal Value Contribution": 0.70,
            "Valuation Data Cutoff": "2025-01-04T00:00:00+00:00",
            "WACC": 0.0775,
            "Terminal Growth": 0.025,
            "DCF Assumption Evidence": assumption_evidence(),
            "DCF Sensitivity Matrix": {
                "wacc_values": [0.0675, 0.0775, 0.0875],
                "terminal_growth_values": [0.015, 0.025, 0.035],
            },
        },
        {"confidence": {"estimate_consistency": "HIGH"}},
    )


def test_schema_complete_dcf_still_fails_until_source_content_is_authenticated():
    valuation, decision = valid_inputs()
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["assessment"] == "FAIL"
    assert result["portfolio_expected_return_eligible"] is False
    assert any("authenticated" in item for item in result["failures"])


def test_unvalidated_forecast_fails_quality_check():
    valuation, decision = valid_inputs()
    decision["confidence"]["estimate_consistency"] = "INSUFFICIENT_DATA"
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["assessment"] == "FAIL"
    assert result["failures"]
    assert result["forecast_accuracy_status"] == (
        "UNCALIBRATED_NO_REALISED_OUTCOME_EVIDENCE"
    )


def test_terminal_dominance_is_visible_for_review():
    valuation, decision = valid_inputs()
    valuation["Terminal Value Contribution"] = 0.86
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["assessment"] == "FAIL"
    assert result["warnings"]


def test_coarse_defaults_are_review_only_and_not_portfolio_eligible():
    valuation, decision = valid_inputs()
    valuation.pop("DCF Assumption Evidence")
    valuation.pop("DCF Sensitivity Matrix")
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["assessment"] == "FAIL"
    assert result["portfolio_expected_return_eligible"] is False
    assert result["expected_return_status"] == (
        "SOURCE_CONTENT_AUTHENTICATION_REQUIRED_NOT_PORTFOLIO_ELIGIBLE"
    )


def test_reported_wacc_must_equal_evidence_derived_wacc():
    valuation, decision = valid_inputs()
    valuation["WACC"] = 0.09
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["assessment"] == "FAIL"
    assert any("WACC" in item for item in result["failures"])


def test_assumption_retrieved_after_valuation_cutoff_is_not_eligible():
    valuation, decision = valid_inputs()
    valuation["DCF Assumption Evidence"]["beta"]["retrieved_at"] = (
        "2025-01-05T00:00:00+00:00"
    )
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["assessment"] == "FAIL"
    assert result["portfolio_expected_return_eligible"] is False


def test_rate_sanity_and_substantive_sensitivity_grid_are_required():
    valuation, decision = valid_inputs()
    valuation["DCF Assumption Evidence"]["beta"]["value"] = 50
    result = ValuationQualityEngine.assess(valuation, decision)
    assert any("beta" in item for item in result["failures"])
    valuation, decision = valid_inputs()
    valuation["DCF Sensitivity Matrix"] = {
        "wacc_values": [0.0775], "terminal_growth_values": [0.025]
    }
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["sensitivity_matrix_complete"] is False
    assert any("three" in item for item in result["failures"])


if __name__ == "__main__":
    test_validated_dcf_passes_quality_check()
    test_unvalidated_forecast_fails_quality_check()
    test_terminal_dominance_is_visible_for_review()
    print("VALUATION QUALITY TESTS PASSED")
