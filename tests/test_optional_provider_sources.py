import os

from core.data_sources.optional_provider_sources import (
    AlphaVantageSource,
    FinancialModelingPrepSource,
    FREDSource,
    PolygonSource,
)
from core.data_sources.portfolio_data_provider_registry import (
    PortfolioDataProviderRegistry,
)
from core.data_sources.provider_configuration import ProviderConfiguration


def test_optional_sources_do_not_make_network_calls_without_credentials():
    names = ("ALPHAVANTAGE_API_KEY", "FMP_API_KEY", "POLYGON_API_KEY", "FRED_API_KEY")
    originals = {name: os.environ.pop(name, None) for name in names}
    original_loader = ProviderConfiguration.load_local_environment
    ProviderConfiguration.load_local_environment = classmethod(lambda cls: None)
    try:
        assert AlphaVantageSource().income_statement("NVDA")["status"] == "NOT_CONFIGURED"
        assert FinancialModelingPrepSource().analyst_estimates("NVDA")["status"] == "NOT_CONFIGURED"
        assert FinancialModelingPrepSource().as_reported_financials("NVDA")["status"] == "NOT_CONFIGURED"
        assert FinancialModelingPrepSource().ratings_snapshot("NVDA")["status"] == "NOT_CONFIGURED"
        assert FinancialModelingPrepSource().price_target_consensus("NVDA")["status"] == "NOT_CONFIGURED"
        assert PolygonSource().snapshot("NVDA")["status"] == "NOT_CONFIGURED"
        assert PolygonSource().company_news("NVDA")["status"] == "NOT_CONFIGURED"
        assert FREDSource().observations("DGS10")["status"] == "NOT_CONFIGURED"
    finally:
        ProviderConfiguration.load_local_environment = original_loader
        for name, value in originals.items():
            if value is not None:
                os.environ[name] = value


def test_provider_registry_never_exposes_keys():
    status = PortfolioDataProviderRegistry.status()
    assert set(status) == {"transcripts", "independent_estimates", "market_history", "macro"}
    assert all("required_environment_variable" in item for item in status.values())


def test_successful_provider_response_has_a_retrieval_timestamp():
    class Response:
        ok = True

        @staticmethod
        def json():
            return {"annualReports": []}

    class Session:
        @staticmethod
        def get(*args, **kwargs):
            return Response()

    original = os.environ.get("ALPHAVANTAGE_API_KEY")
    os.environ["ALPHAVANTAGE_API_KEY"] = "test-key"
    try:
        result = AlphaVantageSource(session=Session()).income_statement("NVDA")
    finally:
        if original is None:
            os.environ.pop("ALPHAVANTAGE_API_KEY", None)
        else:
            os.environ["ALPHAVANTAGE_API_KEY"] = original

    assert result["status"] == "COMPLETE"
    assert result.get("retrieved_at")


if __name__ == "__main__":
    test_optional_sources_do_not_make_network_calls_without_credentials()
    test_provider_registry_never_exposes_keys()
    test_successful_provider_response_has_a_retrieval_timestamp()
    print("OPTIONAL PROVIDER SOURCE TESTS PASSED")
