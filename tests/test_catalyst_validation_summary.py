from core.research.catalyst_validation_engine import CatalystValidationEngine


def validated(direction="POSITIVE", score=80, probability=0.7, impact=8, pricing=50):
    return {
        "direction": direction,
        "probability": probability,
        "impact": impact,
        "validation": {"score": score, "pricing": {"score": pricing}},
    }


def test_summary_excludes_discovery_events_without_sufficient_validation():
    result = CatalystValidationEngine.summary(
        [
            validated(),
            validated(score=40, direction="NEGATIVE"),
        ]
    )

    assert result["discovered_count"] == 2
    assert result["validated_count"] == 1
    assert result["positive_score"] > 0
    assert result["negative_score"] == 0


def test_already_priced_event_has_no_portfolio_catalyst_contribution():
    catalyst = validated(pricing=0)

    assert CatalystValidationEngine.validated_contribution(catalyst) == 0.0


if __name__ == "__main__":
    test_summary_excludes_discovery_events_without_sufficient_validation()
    test_already_priced_event_has_no_portfolio_catalyst_contribution()
    print("CATALYST VALIDATION SUMMARY TESTS PASSED")
