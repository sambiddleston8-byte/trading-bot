from core.data_sources.optional_provider_sources import OptionalProviderError
from scripts.assess_eodhd_capabilities import assess


def complete(count=5):
    return {
        "status": "COMPLETE",
        "sample_record_count": count,
        "sample_field_names": ["Code", "StartDate"],
    }


class Source:
    def historical_sp500_membership_summary(self):
        return complete()

    def delisted_symbol_eod_capability(self):
        return complete()


def test_available_capabilities_remain_partial_and_non_authoritative():
    result = assess(Source())
    assert result["status"] == "CANDIDATE_CAPABILITIES_AVAILABLE"
    assert result["capability_probe_passed"] is True
    for field in (
        "historical_nasdaq_membership_provided",
        "complete_terminal_outcomes_provided",
        "corrections_and_revisions_provided",
        "terms_approved",
        "provider_qualified",
        "dataset_admitted",
        "replay_executed",
        "performance_claim_allowed",
        "live_trading_enabled",
    ):
        assert result[field] is False


def test_provider_failure_is_reported_without_false_qualification():
    source = Source()
    source.historical_sp500_membership_summary = lambda: (_ for _ in ()).throw(
        OptionalProviderError("EODHD capability unavailable")
    )
    result = assess(source)
    assert result["status"] == "NOT_AVAILABLE"
    assert result["capability_probe_passed"] is False
    assert result["checks"][0]["status"] == "UNAVAILABLE"


def test_empty_response_does_not_pass():
    source = Source()
    source.delisted_symbol_eod_capability = lambda: complete(0)
    result = assess(source)
    assert result["status"] == "NOT_AVAILABLE"
    assert result["checks"][1]["status"] == "EMPTY_RESPONSE"
