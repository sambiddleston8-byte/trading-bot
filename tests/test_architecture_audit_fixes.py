import pandas as pd
import pytest

from core.expected_return_tracker import ExpectedReturnTracker
from core.learning_engine import LearningEngine
from core.market_data_cache import MarketDataCache
from core.paper_execution import PaperExecutionEngine
from core.research.market_regime_engine import MarketRegimeEngine
from core.walk_forward_backtest import WalkForwardBacktest


def test_future_price_uses_a_single_explicit_date_range(tmp_path):
    calls = []

    class HistoryClient:
        def history(self, symbol, **kwargs):
            calls.append((symbol, kwargs))
            frame = pd.DataFrame({"Close": [100.0, 101.0, 102.0]})
            return type("Observation", (), {"frame": frame})()

    tracker = ExpectedReturnTracker(tmp_path / "predictions.json", history_client=HistoryClient())

    assert tracker.get_future_price("TEST", "2025-01-01", 30) == 100.0
    assert calls == [("TEST", {"start": "2025-01-31", "end": "2025-02-10"})]


def test_legacy_learning_records_each_exact_horizon_price(monkeypatch, tmp_path):
    path = tmp_path / "learning.json"

    class UnusedHistoryClient:
        def history(self, symbol, **kwargs):
            raise AssertionError("horizon prices are supplied by the test")

    engine = LearningEngine(path, history_client=UnusedHistoryClient())
    engine._save(
        [{
            "Ticker": "TEST",
            "Date": "2020-01-01T00:00:00",
            "Price At Decision": 100.0,
            "Recommendation": "BUY",
            "Status": "OPEN",
            "Outcomes": {},
        }]
    )
    prices = {30: 101.0, 90: 103.0, 180: 106.0, 365: 112.0}

    def horizon_price(ticker, target_date):
        origin = pd.Timestamp("2020-01-01").to_pydatetime()
        return prices[(target_date - origin).days]

    monkeypatch.setattr(engine, "get_horizon_price", horizon_price)
    assert engine.evaluate_predictions()["Evaluated"] == 1

    outcomes = engine._load()[0]["Outcomes"]
    assert outcomes["1M"]["Price"] == 101.0
    assert outcomes["12M"]["Price"] == 112.0
    assert outcomes["1M"]["Target Date"] == "2020-01-31"


def test_legacy_paper_valuation_fails_closed_without_a_current_price():
    engine = PaperExecutionEngine()
    assert engine.buy("TEST", 1, 100)

    with pytest.raises(ValueError, match="current price is required"):
        engine.portfolio_value({})

    with pytest.raises(ValueError, match="current price is required"):
        engine.position_values({})


def test_market_regime_does_not_reuse_a_cache_from_an_unknown_date(monkeypatch):
    history = pd.DataFrame({"Close": list(range(100, 320))})

    class FinancialData:
        def __init__(self):
            self.calls = 0

        def get_price_history(self, symbol, period):
            self.calls += 1
            return history

    data = FinancialData()
    MarketRegimeEngine._cached_result = {"status": "COMPLETE", "regime": "STALE"}
    MarketRegimeEngine._cached_utc_date = None
    result = MarketRegimeEngine(financial_data=data).analyse()

    assert data.calls == 1
    assert result["regime"] == "RISK_ON"


def test_walk_forward_loads_market_data_once_for_the_whole_run():
    index = pd.date_range("2018-01-01", "2022-01-01", freq="B")
    history = pd.DataFrame({"Close": range(100, 100 + len(index))}, index=index)

    class Cache:
        def __init__(self):
            self.calls = []

        def load_universe(self, symbols, start, end):
            self.calls.append((symbols, start, end))
            return {symbol: history for symbol in symbols}

    engine = WalkForwardBacktest()
    engine.data_cache = Cache()
    results = engine.run(["TEST"], start_year=2020, end_year=2021)

    assert len(engine.data_cache.calls) == 1
    assert len(results) == 1


def test_market_data_cache_refuses_a_biased_partial_universe(monkeypatch, tmp_path):
    cache = MarketDataCache(directory=tmp_path)

    def load(symbol, start, end):
        if symbol == "MISSING":
            raise ValueError("provider failed")
        return pd.DataFrame({"Close": [100.0]})

    monkeypatch.setattr(cache, "load", load)

    with pytest.raises(RuntimeError, match="MISSING"):
        cache.load_universe(["GOOD", "MISSING"])

    partial = cache.load_universe(["GOOD", "MISSING"], allow_partial=True)
    assert list(partial) == ["GOOD"]
