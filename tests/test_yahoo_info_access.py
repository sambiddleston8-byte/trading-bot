from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pandas as pd
import pytest

from core.data_sources.provider_access import (
    ProviderAccessCoordinator,
    ProviderAccessPolicy,
    ProviderAttemptMetadata,
)
from core.data_sources.yahoo_history_access import YahooHistoryAccessError, YahooHistoryClient
from core.data_sources.yahoo_info_access import (
    INFO_FIELD_ALLOWLIST,
    NUMERIC_FIELDS,
    TEXT_FIELDS,
    YahooInfoAccessError,
    YahooInfoClient,
    YahooInfoObservation,
)
from core.multi_factor_engine import MultiFactorEngine
from core.portfolio_pipeline import PortfolioPipeline
from core.research_engine import ResearchEngine


ROOT = Path(__file__).resolve().parents[1]

MIGRATED_MODULES = (
    "core/multi_factor_engine.py",
    "core/research_engine.py",
)


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
    def __init__(self, responses, reads):
        self.responses = responses
        self.reads = reads

    @property
    def info(self):
        self.reads.append("info")
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
    return YahooInfoClient(ticker_factory=factory, coordinator=coordinator), symbols, reads, clock


# ------------------------------------------------------------------
# Pre-SDK validation
# ------------------------------------------------------------------


@pytest.mark.parametrize("symbol", ["", "../AAPL", "AAPL?token=secret", "A APL", "A" * 33])
def test_symbols_are_rejected_before_sdk_construction(symbol):
    client, symbols, reads, _ = client_for({"longName": "Apple Inc."})

    with pytest.raises(ValueError, match="unsupported format"):
        client.info(symbol)

    assert symbols == []
    assert reads == []


# ------------------------------------------------------------------
# Allowlist
# ------------------------------------------------------------------


def test_only_allowlisted_fields_survive_the_boundary():
    payload = {
        "longName": "Apple Inc.",
        "sector": "Technology",
        "marketCap": 3_000_000_000_000,
        "revenueGrowth": -0.05,
        "companyOfficers": [{"name": "someone", "totalPay": 1}],
        "address1": "1 Apple Park Way",
        "cookie": "session=never-expose",
        "unknownFutureField": 1.0,
    }
    client, _, _, _ = client_for(payload)

    observation = client.info("AAPL")

    assert dict(observation.fields) == {
        "longName": "Apple Inc.",
        "sector": "Technology",
        "marketCap": 3e12,
        "revenueGrowth": -0.05,
    }
    assert set(observation.fields) <= INFO_FIELD_ALLOWLIST


def test_allowlist_covers_exactly_the_fields_the_migrated_consumers_read():
    read_fields = set()
    for relative in MIGRATED_MODULES + ("core/portfolio_pipeline.py",):
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "info"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                read_fields.add(node.args[0].value)

    assert read_fields == INFO_FIELD_ALLOWLIST
    assert not set(TEXT_FIELDS) & set(NUMERIC_FIELDS)


# ------------------------------------------------------------------
# Scalar validation
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        float("nan"),
        float("inf"),
        float("-inf"),
        "3000000000000",
        [1.0],
        {"raw": 1.0},
        object(),
    ],
)
def test_malformed_numeric_fields_are_omitted_without_replacement(value):
    client, _, _, _ = client_for({"marketCap": value, "sector": "Technology"})

    observation = client.info("AAPL")

    assert "marketCap" not in observation.fields
    assert observation.fields.get("marketCap") is None
    assert dict(observation.fields) == {"sector": "Technology"}


@pytest.mark.parametrize(
    "value",
    [None, 1.0, True, "", "   ", "Apple\x00Inc.", "Apple\nInc.", "A" * 201, ["Apple"]],
)
def test_malformed_text_fields_are_omitted_without_replacement(value):
    client, _, _, _ = client_for({"longName": value, "beta": 1.2})

    observation = client.info("AAPL")

    assert "longName" not in observation.fields
    assert dict(observation.fields) == {"beta": 1.2}


def test_valid_scalars_preserve_exact_integers_and_strip_text():
    client, _, _, _ = client_for(
        {"longName": "  Apple Inc.  ", "marketCap": 7, "profitMargins": -0.25}
    )

    observation = client.info("AAPL")

    assert observation.fields["longName"] == "Apple Inc."
    assert observation.fields["marketCap"] == 7
    assert type(observation.fields["marketCap"]) is int
    assert observation.fields["profitMargins"] == -0.25


def test_large_integer_financial_values_retain_exact_precision():
    exact_value = 9_007_199_254_740_993
    client, _, _, _ = client_for({"marketCap": exact_value})

    observation = client.info("AAPL")

    assert observation.fields["marketCap"] == exact_value
    assert type(observation.fields["marketCap"]) is int


@pytest.mark.parametrize("payload", ["not-a-mapping", None, 5, ["longName"], object()])
def test_non_mapping_payloads_fail_closed(payload):
    client, symbols, reads, _ = client_for(payload)

    with pytest.raises(YahooInfoAccessError, match="Yahoo company-profile read failed") as caught:
        client.info("AAPL")

    assert caught.value.reason_code == "SDK_CALL_FAILURE"
    assert caught.value.metadata.attempts == 1
    assert caught.value.metadata.circuit_state == "CLOSED"
    assert symbols == ["AAPL"]
    assert reads == ["info"]


# ------------------------------------------------------------------
# Immutability and payload non-retention
# ------------------------------------------------------------------


def test_observation_is_immutable_and_retains_no_provider_payload():
    payload = {
        "longName": "Apple Inc.",
        "session_crumb": "never-expose",
        "companyOfficers": [{"name": "never-expose"}],
    }
    client, _, _, _ = client_for(payload)

    observation = client.info("AAPL")

    assert isinstance(observation, YahooInfoObservation)
    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.fields = {}
    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.admissible_as_replay_evidence = True
    with pytest.raises(TypeError):
        observation.fields["longName"] = "changed"
    with pytest.raises(TypeError):
        observation.fields["marketCap"] = 1.0

    assert all(isinstance(value, (int, float, str)) for value in observation.fields.values())
    assert "session_crumb" not in repr(observation)
    assert "never-expose" not in repr(observation)

    payload["longName"] = "mutated after the read"
    assert observation.fields["longName"] == "Apple Inc."


def test_direct_observation_construction_copies_and_validates_fields():
    fields = {"longName": " Apple Inc. ", "unknown": "never-expose"}
    observation = YahooInfoObservation(
        fields=fields,
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

    fields["longName"] = "changed"
    assert dict(observation.fields) == {"longName": "Apple Inc."}
    with pytest.raises(TypeError):
        observation.fields["longName"] = "changed"
    with pytest.raises(TypeError):
        YahooInfoObservation(
            fields={},
            access=observation.access,
            authenticated=True,
        )


def test_observation_claims_no_authority_or_replay_validity():
    client, _, _, _ = client_for({"currentPrice": 204.5})

    observation = client.info("AAPL")

    assert observation.provider == "YAHOO_FINANCE_VIA_YFINANCE"
    assert observation.authenticated is False
    assert observation.point_in_time is False
    assert observation.survivorship_safe is False
    assert observation.tradeable_quote is False
    assert observation.official_settlement is False
    assert observation.account_bound is False
    assert observation.temporally_complete is False
    assert observation.admissible_as_replay_evidence is False
    assert observation.access.attempts == 1
    assert observation.access.retry_count == 0
    assert observation.access.circuit_state == "CLOSED"


# ------------------------------------------------------------------
# One read, pacing and shared circuit behaviour
# ------------------------------------------------------------------


def test_exactly_one_info_property_read_per_call():
    client, symbols, reads, _ = client_for(
        {"longName": "Apple Inc."}, {"longName": "Microsoft Corporation"}
    )

    assert client.info("AAPL").fields["longName"] == "Apple Inc."
    assert client.info("MSFT").fields["longName"] == "Microsoft Corporation"
    assert symbols == ["AAPL", "MSFT"]
    assert reads == ["info", "info"]


def test_sdk_error_is_not_retried_or_leaked():
    secret = "crumb=never-expose&cookie=secret"
    client, symbols, reads, _ = client_for(RuntimeError(secret), {"longName": "Apple Inc."})

    with pytest.raises(YahooInfoAccessError) as caught:
        client.info("AAPL")

    assert str(caught.value) == "Yahoo company-profile read failed."
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.reason_code == "SDK_CALL_FAILURE"
    assert symbols == ["AAPL"]
    assert reads == ["info"]


def test_repeated_sdk_failures_open_the_circuit_without_a_third_read():
    client, symbols, reads, _ = client_for(
        RuntimeError("provider failed"),
        RuntimeError("provider failed"),
        {"longName": "Apple Inc."},
    )

    with pytest.raises(YahooInfoAccessError) as first:
        client.info("AAPL")
    with pytest.raises(YahooInfoAccessError) as second:
        client.info("AAPL")
    with pytest.raises(YahooInfoAccessError) as third:
        client.info("AAPL")

    assert first.value.reason_code == "SDK_CALL_FAILURE"
    assert second.value.metadata.circuit_state == "OPEN"
    assert third.value.reason_code == "CIRCUIT_OPEN"
    assert third.value.metadata.attempts == 0
    assert symbols == ["AAPL", "AAPL"]
    assert reads == ["info", "info"]


def test_repeated_non_mapping_payloads_open_the_circuit_without_a_third_read():
    client, symbols, reads, _ = client_for(None, "not-a-mapping", {})

    with pytest.raises(YahooInfoAccessError) as first:
        client.info("AAPL")
    with pytest.raises(YahooInfoAccessError) as second:
        client.info("MSFT")
    with pytest.raises(YahooInfoAccessError) as third:
        client.info("GOOG")

    assert first.value.reason_code == "SDK_CALL_FAILURE"
    assert first.value.metadata.circuit_state == "CLOSED"
    assert second.value.reason_code == "SDK_CALL_FAILURE"
    assert second.value.metadata.circuit_state == "OPEN"
    assert third.value.reason_code == "CIRCUIT_OPEN"
    assert third.value.metadata.attempts == 0
    assert symbols == ["AAPL", "MSFT"]
    assert reads == ["info", "info"]


def test_non_mapping_payload_does_not_reset_prior_shared_failure_credit():
    client, symbols, reads, _ = client_for(RuntimeError("provider failed"), None, {})

    with pytest.raises(YahooInfoAccessError) as first:
        client.info("AAPL")
    with pytest.raises(YahooInfoAccessError) as second:
        client.info("MSFT")
    with pytest.raises(YahooInfoAccessError) as third:
        client.info("GOOG")

    assert first.value.metadata.circuit_state == "CLOSED"
    assert second.value.metadata.circuit_state == "OPEN"
    assert third.value.reason_code == "CIRCUIT_OPEN"
    assert symbols == ["AAPL", "MSFT"]
    assert reads == ["info", "info"]


def test_non_mapping_payload_preserves_failure_credit_from_history_boundary():
    info_client, symbols, reads, clock = client_for(None, {})
    history_calls = []

    class FailingHistoryTicker:
        def history(self, **kwargs):
            history_calls.append(kwargs)
            raise RuntimeError("provider failed")

    history_client = YahooHistoryClient(
        ticker_factory=lambda symbol: FailingHistoryTicker(),
        coordinator=ProviderAccessCoordinator.for_provider(
            "YAHOO_FINANCE_VIA_YFINANCE",
            "Yahoo Finance via yfinance",
            policy=ProviderAccessPolicy(
                maximum_attempts=1,
                failure_threshold=2,
                cooldown_seconds=60,
            ),
            clock=clock,
            sleep=clock.sleep,
        ),
    )

    with pytest.raises(YahooHistoryAccessError) as history_failure:
        history_client.history("AAPL", period="1d")
    with pytest.raises(YahooInfoAccessError) as info_failure:
        info_client.info("MSFT")
    with pytest.raises(YahooInfoAccessError) as circuit_rejection:
        info_client.info("GOOG")

    assert history_failure.value.metadata.circuit_state == "CLOSED"
    assert info_failure.value.metadata.circuit_state == "OPEN"
    assert circuit_rejection.value.reason_code == "CIRCUIT_OPEN"
    assert history_calls == [{"period": "1d"}]
    assert symbols == ["MSFT"]
    assert reads == ["info"]


def test_missing_optional_fields_do_not_consume_shared_circuit_credit():
    client, _, reads, _ = client_for({}, {}, {}, {"longName": "Apple Inc."})

    for symbol in ("OLD1", "OLD2", "OLD3"):
        observation = client.info(symbol)
        assert dict(observation.fields) == {}
        assert observation.access.circuit_state == "CLOSED"

    assert client.info("AAPL").fields["longName"] == "Apple Inc."
    assert reads == ["info", "info", "info", "info"]


def test_info_and_history_clients_share_caller_entry_pacing():
    info_client, _, _, clock = client_for({"longName": "Apple Inc."}, interval=1)
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

    info_client.info("AAPL")
    history_client.history("MSFT", period="1d")

    assert clock.sleeps == [1.0]
    assert len(history_calls) == 1


# ------------------------------------------------------------------
# Consumer compatibility
# ------------------------------------------------------------------


class FakeInfoClient:
    def __init__(self, fields=None, error=None):
        self.fields = fields
        self.error = error
        self.calls = []

    def info(self, symbol):
        self.calls.append(symbol)
        if self.error is not None:
            raise self.error
        return YahooInfoObservation(
            fields=dict(self.fields),
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


def boundary_error():
    return YahooInfoAccessError(
        "Yahoo company-profile read failed.",
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


def test_multi_factor_get_info_returns_a_plain_mutable_dict(monkeypatch):
    monkeypatch.setattr(
        "core.multi_factor_engine.YahooInfoClient",
        lambda: pytest.fail("an injected info client must not construct a real client"),
    )
    fake = FakeInfoClient({"longName": "Apple Inc.", "marketCap": 3e12})

    result = MultiFactorEngine(info_client=fake).get_info("AAPL")

    assert type(result) is dict
    assert result == {"longName": "Apple Inc.", "marketCap": 3e12}
    result["longName"] = "caller-owned copy"
    assert fake.calls == ["AAPL"]


def test_multi_factor_get_info_returns_an_empty_dict_when_the_boundary_fails(capsys):
    fake = FakeInfoClient(error=boundary_error())

    assert MultiFactorEngine(info_client=fake).get_info("AAPL") == {}
    assert capsys.readouterr().out.strip().endswith("Yahoo info failed.")
    assert fake.calls == ["AAPL"]


def test_multi_factor_failure_log_does_not_echo_the_supplied_symbol(capsys):
    fake = FakeInfoClient(error=RuntimeError("crumb=never-expose"))

    assert MultiFactorEngine(info_client=fake).get_info("token=never-expose") == {}

    assert capsys.readouterr().out.strip() == "Yahoo info failed."


def test_multi_factor_analyse_still_returns_none_without_usable_info():
    fake = FakeInfoClient({})

    assert MultiFactorEngine(info_client=fake).analyse("AAPL") is None
    assert fake.calls == ["AAPL"]


def portfolio_pipeline_with(info_client):
    multi_factor = MultiFactorEngine.__new__(MultiFactorEngine)
    multi_factor.info_client = info_client
    pipeline = PortfolioPipeline.__new__(PortfolioPipeline)
    pipeline.multi_factor = multi_factor
    return pipeline


def test_portfolio_pipeline_retains_valid_allowlisted_current_prices():
    fake = FakeInfoClient({"currentPrice": 204.5})
    pipeline = portfolio_pipeline_with(fake)

    assert pipeline.get_current_prices(["AAPL"]) == {"AAPL": 204.5}
    assert pipeline.get_benchmark_price() == 204.5
    assert fake.calls == ["AAPL", "^GSPC"]


def test_portfolio_pipeline_drops_string_current_prices_fail_closed():
    fake = FakeInfoClient({"currentPrice": "204.5"})
    pipeline = portfolio_pipeline_with(fake)

    assert pipeline.get_current_prices(["AAPL"]) == {}
    assert pipeline.get_benchmark_price() is None


def test_portfolio_pipeline_keeps_no_price_shape_when_boundary_fails():
    fake = FakeInfoClient(error=boundary_error())
    pipeline = portfolio_pipeline_with(fake)

    assert pipeline.get_current_prices(["AAPL"]) == {}
    assert pipeline.get_benchmark_price() is None


def test_research_financial_data_keys_and_missing_values_are_unchanged(monkeypatch):
    monkeypatch.setattr(
        "core.research_engine.YahooInfoClient",
        lambda: pytest.fail("an injected info client must not construct a real client"),
    )
    monkeypatch.setattr("feedparser.parse", lambda url: type("Feed", (), {"entries": []})())
    fake = FakeInfoClient({"longName": "Apple Inc.", "sector": "Technology", "beta": 1.2})
    engine = ResearchEngine(info_client=fake)
    monkeypatch.setattr(engine.sec, "collect", lambda symbol: {"Filing Count": 0})

    research = engine.collect("aapl")

    financial = research["Financial Data"]
    assert set(financial) == {
        "Company",
        "Sector",
        "Industry",
        "Market Cap",
        "Revenue Growth",
        "Earnings Growth",
        "Profit Margin",
        "Operating Margin",
        "Forward PE",
        "Trailing PE",
        "PEG",
        "Price To Book",
        "ROE",
        "ROIC",
        "Beta",
        "Dividend Yield",
    }
    assert financial["Company"] == "Apple Inc."
    assert financial["Sector"] == "Technology"
    assert financial["Beta"] == 1.2
    assert financial["Market Cap"] is None
    assert financial["ROIC"] is None
    assert research["News Relevance"]["company"] == "Apple Inc."
    assert "Yahoo Finance" in research["Sources"]
    assert fake.calls == ["AAPL"]


def test_research_keeps_its_safe_no_data_shape_when_the_boundary_fails(monkeypatch, capsys):
    monkeypatch.setattr("feedparser.parse", lambda url: type("Feed", (), {"entries": []})())
    fake = FakeInfoClient(error=boundary_error())
    engine = ResearchEngine(info_client=fake)
    monkeypatch.setattr(engine.sec, "collect", lambda symbol: {"Filing Count": 0})

    research = engine.collect("AAPL")

    assert research["Financial Data"] == {}
    assert "Yahoo Finance" not in research["Sources"]
    assert "crumb" not in capsys.readouterr().out
    assert fake.calls == ["AAPL"]


def test_research_failure_log_does_not_echo_unexpected_exception_text(monkeypatch, capsys):
    monkeypatch.setattr("feedparser.parse", lambda url: type("Feed", (), {"entries": []})())
    fake = FakeInfoClient(error=RuntimeError("crumb=never-expose"))
    engine = ResearchEngine(info_client=fake)
    monkeypatch.setattr(engine.sec, "collect", lambda symbol: {"Filing Count": 0})

    research = engine.collect("AAPL")

    assert research["Financial Data"] == {}
    assert "never-expose" not in capsys.readouterr().out


# ------------------------------------------------------------------
# Import inventory
# ------------------------------------------------------------------


def test_migrated_modules_no_longer_import_yfinance():
    for relative in MIGRATED_MODULES:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] != "yfinance" for alias in node.names), relative
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "yfinance", relative
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert not (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "importlib"
                    and node.func.attr == "import_module"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "yfinance"
                ), relative
