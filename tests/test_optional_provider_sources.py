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
        assert FinancialModelingPrepSource().historical_sp500_constituent_changes()["status"] == "NOT_CONFIGURED"
        assert FinancialModelingPrepSource().historical_nasdaq_constituent_changes()["status"] == "NOT_CONFIGURED"
        assert FinancialModelingPrepSource().delisted_companies()["status"] == "NOT_CONFIGURED"
        assert FinancialModelingPrepSource().dividend_adjusted_prices("NVDA", start="2025-01-01", end="2025-01-31")["status"] == "NOT_CONFIGURED"
        assert FinancialModelingPrepSource().splits("NVDA")["status"] == "NOT_CONFIGURED"
        assert FinancialModelingPrepSource().symbol_changes()["status"] == "NOT_CONFIGURED"
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


def test_fmp_uses_authentication_header_and_never_query_string_key():
    captured = {}

    class Response:
        ok = True

        @staticmethod
        def json():
            return []

    class Session:
        @staticmethod
        def get(url, **kwargs):
            captured.update(url=url, **kwargs)
            return Response()

    original = os.environ.get("FMP_API_KEY")
    os.environ["FMP_API_KEY"] = "secret-test-key"
    try:
        result = FinancialModelingPrepSource(session=Session()).analyst_estimates("nvda")
    finally:
        if original is None:
            os.environ.pop("FMP_API_KEY", None)
        else:
            os.environ["FMP_API_KEY"] = original

    assert result["status"] == "COMPLETE"
    assert captured["headers"] == {"apikey": "secret-test-key"}
    assert captured["params"]["symbol"] == "NVDA"
    assert "apikey" not in captured["params"]
    assert "secret-test-key" not in captured["url"]


def test_fmp_capability_methods_have_bounded_parameters():
    calls = []

    class Response:
        ok = True

        @staticmethod
        def json():
            return []

    class Session:
        @staticmethod
        def get(url, **kwargs):
            calls.append((url, kwargs["params"]))
            return Response()

    original = os.environ.get("FMP_API_KEY")
    os.environ["FMP_API_KEY"] = "test-key"
    source = FinancialModelingPrepSource(session=Session())
    try:
        source.historical_sp500_constituent_changes()
        source.historical_nasdaq_constituent_changes()
        source.delisted_companies(page=2, limit=50)
        source.dividend_adjusted_prices("aapl", start="2024-01-01", end="2024-01-31")
        source.splits("aapl")
        source.symbol_changes()
    finally:
        if original is None:
            os.environ.pop("FMP_API_KEY", None)
        else:
            os.environ["FMP_API_KEY"] = original

    assert [url.rsplit("/stable/", 1)[1] for url, _ in calls] == [
        "historical-sp500-constituent",
        "historical-nasdaq-constituent",
        "delisted-companies",
        "historical-price-eod/dividend-adjusted",
        "splits",
        "symbol-change",
    ]
    assert calls[2][1] == {"page": 2, "limit": 50}
    assert calls[3][1] == {"from": "2024-01-01", "to": "2024-01-31", "symbol": "AAPL"}


if __name__ == "__main__":
    test_optional_sources_do_not_make_network_calls_without_credentials()
    test_provider_registry_never_exposes_keys()
    test_successful_provider_response_has_a_retrieval_timestamp()
    print("OPTIONAL PROVIDER SOURCE TESTS PASSED")
