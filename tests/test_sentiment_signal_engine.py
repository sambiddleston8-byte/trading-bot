from core.research.sentiment_signal_engine import SentimentSignalEngine


def news(positive=0, negative=0, independent=0, confidence="REVIEW"):
    return {
        "status": "COMPLETE",
        "summary": {
            "evidence_count": positive + negative,
            "positive": positive,
            "negative": negative,
            "independent_source_count": independent,
            "quality": {"confidence": confidence},
        },
    }


def test_positive_independent_evidence_creates_positive_signal():
    result = SentimentSignalEngine.analyse(news(positive=4, independent=2, confidence="HIGH"))

    assert result["label"] == "POSITIVE"
    assert result["score"] > 60


def test_repeated_unindependent_headlines_do_not_create_strong_signal():
    result = SentimentSignalEngine.analyse(news(positive=10, independent=0, confidence="HIGH"))

    assert result["label"] == "NEUTRAL"
    assert result["score"] == 50.0


def test_negative_evidence_creates_negative_signal():
    result = SentimentSignalEngine.analyse(news(negative=3, independent=2, confidence="HIGH"))

    assert result["label"] == "NEGATIVE"
    assert result["score"] < 40


if __name__ == "__main__":
    test_positive_independent_evidence_creates_positive_signal()
    test_repeated_unindependent_headlines_do_not_create_strong_signal()
    test_negative_evidence_creates_negative_signal()
    print("SENTIMENT SIGNAL ENGINE TESTS PASSED")
