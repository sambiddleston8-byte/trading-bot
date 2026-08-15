from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from core.application.portfolio_market_exposure_service import PortfolioMarketExposureService
from core.backtest import BacktestEngine
from core.baseline_comparison import BaselineComparison
from core.benchmark_engine import BenchmarkEngine
from core.data import get_data, get_price
from core.data_sources.provider_access import ProviderAccessCoordinator, ProviderAccessPolicy
from core.data_sources.yahoo_history_access import (
    YahooHistoryAccessError,
    YahooHistoryClient,
    YahooHistoryObservation,
)
from core.expected_return_tracker import ExpectedReturnTracker
from core.history import HistoryEngine
from core.learning_engine import LearningEngine
from core.market_data_cache import MarketDataCache
from core.system_backtest import SystemBacktest
from core.universe_ranker import UniverseRanker


ROOT = Path(__file__).resolve().parents[1]
DIRECT_YFINANCE_IMPORT_ALLOWLIST = {
    "bots/competitors/analyser.py",
    "bots/earnings/analyser.py",
    "core/application/portfolio_monitor_service.py",
    "core/catalyst_engine.py",
    "core/data_sources/analyst_source.py",
    "core/data_sources/earnings_source.py",
    "core/data_sources/yahoo_history_access.py",
    "core/data_sources/yahoo_source.py",
    "core/financial_data.py",
    "core/learning_engine.py",
    "core/multi_factor_engine.py",
    "core/research/catalyst_engine.py",
    "core/research_engine.py",
    "core/stock_universe.py",
    "core/valuation_engine.py",
}
# `core/learning_engine.py` keeps yfinance only for its non-history
# `fast_info` current-price read; its historical reads cross this boundary.


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Ticker:
    def __init__(self, responses, calls):
        self.responses = responses
        self.calls = calls

    def history(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def price_frame(values=(10.0, 11.0)):
    index = pd.DatetimeIndex(["2025-01-02", "2025-01-03"], name="Date")
    return pd.DataFrame({"Close": list(values), "Volume": [100, 110]}, index=index)


@pytest.fixture(autouse=True)
def reset_provider_state():
    ProviderAccessCoordinator.reset()
    yield
    ProviderAccessCoordinator.reset()


def client_for(*responses, interval=0):
    calls = []
    symbols = []
    clock = Clock()

    def factory(symbol):
        symbols.append(symbol)
        return Ticker(queue, calls)

    queue = list(responses)
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
    return YahooHistoryClient(ticker_factory=factory, coordinator=coordinator), symbols, calls, clock


def test_history_observation_preserves_truth_boundary_and_copies_frame():
    source = price_frame()
    client, symbols, calls, _ = client_for(source)

    observation = client.history("aapl", period="1y", auto_adjust=False)
    source.loc[source.index[0], "Close"] = 999

    assert isinstance(observation, YahooHistoryObservation)
    assert list(observation.frame["Close"]) == [10.0, 11.0]
    assert observation.provider == "YAHOO_FINANCE_VIA_YFINANCE"
    assert observation.point_in_time is False
    assert observation.survivorship_safe is False
    assert observation.admissible_as_replay_evidence is False
    assert observation.access.attempts == 1
    assert observation.access.retry_count == 0
    assert symbols == ["aapl"]
    assert calls == [{"period": "1y", "auto_adjust": False}]


def test_date_bound_call_omits_unspecified_auto_adjust():
    client, _, calls, _ = client_for(price_frame())

    client.history("^GSPC", start="2025-01-01", end="2025-02-01")

    assert calls == [{"start": "2025-01-01", "end": "2025-02-01"}]


@pytest.mark.parametrize("symbol", ["", "../AAPL", "AAPL?token=secret", "A APL", "A" * 33])
def test_symbols_are_rejected_before_sdk_construction(symbol):
    client, symbols, calls, _ = client_for(price_frame())

    with pytest.raises(ValueError, match="unsupported format"):
        client.history(symbol, period="1y")

    assert symbols == []
    assert calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"period": "7y"},
        {"period": ["1y"]},
        {"period": "1y", "start": "2025-01-01"},
        {"start": "not-a-date"},
        {},
        {"start": "2025-01-01", "auto_adjust": "yes"},
    ],
)
def test_request_shapes_fail_before_sdk_call(kwargs):
    client, _, calls, _ = client_for(price_frame())

    with pytest.raises((TypeError, ValueError)):
        client.history("AAPL", **kwargs)

    assert calls == []


@pytest.mark.parametrize(
    "response",
    [
        {"Close": [10]},
        pd.DataFrame({"Open": [10]}, index=pd.DatetimeIndex(["2025-01-02"])),
        price_frame((10.0, float("nan"))),
        price_frame((10.0, float("inf"))),
        price_frame((10.0, 0.0)),
        price_frame((10.0, -1.0)),
        price_frame().sort_index(ascending=False),
    ],
)
def test_malformed_or_unsafe_price_frames_fail_closed(response):
    client, _, _, _ = client_for(response)

    with pytest.raises(YahooHistoryAccessError, match="read failed") as caught:
        client.history("AAPL", period="1y")

    assert caught.value.reason_code == "SDK_CALL_FAILURE"
    assert caught.value.metadata.attempts == 1


def test_empty_dataframe_remains_an_explicit_no_data_result():
    client, _, _, _ = client_for(pd.DataFrame())

    assert client.history("AAPL", period="1y").frame.empty


def test_sdk_error_is_not_retried_or_leaked():
    secret = "crumb=never-expose"
    client, symbols, calls, _ = client_for(RuntimeError(secret), price_frame())

    with pytest.raises(YahooHistoryAccessError, match="Yahoo history read failed") as caught:
        client.history("AAPL", period="1y")

    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert symbols == ["AAPL"]
    assert len(calls) == 1


def test_repeated_malformed_frames_open_circuit_without_third_sdk_call():
    client, symbols, calls, _ = client_for({"bad": True}, {"bad": True}, price_frame())

    with pytest.raises(YahooHistoryAccessError) as first:
        client.history("AAPL", period="1y")
    with pytest.raises(YahooHistoryAccessError) as second:
        client.history("AAPL", period="1y")
    with pytest.raises(YahooHistoryAccessError) as third:
        client.history("AAPL", period="1y")

    assert first.value.reason_code == "SDK_CALL_FAILURE"
    assert second.value.metadata.circuit_state == "OPEN"
    assert third.value.reason_code == "CIRCUIT_OPEN"
    assert symbols == ["AAPL", "AAPL"]
    assert len(calls) == 2


def test_separate_yahoo_clients_share_caller_entry_pacing():
    first, _, _, clock = client_for(price_frame(), interval=1)
    calls = []
    second = YahooHistoryClient(
        ticker_factory=lambda symbol: Ticker([price_frame()], calls),
        coordinator=ProviderAccessCoordinator.for_provider(
            "YAHOO_FINANCE_VIA_YFINANCE",
            "Yahoo Finance via yfinance",
            policy=ProviderAccessPolicy(minimum_interval_seconds=1, maximum_attempts=1),
            clock=clock,
            sleep=clock.sleep,
        ),
    )

    first.history("AAPL", period="1y")
    second.history("MSFT", period="1y")

    assert clock.sleeps == [1.0]
    assert len(calls) == 1


class FakeObservationClient:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def history(self, symbol, **kwargs):
        self.calls.append((symbol, kwargs))
        return type("Observation", (), {"frame": self.frame.copy(deep=True)})()


def test_migrated_legacy_callers_use_injected_history_boundary():
    fake = FakeObservationClient(price_frame())

    assert list(HistoryEngine(fake).get_history("AAPL", "1y")["Close"]) == [10.0, 11.0]
    assert list(get_data("AAPL", "1y", history_client=fake)["Close"]) == [10.0, 11.0]
    assert get_price("AAPL", history_client=fake) == 11.0
    assert BacktestEngine(fake).get_return("AAPL", "1y") == 10.0
    assert BenchmarkEngine("^GSPC", fake).get_return("2025-01-01", "2025-02-01") == 10.0
    assert BaselineComparison(history_client=fake).buy_and_hold_return(
        "AAPL", "2025-01-01", "2025-02-01"
    ) == pytest.approx(0.1)
    assert SystemBacktest(history_client=fake).get_prices(
        "AAPL", "2025-01-01", "2025-02-01"
    ) is not None
    assert fake.calls == [
        ("AAPL", {"period": "1y"}),
        ("AAPL", {"period": "1y"}),
        ("AAPL", {"period": "6mo"}),
        ("AAPL", {"period": "1y"}),
        ("^GSPC", {"start": "2025-01-01", "end": "2025-02-01"}),
        (
            "AAPL",
            {
                "start": "2025-01-01",
                "end": "2025-02-01",
                "auto_adjust": True,
            },
        ),
        ("AAPL", {"start": "2025-01-01", "end": "2025-02-01"}),
    ]


def test_migrated_learning_and_ranking_callers_preserve_exact_request_shapes(monkeypatch, tmp_path):
    fake = FakeObservationClient(price_frame())

    class NoFastInfo:
        fast_info: dict = {}

    monkeypatch.setattr("core.learning_engine.yf.Ticker", lambda ticker: NoFastInfo())
    learning = LearningEngine(tmp_path / "learning.json", history_client=fake)
    tracker = ExpectedReturnTracker(tmp_path / "predictions.json", history_client=fake)
    ranker = UniverseRanker(tmp_path / "rankings.json", history_client=fake)

    assert learning.get_current_price("AAPL") == 11.0
    assert learning.get_horizon_price("AAPL", datetime(2025, 1, 1)) == 10.0
    assert tracker.get_current_price("AAPL") == 11.0
    assert tracker.get_future_price("AAPL", "2025-01-01", 30) == 10.0
    assert list(ranker.load_prices("AAPL")["Close"]) == [10.0, 11.0]
    assert PortfolioMarketExposureService.review(
        {"holdings": [{"ticker": "aapl", "weight": 1.0}]},
        history_client=fake,
    )["covered_holdings"] == 1

    assert fake.calls == [
        ("AAPL", {"period": "1d"}),
        ("AAPL", {"start": "2025-01-01", "end": "2025-01-11"}),
        ("AAPL", {"period": "5d"}),
        ("AAPL", {"start": "2025-01-31", "end": "2025-02-10"}),
        ("AAPL", {"period": "2y", "auto_adjust": True}),
        ("AAPL", {"period": "1y", "auto_adjust": False}),
    ]


def test_market_cache_crosses_boundary_with_explicit_adjustment(tmp_path):
    fake = FakeObservationClient(price_frame())
    cache = MarketDataCache(tmp_path, history_client=fake)

    result = cache.load("AAPL", start="2025-01-01", end="2025-02-01")

    assert len(result) == 2
    assert fake.calls == [
        (
            "AAPL",
            {
                "start": "2025-01-01",
                "end": "2025-02-01",
                "auto_adjust": True,
            },
        )
    ]


def test_direct_yfinance_import_inventory_is_explicit():
    found = set()
    for root_name in ("core", "bots", "scripts", "tools"):
        for path in (ROOT / root_name).rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(alias.name.split(".")[0] == "yfinance" for alias in node.names):
                        found.add(relative)
                if isinstance(node, ast.ImportFrom):
                    if (node.module or "").split(".")[0] == "yfinance":
                        found.add(relative)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    dynamic_import = (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "importlib"
                        and node.func.attr == "import_module"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == "yfinance"
                    )
                    if dynamic_import:
                        found.add(relative)

    assert found == DIRECT_YFINANCE_IMPORT_ALLOWLIST


def test_invalid_watchlist_symbol_is_skipped_without_sdk_access():
    client, symbols, calls, _ = client_for(price_frame())

    assert BacktestEngine(client).compare_watchlist(["AAPL?bad"], period="1y") == []
    assert symbols == []
    assert calls == []
