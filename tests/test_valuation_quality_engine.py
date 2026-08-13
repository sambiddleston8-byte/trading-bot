from core.research.valuation_quality_engine import ValuationQualityEngine


def valid_inputs():
    return (
        {
            "Current Price": 100,
            "Intrinsic Value": {"Base": 130},
            "Expected Return": {"Base": 0.30},
            "Forecast Validation": {"Overall Confidence": "HIGH"},
            "Terminal Value Contribution": 0.70,
        },
        {"confidence": {"estimate_consistency": "HIGH"}},
    )


def test_validated_dcf_passes_quality_check():
    valuation, decision = valid_inputs()
    result = ValuationQualityEngine.assess(valuation, decision)
    assert result["assessment"] == "PASS"


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
    assert result["assessment"] == "REVIEW"
    assert result["warnings"]


if __name__ == "__main__":
    test_validated_dcf_passes_quality_check()
    test_unvalidated_forecast_fails_quality_check()
    test_terminal_dominance_is_visible_for_review()
    print("VALUATION QUALITY TESTS PASSED")
