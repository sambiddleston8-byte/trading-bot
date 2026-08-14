import os

import pytest
import requests

from core.data_sources.optional_provider_sources import (
    AlphaVantageSource,
    EODHDSource,
    FinancialModelingPrepSource,
    FREDSource,
    PolygonSource,
)
from core.data_sources.provider_access import (
    ProviderAccessCoordinator,
    ProviderAccessPolicy,
)
from core.data_sources.portfolio_data_provider_registry import (
    PortfolioDataProviderRegistry,
)
from core.data_sources.provider_configuration import ProviderConfiguration


@pytest.fixture(autouse=True)
def reset_provider_access_state():
    """Keep process-local pacing and breaker state out of unrelated tests."""
    ProviderAccessCoordinator.reset()
    yield
    ProviderAccessCoordinator.reset()


def test_optional_sources_do_not_make_network_calls_without_credentials():
    names = (
        "ALPHAVANTAGE_API_KEY",
        "FMP_API_KEY",
        "POLYGON_API_KEY",
        "FRED_API_KEY",
        "EODHD_API_TOKEN",
    )
    originals = {name: os.environ.pop(name, None) for name in names}
    original_loader = ProviderConfiguration.load_local_environment
    ProviderConfiguration.load_local_environment = classmethod(lambda cls: None)
    try:
        assert AlphaVantageSource().income_statement("NVDA")["status"] == "NOT_CONFIGURED"
        assert AlphaVantageSource().listing_status_summary(
            as_of="2020-01-02", state="active"
        )["status"] == "NOT_CONFIGURED"
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
        assert EODHDSource().historical_sp500_membership_summary()["status"] == "NOT_CONFIGURED"
        assert EODHDSource().delisted_symbol_eod_capability()["status"] == "NOT_CONFIGURED"
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
    assert result["provider_access"]["attempts"] == 1
    assert result["provider_access"]["retry_count"] == 0
    assert result["provider_access"]["request_url_recorded"] is False
    assert "test-key" not in str(result["provider_access"])


def test_alpha_vantage_listing_status_returns_metadata_not_rows_or_key():
    captured = {}

    class Response:
        ok = True
        text = "symbol,name,exchange,assetType,ipoDate,delistingDate,status\nAAA,Example,NASDAQ,Stock,2010-01-01,null,Active\n"

    class Session:
        @staticmethod
        def get(url, **kwargs):
            captured.update(url=url, **kwargs)
            return Response()

    original = os.environ.get("ALPHAVANTAGE_API_KEY")
    os.environ["ALPHAVANTAGE_API_KEY"] = "secret-test-key"
    try:
        result = AlphaVantageSource(session=Session()).listing_status_summary(
            as_of="2020-01-02", state="active"
        )
    finally:
        if original is None:
            os.environ.pop("ALPHAVANTAGE_API_KEY", None)
        else:
            os.environ["ALPHAVANTAGE_API_KEY"] = original

    assert result["status"] == "COMPLETE"
    assert result["sample_record_count"] == 1
    assert result["sample_field_names"] == [
        "assetType", "delistingDate", "exchange", "ipoDate", "name", "status", "symbol"
    ]
    assert "payload" not in result
    assert "secret-test-key" not in str(result)
    assert captured["params"] == {
        "function": "LISTING_STATUS",
        "date": "2020-01-02",
        "state": "active",
        "apikey": "secret-test-key",
    }


def test_alpha_vantage_listing_status_rejects_unbounded_inputs():
    source = AlphaVantageSource()
    for as_of, state in (("2009-12-31", "active"), ("bad", "active"), ("2020-01-02", "all")):
        try:
            source.listing_status_summary(as_of=as_of, state=state)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid listing-status scope was accepted")


@pytest.mark.parametrize(
    "response,fragment",
    [
        (
            type("Response", (), {"ok": False, "status_code": 403, "text": "denied"})(),
            "unavailable",
        ),
        (
            type("Response", (), {"ok": True, "status_code": 200, "text": "Information,quota exceeded\n"})(),
            "rejected",
        ),
        (
            type("Response", (), {"ok": True, "status_code": 200, "text": "symbol,name\nAAA,Example\n"})(),
            "unexpected",
        ),
    ],
)
def test_alpha_vantage_listing_status_fails_closed_on_provider_responses(
    response, fragment
):
    class Session:
        @staticmethod
        def get(*args, **kwargs):
            return response

    original = os.environ.get("ALPHAVANTAGE_API_KEY")
    os.environ["ALPHAVANTAGE_API_KEY"] = "test-key"
    try:
        with pytest.raises(Exception, match=fragment):
            AlphaVantageSource(session=Session()).listing_status_summary(
                as_of="2020-01-02", state="active"
            )
    finally:
        if original is None:
            os.environ.pop("ALPHAVANTAGE_API_KEY", None)
        else:
            os.environ["ALPHAVANTAGE_API_KEY"] = original


def test_alpha_vantage_listing_status_sanitizes_network_failure():
    class Session:
        @staticmethod
        def get(*args, **kwargs):
            raise requests.ConnectionError("sensitive request details")

    original = os.environ.get("ALPHAVANTAGE_API_KEY")
    os.environ["ALPHAVANTAGE_API_KEY"] = "test-key"
    try:
        with pytest.raises(Exception, match="could not be reached") as error:
            AlphaVantageSource(session=Session()).listing_status_summary(
                as_of="2020-01-02", state="delisted"
            )
        assert error.value.__cause__ is None
        assert "sensitive request details" not in str(error.value)
        assert "test-key" not in str(error.value)
    finally:
        if original is None:
            os.environ.pop("ALPHAVANTAGE_API_KEY", None)
        else:
            os.environ["ALPHAVANTAGE_API_KEY"] = original


def test_alpha_vantage_csv_path_uses_shared_transient_retry_controls():
    good = type(
        "Response",
        (),
        {
            "ok": True,
            "status_code": 200,
            "headers": {},
            "text": (
                "symbol,name,exchange,assetType,ipoDate,delistingDate,status\n"
                "AAA,Example,NASDAQ,Stock,2010-01-01,null,Active\n"
            ),
        },
    )()
    unavailable = type(
        "Response",
        (),
        {"ok": False, "status_code": 503, "headers": {"Retry-After": "0"}, "text": ""},
    )()

    class Session:
        def __init__(self):
            self.responses = [unavailable, good]
            self.calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            return self.responses.pop(0)

    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    access = ProviderAccessCoordinator.for_provider(
        "ALPHA_CSV_TEST",
        "Alpha Vantage",
        policy=ProviderAccessPolicy(
            minimum_interval_seconds=0,
            base_backoff_seconds=0,
        ),
        clock=clock,
        sleep=clock.sleep,
    )
    session = Session()
    original = os.environ.get("ALPHAVANTAGE_API_KEY")
    os.environ["ALPHAVANTAGE_API_KEY"] = "test-key"
    try:
        result = AlphaVantageSource(session=session, access=access).listing_status_summary(
            as_of="2020-01-02", state="active"
        )
    finally:
        if original is None:
            os.environ.pop("ALPHAVANTAGE_API_KEY", None)
        else:
            os.environ["ALPHAVANTAGE_API_KEY"] = original

    assert session.calls == 2
    assert result["provider_access"]["retry_count"] == 1
    assert result["provider_access"]["retried_status_codes"] == [503]


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


def test_eodhd_probes_return_metadata_not_rows_prices_or_key():
    calls = []
    membership = {
        "0": {
            "Code": "AAA",
            "Name": "Example",
            "StartDate": "2020-01-01",
            "EndDate": None,
            "IsActiveNow": 1,
            "IsDelisted": 0,
        }
    }
    prices = [{
        "date": "2022-10-24", "open": 1, "high": 2, "low": 1,
        "close": 2, "adjusted_close": 2, "volume": 3,
    }]

    class Response:
        ok = True
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Session:
        @staticmethod
        def get(url, **kwargs):
            calls.append((url, kwargs["params"]))
            return Response(membership if "fundamentals" in url else prices)

    original = os.environ.get("EODHD_API_TOKEN")
    os.environ["EODHD_API_TOKEN"] = "secret-test-token"
    try:
        source = EODHDSource(session=Session())
        member_result = source.historical_sp500_membership_summary()
        price_result = source.delisted_symbol_eod_capability()
    finally:
        if original is None:
            os.environ.pop("EODHD_API_TOKEN", None)
        else:
            os.environ["EODHD_API_TOKEN"] = original

    for result in (member_result, price_result):
        assert result["status"] == "COMPLETE"
        assert result["sample_record_count"] == 1
        assert "payload" not in result
        assert "secret-test-token" not in str(result)
        assert "Example" not in str(result)
    assert price_result["symbol"] == "TWTR.US"
    assert calls == [
        (
            "https://eodhd.com/api/v1.1/fundamentals/GSPC.INDX",
            {
                "filter": "HistoricalTickerComponents",
                "api_token": "secret-test-token",
                "fmt": "json",
            },
        ),
        (
            "https://eodhd.com/api/eod/TWTR.US",
            {
                "from": "2022-10-24",
                "to": "2022-10-28",
                "api_token": "secret-test-token",
                "fmt": "json",
            },
        ),
    ]


@pytest.mark.parametrize(
    "method,payload",
    [
        ("historical_sp500_membership_summary", []),
        ("historical_sp500_membership_summary", {"0": {"Code": "AAA"}}),
        ("delisted_symbol_eod_capability", {}),
        ("delisted_symbol_eod_capability", [{"date": "2022-10-24"}]),
    ],
)
def test_eodhd_probes_fail_closed_on_unexpected_schema(method, payload):
    class Response:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return payload

    class Session:
        @staticmethod
        def get(*args, **kwargs):
            return Response()

    original = os.environ.get("EODHD_API_TOKEN")
    os.environ["EODHD_API_TOKEN"] = "test-token"
    try:
        with pytest.raises(Exception, match="unexpected"):
            getattr(EODHDSource(session=Session()), method)()
    finally:
        if original is None:
            os.environ.pop("EODHD_API_TOKEN", None)
        else:
            os.environ["EODHD_API_TOKEN"] = original


def test_query_authenticated_provider_network_failure_has_no_secret_cause():
    class Session:
        @staticmethod
        def get(*args, **kwargs):
            raise requests.ConnectionError("request contained secret-test-token")

    original = os.environ.get("EODHD_API_TOKEN")
    os.environ["EODHD_API_TOKEN"] = "secret-test-token"
    try:
        with pytest.raises(Exception, match="could not be reached") as error:
            EODHDSource(session=Session()).historical_sp500_membership_summary()
        assert error.value.__cause__ is None
        assert "secret-test-token" not in str(error.value)
    finally:
        if original is None:
            os.environ.pop("EODHD_API_TOKEN", None)
        else:
            os.environ["EODHD_API_TOKEN"] = original


if __name__ == "__main__":
    test_optional_sources_do_not_make_network_calls_without_credentials()
    test_provider_registry_never_exposes_keys()
    test_successful_provider_response_has_a_retrieval_timestamp()
    print("OPTIONAL PROVIDER SOURCE TESTS PASSED")
