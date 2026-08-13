from core.data_sources.optional_provider_sources import OptionalProviderError
from scripts.assess_fmp_capabilities import assess


def complete(payload):
    return {"status": "COMPLETE", "payload": payload}


class Source:
    def historical_sp500_constituent_changes(self):
        return complete([{"date": "x"}])

    def historical_nasdaq_constituent_changes(self):
        return complete([{"date": "x"}])

    def delisted_companies(self, *, limit):
        assert limit == 5
        return complete([{"symbol": "X"}])

    def as_reported_financials(self, symbol):
        assert symbol == "AAPL"
        return complete([{"date": "x"}])

    def dividend_adjusted_prices(self, symbol, *, start, end):
        assert (symbol, start, end) == ("AAPL", "2024-01-01", "2024-01-10")
        return complete([{"date": "x", "adjClose": 1}])

    def splits(self, symbol):
        assert symbol == "AAPL"
        return complete([{"date": "x"}])

    def symbol_changes(self):
        return complete([{"oldSymbol": "X"}])


def test_all_available_still_does_not_claim_qualification_or_authority():
    result = assess(Source())
    assert result["status"] == "CANDIDATE_CAPABILITIES_AVAILABLE"
    assert result["critical_probe_passed"] is True
    assert result["provider_qualified"] is False
    assert result["dataset_admitted"] is False
    assert result["replay_executed"] is False
    assert result["performance_claim_allowed"] is False
    assert result["live_trading_enabled"] is False


def test_inaccessible_membership_feed_fails_replay_qualification():
    source = Source()
    source.historical_sp500_constituent_changes = lambda: (_ for _ in ()).throw(
        OptionalProviderError(
            "Financial Modeling Prep capability is unavailable for the configured account (HTTP 402)."
        )
    )
    result = assess(source)
    assert result["status"] == "NOT_REPLAY_QUALIFIED"
    assert result["critical_probe_passed"] is False
    check = next(
        item for item in result["checks"]
        if item["capability"] == "HISTORICAL_SP500_MEMBERSHIP_CHANGES"
    )
    assert check["status"] == "UNAVAILABLE"
    assert "402" in check["reason"]


def test_empty_critical_response_does_not_pass():
    source = Source()
    source.symbol_changes = lambda: complete([])
    result = assess(source)
    assert result["status"] == "NOT_REPLAY_QUALIFIED"
    assert result["critical_probe_passed"] is False
