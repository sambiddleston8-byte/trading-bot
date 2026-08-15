"""Fake-only coverage for the profile consumers migrated onto the boundary.

Every test here drives a fake info client, so no test in this module reaches a
network, a provider SDK or a credential.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from bots.competitors.analyser import CompetitorAnalyser
from core.company_context import CompanyContext
from core.data_sources.provider_access import ProviderAttemptMetadata
from core.data_sources.yahoo_info_access import (
    INFO_FIELD_ALLOWLIST,
    YahooInfoAccessError,
    YahooInfoClient,
    YahooInfoObservation,
)
from core.data_sources.yahoo_source import YahooSource
from core.valuation_engine import ValuationEngine


ROOT = Path(__file__).resolve().parents[1]

# ``core/financial_data.py`` is deliberately absent: its cached
# ``get_company_info`` still reads ``Ticker.info`` directly and feeds the broad
# ``CompanyContext``, whose analysers consume profile fields outside this
# allowlist.  Migrating it needs its own inventoried batch, and no field is
# admitted to the boundary on its behalf here.
MIGRATED_CONSUMERS = (
    "bots/competitors/analyser.py",
    "core/data_sources/yahoo_source.py",
    "core/valuation_engine.py",
)

# The only receivers a migrated consumer may read ``.info`` from: the injected
# boundary client, and the plain ``CompanyContext`` dataclass field that the
# still-deferred aggregator populates.  A ``yf.Ticker(...).info`` read would add
# an unexpected receiver here and fail the inventory test below.
PERMITTED_INFO_RECEIVERS = {
    "bots/competitors/analyser.py": {"self.info_client", "context"},
    "core/data_sources/yahoo_source.py": {"self.info_client"},
    "core/valuation_engine.py": {"self.info_client"},
}


def metadata():
    return ProviderAttemptMetadata(
        provider="Yahoo Finance via yfinance",
        attempts=1,
        retry_count=0,
        retried_status_codes=(),
        total_wait_seconds=0.0,
        elapsed_seconds=0.0,
        circuit_state="CLOSED",
    )


class FakeInfoClient:
    """Return real observations, so boundary field filtering is exercised."""

    def __init__(self, payloads=None, error=None):
        self.payloads = payloads or {}
        self.error = error
        self.calls = []

    def info(self, symbol):
        self.calls.append(symbol)
        if self.error is not None:
            raise self.error
        return YahooInfoObservation(
            fields=dict(self.payloads.get(symbol, {})),
            access=metadata(),
        )


def boundary_error():
    return YahooInfoAccessError(
        "Yahoo company-profile read failed.",
        reason_code="SDK_CALL_FAILURE",
        metadata=metadata(),
    )


# ------------------------------------------------------------------
# CompetitorAnalyser
# ------------------------------------------------------------------


def context_for(info):
    return CompanyContext(
        symbol="NVDA",
        info=info,
        financials=None,
        balance_sheet=None,
        cashflow=None,
        history=None,
    )


SUBJECT_INFO = {
    "sector": "Technology",
    "industry": "Semiconductors",
    "longName": "NVIDIA Corporation",
    "marketCap": 3_000_000_000_000,
    "trailingPE": 50.0,
    "forwardPE": 30.0,
    "revenueGrowth": 0.80,
    "profitMargins": 0.50,
    "operatingMargins": 0.60,
    "returnOnEquity": 0.90,
    "returnOnInvestedCapital": 0.70,
}


def test_competitor_analyser_reads_peers_through_the_boundary():
    fake = FakeInfoClient(
        {
            "AMD": {
                "longName": "Advanced Micro Devices, Inc.",
                "marketCap": 250_000_000_000,
                "trailingPE": 100.0,
                "revenueGrowth": 0.10,
                "profitMargins": 0.05,
            }
        }
    )

    result = CompetitorAnalyser(info_client=fake).analyse(context_for(SUBJECT_INFO))

    assert fake.calls == ["AMD", "AVGO", "QCOM", "INTC", "MU"]
    assert [peer["Ticker"] for peer in result["Peers"]] == ["AMD"]
    assert result["Peers"][0]["Company"] == "Advanced Micro Devices, Inc."
    assert result["Peers"][0]["Forward PE"] is None
    assert result["Sector"] == "Technology"
    assert result["Industry"] == "Semiconductors"
    # Lower PE, higher growth and higher margin all favour the subject.
    assert result["Valuation Rank"] == 1
    assert result["Growth Rank"] == 1
    assert result["Profitability Rank"] == 1
    assert result["Competitive Score"] == 90
    assert result["Assessment"] == (
        "The company compares favourably with its selected peers."
    )


def test_competitor_analyser_keeps_its_no_peer_shape_when_the_boundary_fails(capsys):
    fake = FakeInfoClient(error=boundary_error())

    result = CompetitorAnalyser(info_client=fake).analyse(context_for(SUBJECT_INFO))

    assert result["Peers"] == []
    assert result["Valuation Rank"] is None
    assert result["Growth Rank"] is None
    assert result["Profitability Rank"] is None
    assert result["Competitive Score"] == 50
    assert result["Subject Company"]["Company"] == "NVIDIA Corporation"
    assert capsys.readouterr().out == ""


def test_competitor_analyser_failure_log_does_not_echo_provider_text(capsys):
    fake = FakeInfoClient(error=RuntimeError("crumb=never-expose"))

    result = CompetitorAnalyser(info_client=fake).analyse(context_for(SUBJECT_INFO))

    assert result["Peers"] == []
    assert "never-expose" not in capsys.readouterr().out


def test_competitor_analyser_drops_non_allowlisted_peer_fields():
    fake = FakeInfoClient(
        {
            "AMD": {
                "longName": "Advanced Micro Devices, Inc.",
                "companyOfficers": [{"name": "never-expose"}],
                "cookie": "session=never-expose",
                "address1": "2485 Augustine Drive",
            }
        }
    )
    analyser = CompetitorAnalyser(info_client=fake)

    peer_info = analyser.get_peer_info("AMD")

    assert type(peer_info) is dict
    assert set(peer_info) <= INFO_FIELD_ALLOWLIST
    assert peer_info == {"longName": "Advanced Micro Devices, Inc."}
    peer_info["longName"] = "caller-owned copy"


def test_competitor_analyser_skips_a_peer_with_no_usable_fields():
    fake = FakeInfoClient({"AMD": {"address1": "2485 Augustine Drive"}})

    result = CompetitorAnalyser(info_client=fake).analyse(context_for(SUBJECT_INFO))

    assert result["Peers"] == []


def test_competitor_analyser_omits_malformed_subject_ranking_numbers():
    fake = FakeInfoClient(
        {
            "AMD": {
                "trailingPE": 100.0,
                "revenueGrowth": 0.10,
                "profitMargins": 0.05,
            }
        }
    )
    subject_info = dict(SUBJECT_INFO)
    subject_info.update(
        {
            "trailingPE": "50.0",
            "revenueGrowth": float("nan"),
            "profitMargins": True,
        }
    )

    result = CompetitorAnalyser(info_client=fake).analyse(context_for(subject_info))

    assert result["Subject Company"]["PE"] is None
    assert result["Subject Company"]["Revenue Growth"] is None
    assert result["Subject Company"]["Profit Margin"] is None
    assert result["Valuation Rank"] is None
    assert result["Growth Rank"] is None
    assert result["Profitability Rank"] is None


def test_default_constructors_wire_yahoo_info_clients(monkeypatch, tmp_path):
    class DefaultInfoClient:
        pass

    fake_yfinance = SimpleNamespace(Ticker=object())
    monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yfinance)
    monkeypatch.setattr("bots.competitors.analyser.YahooInfoClient", DefaultInfoClient)
    monkeypatch.setattr(
        "core.data_sources.yahoo_source.YahooInfoClient", DefaultInfoClient
    )
    monkeypatch.setattr("core.valuation_engine.YahooInfoClient", DefaultInfoClient)

    competitor = CompetitorAnalyser()
    source = YahooSource()
    valuation = ValuationEngine(output_directory=tmp_path / "valuation")

    assert isinstance(competitor.info_client, DefaultInfoClient)
    assert isinstance(source.info_client, DefaultInfoClient)
    assert source.yf is fake_yfinance
    assert isinstance(valuation.info_client, DefaultInfoClient)


# ------------------------------------------------------------------
# YahooSource.fetch
# ------------------------------------------------------------------


class FakeTicker:
    revenue_estimate = None
    earnings_estimate = None
    income_stmt = None
    cashflow = None
    balance_sheet = None


class FakeYFinance:
    def __init__(self):
        self.symbols = []

    def Ticker(self, symbol):
        self.symbols.append(symbol)
        return FakeTicker()


def yahoo_source_with(info_client):
    source = YahooSource.__new__(YahooSource)
    source.yf = FakeYFinance()
    source.info_client = info_client
    return source


def test_yahoo_source_fetch_maps_boundary_scalars_onto_its_market_shape():
    fake = FakeInfoClient(
        {
            "AAPL": {
                "longName": "Apple Inc.",
                "currentPrice": 204.5,
                "marketCap": 3_000_000_000_000,
                "sharesOutstanding": 15_000_000_000,
                "beta": 1.2,
                "totalCash": 60_000_000_000,
            }
        }
    )

    result = yahoo_source_with(fake).fetch("aapl")

    assert fake.calls == ["AAPL"]
    assert result["ticker"] == "AAPL"
    assert result["company"] == "Apple Inc."
    assert result["market"] == {
        "current_price": 204.5,
        "market_cap": 3e12,
        "shares_outstanding": 15e9,
        "beta": 1.2,
    }
    assert result["balance_sheet"]["yahoo_total_cash"]["value"] == 6e10


def test_yahoo_source_fetch_still_falls_back_to_regular_market_price():
    fake = FakeInfoClient({"AAPL": {"regularMarketPrice": 201.25}})

    result = yahoo_source_with(fake).fetch("AAPL")

    assert result["market"]["current_price"] == 201.25


def test_yahoo_source_fetch_keeps_its_no_data_shape_when_the_boundary_fails(capsys):
    fake = FakeInfoClient(error=boundary_error())

    result = yahoo_source_with(fake).fetch("AAPL")

    assert result["company"] is None
    assert result["market"] == {
        "current_price": None,
        "market_cap": None,
        "shares_outstanding": None,
        "beta": None,
    }
    assert result["balance_sheet"]["yahoo_total_cash"]["value"] is None
    assert result["financials"] == {"latest_annual": None, "historical_annual": []}
    assert capsys.readouterr().out == ""


def test_yahoo_source_get_company_info_filters_and_returns_a_fresh_dict():
    fake = FakeInfoClient(
        {"AAPL": {"longName": "Apple Inc.", "cookie": "session=never-expose"}}
    )
    source = yahoo_source_with(fake)

    first = source.get_company_info("AAPL")
    second = source.get_company_info("AAPL")

    assert type(first) is dict
    assert first == {"longName": "Apple Inc."}
    assert set(first) <= INFO_FIELD_ALLOWLIST
    first["longName"] = "caller-owned copy"
    assert second == {"longName": "Apple Inc."}


def test_yahoo_source_rejects_an_unsupported_symbol_without_a_provider_call():
    provider_symbols = []

    def ticker_factory(symbol):
        provider_symbols.append(symbol)
        raise AssertionError("provider construction must not be reached")

    source = yahoo_source_with(YahooInfoClient(ticker_factory=ticker_factory))

    assert source.get_company_info("../AAPL") == {}
    assert provider_symbols == []


# ------------------------------------------------------------------
# ValuationEngine.get_info
# ------------------------------------------------------------------


def valuation_engine_with(info_client, tmp_path):
    return ValuationEngine(
        output_directory=tmp_path / "valuation",
        info_client=info_client,
    )


def test_valuation_get_info_returns_a_fresh_filtered_dict(tmp_path):
    fake = FakeInfoClient(
        {
            "NVDA": {
                "beta": 1.7,
                "debtToEquity": 20.0,
                "revenueGrowth": 0.80,
                "currentPrice": 120.0,
                "totalDebt": 10_000_000_000,
                "totalCash": 35_000_000_000,
                "cookie": "session=never-expose",
            }
        }
    )
    engine = valuation_engine_with(fake, tmp_path)

    info = engine.get_info("NVDA")

    assert type(info) is dict
    assert info == {
        "beta": 1.7,
        "debtToEquity": 20.0,
        "revenueGrowth": 0.80,
        "currentPrice": 120.0,
        "totalDebt": 10_000_000_000,
        "totalCash": 35_000_000_000,
    }
    assert set(info) <= INFO_FIELD_ALLOWLIST
    info["beta"] = "caller-owned copy"
    assert engine.get_info("NVDA")["beta"] == 1.7
    assert fake.calls == ["NVDA", "NVDA"]


def test_valuation_assumptions_are_unchanged_by_the_migration(tmp_path):
    fake = FakeInfoClient({"NVDA": {"beta": 1.7, "debtToEquity": 20.0, "revenueGrowth": 0.80}})
    engine = valuation_engine_with(fake, tmp_path)

    info = engine.get_info("NVDA")

    # beta >= 1.5 adds 1.5 points, debt/equity < 25 removes 0.5.
    assert engine.determine_wacc(info) == pytest.approx(0.09 + 0.015 - 0.005)
    # 60% analyst + 25% Yahoo revenueGrowth + 15% historical CAGR, unchanged.
    assert engine.determine_revenue_growth(info, 0.40, 0.50) == pytest.approx(
        0.50 * 0.60 + 0.80 * 0.25 + 0.40 * 0.15
    )
    # The 0.60 upper clamp is likewise unchanged.
    assert engine.determine_revenue_growth(info, 0.90, 0.90) == pytest.approx(0.60)


def test_valuation_get_info_returns_an_empty_dict_when_the_boundary_fails(tmp_path, capsys):
    fake = FakeInfoClient(error=boundary_error())
    engine = valuation_engine_with(fake, tmp_path)

    assert engine.get_info("NVDA") == {}
    assert capsys.readouterr().out.strip() == "Yahoo info failed."


def test_valuation_failure_log_does_not_echo_the_symbol_or_provider_text(tmp_path, capsys):
    fake = FakeInfoClient(error=RuntimeError("crumb=never-expose"))
    engine = valuation_engine_with(fake, tmp_path)

    assert engine.get_info("AAPL?token=never-expose") == {}
    assert capsys.readouterr().out.strip() == "Yahoo info failed."


def test_valuation_analyse_fails_closed_without_company_data(tmp_path, capsys):
    fake = FakeInfoClient(error=boundary_error())
    engine = valuation_engine_with(fake, tmp_path)

    assert engine.analyse("NVDA") == {
        "Ticker": "NVDA",
        "Status": "FAILED",
        "Reason": "No company data available.",
    }
    assert "never-expose" not in capsys.readouterr().out


# ------------------------------------------------------------------
# Import and raw-read inventory
# ------------------------------------------------------------------


def profile_fields_read_by(relative):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)
    return {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and (node.func.value.id == "info" or node.func.value.id.endswith("_info"))
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }


def test_every_valuation_profile_field_direct_and_indirect_survives_the_boundary():
    direct = profile_fields_read_by("core/valuation_engine.py")
    # ValuationEngine.analyse reaches YahooSource.fetch's profile read through
    # FinancialDataEngine.build_context, so that module's fields are the
    # engine's indirect boundary dependency.
    indirect = profile_fields_read_by("core/data_sources/yahoo_source.py")

    assert direct == {
        "beta",
        "currentPrice",
        "debtToEquity",
        "longName",
        "marketCap",
        "regularMarketPrice",
        "revenueGrowth",
        "sharesOutstanding",
        "totalCash",
        "totalDebt",
    }
    assert indirect == {
        "beta",
        "currentPrice",
        "longName",
        "marketCap",
        "regularMarketPrice",
        "sharesOutstanding",
        "totalCash",
    }
    assert direct | indirect <= INFO_FIELD_ALLOWLIST


def test_the_deferred_aggregator_still_reads_info_directly():
    """Pin the deferred scope, so a silent partial migration cannot slip in.

    ``FinancialDataEngine.get_company_info`` feeds ``CompanyContext``, whose
    analysers read profile fields this allowlist deliberately excludes.  Routing
    it through the boundary without first inventorying those consumers would
    silently blank their inputs.
    """
    tree = ast.parse(
        (ROOT / "core/financial_data.py").read_text(encoding="utf-8"),
        filename="core/financial_data.py",
    )

    receivers = {
        ast.unparse(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "info"
    }

    assert receivers == {"self.get_company(symbol)"}


@pytest.mark.parametrize("relative", MIGRATED_CONSUMERS)
def test_consumers_read_info_only_from_the_injected_boundary_client(relative):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)

    receivers = {
        ast.unparse(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "info"
    }

    assert receivers == PERMITTED_INFO_RECEIVERS[relative]


def test_competitor_analyser_no_longer_imports_yfinance():
    tree = ast.parse(
        (ROOT / "bots/competitors/analyser.py").read_text(encoding="utf-8"),
        filename="bots/competitors/analyser.py",
    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "yfinance" for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "yfinance"


@pytest.mark.parametrize("relative", MIGRATED_CONSUMERS)
def test_consumers_construct_no_yfinance_ticker_for_a_profile_read(relative):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr == "info"):
            continue
        assert not isinstance(node.value, ast.Call), relative
