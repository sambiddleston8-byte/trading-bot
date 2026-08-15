from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from core.application.portfolio_monitor_service import PortfolioMonitorService
from core.data_sources.provider_access import (
    ProviderAccessCoordinator,
    ProviderAccessPolicy,
    ProviderAttemptMetadata,
)
from core.data_sources.yahoo_fast_info_access import (
    YahooFastInfoAccessError,
    YahooFastInfoClient,
    YahooFastInfoObservation,
)
from core.data_sources.yahoo_history_access import YahooHistoryClient
from core.learning_engine import LearningEngine
from core.stock_universe import StockUniverse


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class MappingFastInfo(dict):
    """A payload exposing ``last_price`` only through mapping access."""


class AttributeFastInfo:
    """A payload exposing ``last_price`` the way ``yfinance`` does."""

    def __init__(self, value, reads):
        self._value = value
        self._reads = reads

    @property
    def last_price(self):
        self._reads.append("last_price")
        return self._value

    def get(self, key, default=None):
        raise AssertionError("attribute access must be preferred")


class Ticker:
    def __init__(self, responses, reads):
        self.responses = responses
        self.reads = reads

    @property
    def fast_info(self):
        self.reads.append("fast_info")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def reset_provider_state():
    ProviderAccessCoordinator.reset()
    yield
    ProviderAccessCoordinator.reset()


def client_for(*responses, interval=0):
    reads = []
    symbols = []
    clock = Clock()
    queue = list(responses)

    def factory(symbol):
        symbols.append(symbol)
        return Ticker(queue, reads)

    coordinator = ProviderAccessCoordinator.for_provider(
        "YAHOO_FINANCE_VIA_YFINANCE",
        "Yahoo Finance via yfinance",
        policy=ProviderAccessPolicy(
            minimum_interval_seconds=interval,
            maximum_attempts=1,
            base_backoff_seconds=0,
            failure_threshold=2,
            cooldown_seconds=60,
        ),
        clock=clock,
        sleep=clock.sleep,
    )
    return YahooFastInfoClient(ticker_factory=factory, coordinator=coordinator), symbols, reads, clock


def test_mapping_shape_yields_a_scalar_observation_with_false_evidence_flags():
    payload = MappingFastInfo({"last_price": 101.5, "currency": "USD"})
    client, symbols, reads, _ = client_for(payload)

    observation = client.last_price("aapl")

    assert isinstance(observation, YahooFastInfoObservation)
    assert observation.last_price == 101.5
    assert observation.provider == "YAHOO_FINANCE_VIA_YFINANCE"
    assert observation.authenticated is False
    assert observation.point_in_time is False
    assert observation.survivorship_safe is False
    assert observation.tradeable_quote is False
    assert observation.admissible_as_replay_evidence is False
    assert observation.access.attempts == 1
    assert observation.access.retry_count == 0
    assert observation.access.circuit_state == "CLOSED"
    assert symbols == ["aapl"]
    assert reads == ["fast_info"]


def test_attribute_shape_is_read_once_without_mapping_access():
    reads = []
    payload = AttributeFastInfo(12.25, reads)
    client, symbols, sdk_reads, _ = client_for(payload)

    assert client.last_price("^GSPC").last_price == 12.25
    assert symbols == ["^GSPC"]
    assert sdk_reads == ["fast_info"]
    assert reads == ["last_price"]


def test_integer_prices_are_accepted_as_positive_finite_scalars():
    client, _, _, _ = client_for(MappingFastInfo({"last_price": 7}))

    observation = client.last_price("AAPL")

    assert observation.last_price == 7.0
    assert isinstance(observation.last_price, float)


@pytest.mark.parametrize("symbol", ["", "../AAPL", "AAPL?token=secret", "A APL", "A" * 33])
def test_symbols_are_rejected_before_sdk_construction(symbol):
    client, symbols, reads, _ = client_for(MappingFastInfo({"last_price": 10.0}))

    with pytest.raises(ValueError, match="unsupported format"):
        client.last_price(symbol)

    assert symbols == []
    assert reads == []


@pytest.mark.parametrize(
    "payload",
    [
        MappingFastInfo({}),
        MappingFastInfo({"last_price": None}),
        MappingFastInfo({"last_price": True}),
        MappingFastInfo({"last_price": float("nan")}),
        MappingFastInfo({"last_price": float("inf")}),
        MappingFastInfo({"last_price": float("-inf")}),
        MappingFastInfo({"last_price": 0.0}),
        MappingFastInfo({"last_price": -1.0}),
        MappingFastInfo({"last_price": "101.5"}),
        MappingFastInfo({"last_price": [101.5]}),
        MappingFastInfo({"lastPrice": 101.5}),
        "not-a-payload",
        None,
        object(),
    ],
)
def test_missing_or_malformed_prices_fail_closed(payload):
    client, symbols, reads, _ = client_for(payload)

    with pytest.raises(YahooFastInfoAccessError, match="Yahoo current-price read failed") as caught:
        client.last_price("AAPL")

    assert caught.value.reason_code == "PRICE_UNAVAILABLE"
    assert caught.value.metadata.attempts == 1
    assert caught.value.metadata.circuit_state == "CLOSED"
    assert symbols == ["AAPL"]
    assert reads == ["fast_info"]


def test_observation_is_immutable_and_retains_no_provider_payload():
    payload = MappingFastInfo({"last_price": 55.0, "session_crumb": "never-expose"})
    client, _, _, _ = client_for(payload)

    observation = client.last_price("AAPL")

    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.last_price = 1.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.admissible_as_replay_evidence = True

    retained = [getattr(observation, field.name) for field in dataclasses.fields(observation)]
    assert all(
        isinstance(value, (float, bool, str, ProviderAttemptMetadata)) for value in retained
    )
    assert "session_crumb" not in repr(observation)
    assert "never-expose" not in repr(observation)


def test_sdk_error_is_not_retried_or_leaked():
    secret = "crumb=never-expose&cookie=secret"
    client, symbols, reads, _ = client_for(
        RuntimeError(secret), MappingFastInfo({"last_price": 10.0})
    )

    with pytest.raises(YahooFastInfoAccessError) as caught:
        client.last_price("AAPL")

    assert str(caught.value) == "Yahoo current-price read failed."
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.reason_code == "SDK_CALL_FAILURE"
    assert symbols == ["AAPL"]
    assert reads == ["fast_info"]


def test_repeated_failures_open_circuit_without_a_third_sdk_call():
    client, symbols, reads, _ = client_for(
        RuntimeError("provider failed"),
        RuntimeError("provider failed"),
        MappingFastInfo({"last_price": 10.0}),
    )

    with pytest.raises(YahooFastInfoAccessError) as first:
        client.last_price("AAPL")
    with pytest.raises(YahooFastInfoAccessError) as second:
        client.last_price("AAPL")
    with pytest.raises(YahooFastInfoAccessError) as third:
        client.last_price("AAPL")

    assert first.value.reason_code == "SDK_CALL_FAILURE"
    assert second.value.metadata.circuit_state == "OPEN"
    assert third.value.reason_code == "CIRCUIT_OPEN"
    assert third.value.metadata.attempts == 0
    assert symbols == ["AAPL", "AAPL"]
    assert reads == ["fast_info", "fast_info"]


def test_unavailable_prices_do_not_open_the_shared_circuit_or_block_history():
    price_client, _, _, clock = client_for(
        MappingFastInfo({}),
        MappingFastInfo({}),
        MappingFastInfo({}),
    )
    for symbol in ("OLD1", "OLD2", "OLD3"):
        with pytest.raises(YahooFastInfoAccessError) as caught:
            price_client.last_price(symbol)
        assert caught.value.reason_code == "PRICE_UNAVAILABLE"
        assert caught.value.metadata.circuit_state == "CLOSED"

    history_calls = []

    class HistoryTicker:
        def history(self, **kwargs):
            history_calls.append(kwargs)
            index = pd.DatetimeIndex(["2025-01-02"])
            return pd.DataFrame({"Close": [10.0]}, index=index)

    history_client = YahooHistoryClient(
        ticker_factory=lambda symbol: HistoryTicker(),
        coordinator=ProviderAccessCoordinator.for_provider(
            "YAHOO_FINANCE_VIA_YFINANCE",
            "Yahoo Finance via yfinance",
            policy=ProviderAccessPolicy(maximum_attempts=1),
            clock=clock,
            sleep=clock.sleep,
        ),
    )

    assert history_client.history("AAPL", period="1d").frame.iloc[-1]["Close"] == 10.0
    assert history_calls == [{"period": "1d"}]


def test_mixed_universe_screen_does_not_drop_a_later_valid_symbol(tmp_path):
    client, _, _, _ = client_for(
        MappingFastInfo({}),
        MappingFastInfo({}),
        MappingFastInfo({}),
        MappingFastInfo({"last_price": 10.0}),
    )
    universe = StockUniverse(tmp_path / "universe.json", price_client=client)

    result = universe.validate(["OLD1", "OLD2", "OLD3", "AAPL"])

    assert result == {
        "Valid": ["AAPL"],
        "Invalid": ["OLD1", "OLD2", "OLD3"],
    }


def test_price_and_history_clients_share_caller_entry_pacing():
    price_client, _, _, clock = client_for(MappingFastInfo({"last_price": 10.0}), interval=1)
    history_calls = []

    class HistoryTicker:
        def history(self, **kwargs):
            history_calls.append(kwargs)
            index = pd.DatetimeIndex(["2025-01-02"])
            return pd.DataFrame({"Close": [10.0]}, index=index)

    history_client = YahooHistoryClient(
        ticker_factory=lambda symbol: HistoryTicker(),
        coordinator=ProviderAccessCoordinator.for_provider(
            "YAHOO_FINANCE_VIA_YFINANCE",
            "Yahoo Finance via yfinance",
            policy=ProviderAccessPolicy(minimum_interval_seconds=1, maximum_attempts=1),
            clock=clock,
            sleep=clock.sleep,
        ),
    )

    price_client.last_price("AAPL")
    history_client.history("MSFT", period="1d")

    assert clock.sleeps == [1.0]
    assert len(history_calls) == 1


class FakePriceClient:
    def __init__(self, price=None, error=None):
        self.price = price
        self.error = error
        self.calls = []

    def last_price(self, symbol):
        self.calls.append(symbol)
        if self.error is not None:
            raise self.error
        return YahooFastInfoObservation(
            last_price=self.price,
            access=ProviderAttemptMetadata(
                provider="Yahoo Finance via yfinance",
                attempts=1,
                retry_count=0,
                retried_status_codes=(),
                total_wait_seconds=0.0,
                elapsed_seconds=0.0,
                circuit_state="CLOSED",
            ),
        )

    def __bool__(self):
        return False


class FakeHistoryClient:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def history(self, symbol, **kwargs):
        self.calls.append((symbol, kwargs))
        return type("Observation", (), {"frame": self.frame.copy(deep=True)})()


def price_frame():
    index = pd.DatetimeIndex(["2025-01-02", "2025-01-03"], name="Date")
    return pd.DataFrame({"Close": [10.0, 11.0], "Volume": [100, 110]}, index=index)


def test_monitor_reads_an_uppercase_symbol_through_the_injected_boundary(monkeypatch):
    monkeypatch.setattr(
        "core.application.portfolio_monitor_service.YahooFastInfoClient",
        lambda: pytest.fail("an injected price client must not construct a real client"),
    )
    fake = FakePriceClient(price=204.5)

    assert PortfolioMonitorService.current_price("aapl", price_client=fake) == 204.5
    assert fake.calls == ["AAPL"]


def test_monitor_returns_none_when_the_price_boundary_fails():
    fake = FakePriceClient(
        error=YahooFastInfoAccessError(
            "Yahoo current-price read failed.",
            reason_code="SDK_CALL_FAILURE",
            metadata=ProviderAttemptMetadata(
                provider="Yahoo Finance via yfinance",
                attempts=1,
                retry_count=0,
                retried_status_codes=(),
                total_wait_seconds=0.0,
                elapsed_seconds=0.0,
                circuit_state="CLOSED",
            ),
        )
    )

    assert PortfolioMonitorService.current_price("AAPL", price_client=fake) is None
    assert fake.calls == ["AAPL"]


def test_stock_validation_is_true_only_for_a_validated_price(tmp_path):
    valid = FakePriceClient(price=10.0)
    invalid = FakePriceClient(error=RuntimeError("boundary failed"))

    assert StockUniverse(tmp_path / "universe.json", price_client=valid).validate_symbol("AAPL")
    assert (
        StockUniverse(tmp_path / "universe.json", price_client=invalid).validate_symbol("AAPL")
        is False
    )
    assert valid.calls == ["AAPL"]
    assert invalid.calls == ["AAPL"]


def test_learning_prefers_the_price_boundary_and_skips_its_history_fallback(tmp_path):
    price = FakePriceClient(price=204.5)
    history = FakeHistoryClient(price_frame())

    engine = LearningEngine(
        tmp_path / "learning.json",
        history_client=history,
        price_client=price,
    )

    assert engine.get_current_price("AAPL") == 204.5
    assert price.calls == ["AAPL"]
    assert history.calls == []


def test_learning_falls_back_to_the_existing_one_day_history_boundary(tmp_path):
    price = FakePriceClient(error=RuntimeError("boundary failed"))
    history = FakeHistoryClient(price_frame())

    engine = LearningEngine(
        tmp_path / "learning.json",
        history_client=history,
        price_client=price,
    )

    assert engine.get_current_price("AAPL") == 11.0
    assert price.calls == ["AAPL"]
    assert history.calls == [("AAPL", {"period": "1d"})]


def test_learning_returns_none_when_both_boundaries_fail(tmp_path):
    class FailingHistoryClient:
        def history(self, symbol, **kwargs):
            raise RuntimeError("boundary failed")

    engine = LearningEngine(
        tmp_path / "learning.json",
        history_client=FailingHistoryClient(),
        price_client=FakePriceClient(error=RuntimeError("boundary failed")),
    )

    assert engine.get_current_price("AAPL") is None
