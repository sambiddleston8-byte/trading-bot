from core.data_sources.optional_provider_sources import OptionalProviderError
from scripts.assess_alpha_vantage_universe_capabilities import assess


class Source:
    def listing_status_summary(self, *, as_of, state):
        assert as_of == "2020-01-02"
        return {
            "status": "COMPLETE",
            "as_of": as_of,
            "state": state,
            "sample_record_count": 5,
            "sample_field_names": ["status", "symbol"],
        }


def test_available_listing_history_remains_partial_and_non_authoritative():
    result = assess(Source())
    assert result["status"] == "PARTIAL_UNIVERSE_CAPABILITY_AVAILABLE"
    assert result["historical_listing_probe_passed"] is True
    assert result["historical_index_membership_provided"] is False
    assert result["provider_qualified"] is False
    assert result["dataset_admitted"] is False
    assert result["replay_executed"] is False
    assert result["performance_claim_allowed"] is False
    assert result["live_trading_enabled"] is False


def test_provider_failure_is_reported_without_false_qualification():
    source = Source()
    source.listing_status_summary = lambda **kwargs: (_ for _ in ()).throw(
        OptionalProviderError("Alpha Vantage rejected the listing-status request or quota.")
    )
    result = assess(source)
    assert result["status"] == "NOT_AVAILABLE"
    assert result["historical_listing_probe_passed"] is False
    assert all(item["status"] == "UNAVAILABLE" for item in result["checks"])
