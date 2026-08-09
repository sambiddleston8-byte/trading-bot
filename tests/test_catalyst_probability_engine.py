from core.research.catalyst_probability_engine import CatalystProbabilityEngine


def test_primary_factual_evidence_is_stronger_than_an_unsupported_headline():
    unsupported = CatalystProbabilityEngine.assess(
        {"category": "product", "evidence": []}
    )
    supported = CatalystProbabilityEngine.assess(
        {
            "category": "product",
            "independent_source_count": 2,
            "evidence": [
                {
                    "evidence_type": "FACT",
                    "source_tier": 1,
                    "underlying_source": "COMPANY_IR",
                },
                {
                    "evidence_type": "FACT",
                    "source_tier": 2,
                    "underlying_source": "REGULATOR",
                },
            ],
        }
    )

    assert unsupported["probability"] < supported["probability"]
    assert unsupported["confidence"] == "REVIEW"
    assert supported["confidence"] == "MEDIUM"


def test_scheduled_earnings_event_is_not_treated_as_a_known_outcome():
    result = CatalystProbabilityEngine.assess(
        {
            "category": "earnings",
            "expected_date": "2026-09-01",
            "evidence": [
                {
                    "evidence_type": "FACT",
                    "source_tier": 3,
                    "underlying_source": "YAHOO_EARNINGS_CALENDAR",
                }
            ],
        }
    )

    assert result["probability"] == 0.75
    assert result["probability"] < 1.0
    assert "outcome remains unknown" in result["basis"].lower()


if __name__ == "__main__":
    test_primary_factual_evidence_is_stronger_than_an_unsupported_headline()
    test_scheduled_earnings_event_is_not_treated_as_a_known_outcome()
    print("CATALYST PROBABILITY ENGINE TESTS PASSED")
