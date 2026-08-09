from core.research.thesis_challenger import ThesisChallenger


def investigation(
    forecast_confidence="MEDIUM",
    net_debt_to_fcf=1.0,
    news=None,
):
    result = ThesisChallenger.build(
        ticker="TEST",
        fundamentals={
            "net_debt": 100.0,
            "fcf_margin": 0.35,
            "roic": 0.25,
            "balance_sheet": {
                "net_debt_to_fcf": net_debt_to_fcf,
            },
        },
        valuation={"expected_return": 0.20},
        expectations={
            "forward_revenue_growth": 0.10,
            "forward_eps_growth": 0.10,
            "forecast_confidence": forecast_confidence,
        },
    )
    result["catalysts"] = {
        "positive_score": 2,
        "negative_score": 0,
    }
    result["data_quality"] = {
        "unresolved_discrepancies": 0,
    }
    result["news"] = news or {"evidence": []}
    return result


def test_ordinary_uncertainty_does_not_weaken_a_thesis():
    result = ThesisChallenger.calculate_result(
        ThesisChallenger.populate_findings(investigation())
    )

    assert result["overall_challenge_result"] == "THESIS_SURVIVES"
    assert result["thesis_survives"] is True


def test_high_leverage_still_weakens_a_thesis():
    result = ThesisChallenger.calculate_result(
        ThesisChallenger.populate_findings(
            investigation(net_debt_to_fcf=3.5)
        )
    )

    assert result["overall_challenge_result"] == "THESIS_WEAKENED"


def test_neutral_keyword_news_is_not_a_material_negative():
    result = ThesisChallenger.calculate_result(
        ThesisChallenger.populate_findings(
            investigation(
                news={
                    "evidence": [
                        {
                            "headline": "Interest rates remain broadly stable",
                            "impact": "NEUTRAL",
                        },
                    ],
                },
            )
        )
    )

    macro = next(
        item
        for item in result["challenges"]
        if item["area"] == "macro"
    )

    assert macro["thesis_impact"] == "NEUTRAL"
    assert result["overall_challenge_result"] == "THESIS_SURVIVES"


if __name__ == "__main__":
    test_ordinary_uncertainty_does_not_weaken_a_thesis()
    test_high_leverage_still_weakens_a_thesis()
    test_neutral_keyword_news_is_not_a_material_negative()

    print("THESIS CHALLENGER CALIBRATION TESTS PASSED")
