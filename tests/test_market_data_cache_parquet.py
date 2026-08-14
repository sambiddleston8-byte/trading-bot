from __future__ import annotations

from hashlib import sha256
import json

import pandas as pd
import pytest

import core.market_data_cache as module
from core.market_data_cache import MarketDataCache


def frame(*, first_close: float = 10.0) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        ["2025-01-02T09:30:00-05:00", "2025-01-03T09:30:00-05:00"],
        name="Date",
    )
    return pd.DataFrame(
        {
            "Open": pd.Series([9.0, 10.0], index=index, dtype="float64"),
            "Close": pd.Series([first_close, 11.0], index=index, dtype="float64"),
            "Volume": pd.Series([100, 110], index=index, dtype="int64"),
        }
    )


def manifests(cache: MarketDataCache) -> list:
    return sorted(cache.snapshot_root.rglob("*.json"))


def test_parquet_round_trip_preserves_frame_and_reuses_exact_query(tmp_path, monkeypatch):
    cache = MarketDataCache(tmp_path)
    source = frame()
    calls = []
    monkeypatch.setattr(cache, "_fetch", lambda *args: calls.append(args) or source)

    first = cache.load("AAPL", start="2025-01-01", end="2025-02-01")
    second = cache.load("AAPL", start="2025-01-01", end="2025-02-01")

    pd.testing.assert_frame_equal(first, source)
    pd.testing.assert_frame_equal(second, source)
    assert len(calls) == 1
    assert not list(tmp_path.rglob("*.pkl"))


def test_legacy_pickle_is_never_opened_or_changed(tmp_path, monkeypatch):
    cache = MarketDataCache(tmp_path)
    legacy = cache._legacy_path("^GSPC")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    hostile_bytes = b"not-a-real-pickle-and-never-opened"
    legacy.write_bytes(hostile_bytes)
    monkeypatch.setattr(cache, "_fetch", lambda *args: frame())

    result = cache.load("^GSPC", start="2025-01-01", end="2025-02-01")

    assert len(result) == 2
    assert legacy.read_bytes() == hostile_bytes


def test_manifest_marks_snapshot_non_authoritative_and_hashes_parquet(tmp_path, monkeypatch):
    cache = MarketDataCache(tmp_path)
    monkeypatch.setattr(cache, "_fetch", lambda *args: frame())
    cache.load("AAPL", start="2025-01-01", end=None)

    manifest_path = manifests(cache)[0]
    manifest = json.loads(manifest_path.read_text())
    payload = (manifest_path.parent / manifest["parquet_file"]).read_bytes()

    assert manifest["content_sha256"] == sha256(payload).hexdigest()
    assert manifest["prices_are_back_adjusted"] is True
    assert manifest["point_in_time"] is False
    assert manifest["survivorship_safe"] is False
    assert manifest["admissible_as_replay_evidence"] is False


@pytest.mark.parametrize("bad_close", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_fresh_close_is_never_written(tmp_path, monkeypatch, bad_close):
    cache = MarketDataCache(tmp_path)
    monkeypatch.setattr(cache, "_fetch", lambda *args: frame(first_close=bad_close))

    with pytest.raises(ValueError, match="invalid price evidence"):
        cache.download("AAPL", start="2025-01-01", end=None)

    assert not manifests(cache)
    assert not list(cache.snapshot_root.rglob("*.parquet"))


def test_tampered_parquet_fails_closed_without_refetch(tmp_path, monkeypatch):
    cache = MarketDataCache(tmp_path)
    calls = []
    monkeypatch.setattr(cache, "_fetch", lambda *args: calls.append(args) or frame())
    cache.load("AAPL", start="2025-01-01", end=None)
    manifest_path = manifests(cache)[0]
    manifest = json.loads(manifest_path.read_text())
    parquet = manifest_path.parent / manifest["parquet_file"]
    parquet.write_bytes(parquet.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="integrity check failed"):
        cache.load("AAPL", start="2025-01-01", end=None)
    assert len(calls) == 1


def test_snapshots_are_versioned_and_symbol_sanitisation_cannot_collide(tmp_path, monkeypatch):
    cache = MarketDataCache(tmp_path)
    values = iter([frame(first_close=10.0), frame(first_close=20.0)])
    monkeypatch.setattr(cache, "_fetch", lambda *args: next(values))

    cache.download("^X", start="2025-01-01", end=None)
    cache.download("^X", start="2025-01-01", end=None)

    assert len(manifests(cache)) == 2
    assert cache._symbol_directory("^X") != cache._symbol_directory("/X")
    assert len(list(cache.snapshot_root.rglob("*.parquet"))) == 2


def test_failed_manifest_publication_keeps_prior_snapshot_readable(tmp_path, monkeypatch):
    cache = MarketDataCache(tmp_path)
    monkeypatch.setattr(cache, "_fetch", lambda *args: frame())
    cache.download("AAPL", start="2025-01-01", end=None)
    original = module._atomic_write_once
    calls = 0

    def fail_second_write(path, payload):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated manifest failure")
        original(path, payload)

    monkeypatch.setattr(module, "_atomic_write_once", fail_second_write)
    with pytest.raises(OSError, match="manifest failure"):
        cache.download("AAPL", start="2025-01-01", end=None)

    monkeypatch.setattr(cache, "_fetch", lambda *args: pytest.fail("unexpected refetch"))
    loaded = cache.load("AAPL", start="2025-01-01", end=None)
    pd.testing.assert_frame_equal(loaded, frame())


def test_date_ranges_have_separate_snapshots_and_timezone_safe_slicing(tmp_path, monkeypatch):
    cache = MarketDataCache(tmp_path)
    calls = []
    monkeypatch.setattr(cache, "_fetch", lambda *args: calls.append(args) or frame())

    result = cache.load("AAPL", start="2025-01-03", end="2025-01-04")
    cache.load("AAPL", start="2025-01-01", end="2025-02-01")

    assert list(result["Close"]) == [11.0]
    assert len(calls) == 2
    assert len(manifests(cache)) == 2
