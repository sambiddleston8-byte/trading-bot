from core.research.outcome_learning_adapter import OutcomeLearningAdapter


def test_learning_stays_neutral_until_there_is_enough_closed_evidence():
    result = OutcomeLearningAdapter.evaluate("BUY", [{"Status": "CLOSED", "Recommendation": "BUY", "Actual Return": 10}])
    assert result["status"] == "INSUFFICIENT_EVIDENCE"
    assert result["adjustment"] == 0.0


def test_learning_uses_only_closed_matching_records():
    history = [
        {"Status": "CLOSED", "Recommendation": "BUY", "Actual Return": 10, "Correct": True}
        for _ in range(20)
    ]
    history.append({"Status": "OPEN", "Recommendation": "BUY", "Actual Return": -100, "Correct": False})
    result = OutcomeLearningAdapter.evaluate("BUY", history)
    assert result["status"] == "READY"
    assert result["observations"] == 20
    assert result["adjustment"] > 0


if __name__ == "__main__":
    test_learning_stays_neutral_until_there_is_enough_closed_evidence()
    test_learning_uses_only_closed_matching_records()
    print("OUTCOME LEARNING ADAPTER TESTS PASSED")
