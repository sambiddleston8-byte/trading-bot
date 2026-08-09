from core.research_engine import ResearchEngine


def test_ticker_or_company_named_headlines_are_retained():
    assert ResearchEngine.headline_is_relevant(
        "AAPL reports quarterly earnings",
        "AAPL",
        "Apple Inc.",
    )
    assert ResearchEngine.headline_is_relevant(
        "Apple launches a new product line",
        "AAPL",
        "Apple Inc.",
    )


def test_unrelated_headlines_are_rejected():
    assert not ResearchEngine.headline_is_relevant(
        "AMD reports data-centre revenue growth",
        "AAPL",
        "Apple Inc.",
    )
    assert not ResearchEngine.headline_is_relevant(
        "Markets rise on economic data",
        "A",
        "Agilent Technologies, Inc.",
    )
    assert ResearchEngine.headline_is_relevant(
        "Agilent Technologies announces guidance",
        "A",
        "Agilent Technologies, Inc.",
    )


if __name__ == "__main__":
    test_ticker_or_company_named_headlines_are_retained()
    test_unrelated_headlines_are_rejected()
    print("NEWS RELEVANCE FILTER TESTS PASSED")
