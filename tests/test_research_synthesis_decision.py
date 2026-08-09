from core.research.research_synthesis_engine import ResearchSynthesisEngine


def analysis(
    expected_return=0.30,
    thesis_result="THESIS_SURVIVES",
    unresolved_discrepancies=0,
):
    return {
        "ticker": "TEST",
        "scores": {
            "fundamental_quality": 85,
            "valuation": 85,
        },
        "fundamentals": {"drivers": ["strong economics"]},
        "valuation": {
            "expected_return": expected_return,
            "status": "ATTRACTIVE",
        },
        "catalysts": {
            "positive_score": 4,
            "negative_score": 0,
        },
        "thesis_challenge": {
            "overall_challenge_result": thesis_result,
            "challenge_count": 4,
            "material_negative": 0,
        },
        "validation": {"overall_confidence": "HIGH"},
        "data_quality": {
            "unresolved_discrepancies": unresolved_discrepancies,
        },
    }


def test_surviving_high_conviction_case_can_be_a_strong_buy():
    result = ResearchSynthesisEngine.synthesise(analysis())

    assert result["decision"] == "STRONG_BUY"
    assert result["decision_reason"]


def test_weakened_thesis_overrides_earlier_buy_signal():
    result = ResearchSynthesisEngine.synthesise(
        analysis(thesis_result="THESIS_WEAKENED")
    )

    assert result["decision"] == "WATCHLIST"


def test_unresolved_data_overrides_earlier_buy_signal():
    result = ResearchSynthesisEngine.synthesise(
        analysis(unresolved_discrepancies=1)
    )

    assert result["decision"] == "WATCHLIST"


def test_materially_negative_valuation_is_an_avoid():
    result = ResearchSynthesisEngine.synthesise(
        analysis(expected_return=-0.20)
    )

    assert result["decision"] == "AVOID"


if __name__ == "__main__":
    test_surviving_high_conviction_case_can_be_a_strong_buy()
    test_weakened_thesis_overrides_earlier_buy_signal()
    test_unresolved_data_overrides_earlier_buy_signal()
    test_materially_negative_valuation_is_an_avoid()

    print("RESEARCH SYNTHESIS DECISION TESTS PASSED")
