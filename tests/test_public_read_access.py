from __future__ import annotations

import ast
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests

from core.catalyst_engine import CatalystEngine
from core.data_sources.provider_access import (
    ProviderAccessCoordinator,
    ProviderAccessPolicy,
)
from core.data_sources.public_read_access import (
    FRED_GRAPH_ENDPOINT,
    GITHUB_UNIVERSE_ENDPOINT,
    GOOGLE_NEWS_ENDPOINT,
    PublicReadError,
    PublicReadEndpoint,
    PublicTextClient,
)
from core.portfolio.universe_engine import UniverseEngine
from core.research.macro_environment_engine import MacroEnvironmentEngine


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Response:
    def __init__(self, status: int, text: str = "ok", *, raw=None, headers=None) -> None:
        self.status_code = status
        self.ok = 200 <= status < 300
        self.headers = headers or {}
        self.content = text.encode("utf-8") if raw is None else raw


class Session:
    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeTextClient:
    def __init__(self, values) -> None:
        self.values = list(values)
        self.calls: list[tuple[str, dict]] = []

    def get_text(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeSECClient:
    def get_json(self, url):
        raise AssertionError(f"unexpected SEC call: {url}")


@pytest.fixture(autouse=True)
def reset_shared_state():
    ProviderAccessCoordinator.reset()
    MacroEnvironmentEngine._cached_result = None
    yield
    ProviderAccessCoordinator.reset()
    MacroEnvironmentEngine._cached_result = None


def access(endpoint, clock, *, attempts=2, interval=0):
    return ProviderAccessCoordinator.for_provider(
        endpoint.provider_key,
        endpoint.provider_name,
        policy=ProviderAccessPolicy(
            minimum_interval_seconds=interval,
            maximum_attempts=attempts,
            base_backoff_seconds=0,
            retry_after_cap_seconds=0,
            failure_threshold=2,
            cooldown_seconds=60,
        ),
        clock=clock,
        sleep=clock.sleep,
    )


def client(endpoint, session, clock=None, **changes):
    clock = clock or Clock()
    return PublicTextClient(
        endpoint,
        session=session,
        access=access(endpoint, clock, **changes),
    )


def test_fixed_public_read_uses_no_redirects_and_exact_request_shape():
    session = Session(Response(200, "Symbol,Security\nAAPL,Apple\n"))
    target = client(GITHUB_UNIVERSE_ENDPOINT, session)

    text = target.get_text(
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
        accept="text/csv,text/plain",
    )

    assert text.startswith("Symbol")
    assert session.calls[0][1] == {
        "params": None,
        "headers": {"Accept": "text/csv,text/plain"},
        "timeout": 30,
        "allow_redirects": False,
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://fred.stlouisfed.org/graph/fredgraph.csv",
        "https://example.com/graph/fredgraph.csv",
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=FEDFUNDS",
        "https://fred.stlouisfed.org/graph/other.csv",
        "https://fred.stlouisfed.org/graph/fred\ngraph.csv",
    ],
)
def test_invalid_public_target_fails_before_network(url):
    session = Session(Response(200))
    with pytest.raises(PublicReadError, match="target") as caught:
        client(FRED_GRAPH_ENDPOINT, session).get_text(
            url,
            params={"id": "FEDFUNDS"},
            accept="text/csv",
        )
    assert caught.value.reason_code == "INVALID_TARGET"
    assert session.calls == []


@pytest.mark.parametrize(
    "params",
    [
        None,
        {},
        [],
        {"id": ""},
        {"id": "FED\nFUNDS"},
        {"id": "FEDFUNDS", "key": "x"},
    ],
)
def test_invalid_public_parameters_fail_before_network(params):
    session = Session(Response(200))
    with pytest.raises(PublicReadError, match="parameters") as caught:
        client(FRED_GRAPH_ENDPOINT, session).get_text(
            "https://fred.stlouisfed.org/graph/fredgraph.csv",
            params=params,
            accept="text/csv",
        )
    assert caught.value.reason_code == "INVALID_PARAMETERS"
    assert session.calls == []


def test_caller_url_constants_are_bound_to_endpoint_policies():
    assert urlsplit(UniverseEngine.SP500_URL).path in GITHUB_UNIVERSE_ENDPOINT.paths
    assert urlsplit(UniverseEngine.NASDAQ100_URL).path in GITHUB_UNIVERSE_ENDPOINT.paths
    assert urlsplit(MacroEnvironmentEngine.FRED_URL).path in FRED_GRAPH_ENDPOINT.paths


def test_endpoint_policy_rejects_empty_or_non_absolute_configuration():
    with pytest.raises(ValueError, match="invalid"):
        PublicReadEndpoint("", "name", "example.com", frozenset({"/x"}), frozenset(), 1)
    with pytest.raises(ValueError, match="absolute"):
        PublicReadEndpoint("key", "name", "example.com", frozenset({"x"}), frozenset(), 1)


@pytest.mark.parametrize("status", [301, 302, 307, 308, 429])
def test_redirect_and_quota_bodies_are_never_accepted(status):
    session = Session(Response(status, "must not be trusted"))
    with pytest.raises(PublicReadError, match=f"HTTP {status}"):
        client(GITHUB_UNIVERSE_ENDPOINT, session).get_text(
            "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
            accept="text/csv",
        )
    assert len(session.calls) == 1


def test_retryable_failure_retries_once_and_shared_clients_are_paced():
    clock = Clock()
    first_session = Session(Response(503), Response(200, "first"))
    second_session = Session(Response(200, "second"))
    first = client(GITHUB_UNIVERSE_ENDPOINT, first_session, clock, interval=1)
    second = client(GITHUB_UNIVERSE_ENDPOINT, second_session, clock, interval=1)
    url = (
        "https://raw.githubusercontent.com/"
        "datasets/s-and-p-500-companies/main/data/constituents.csv"
    )

    assert first.get_text(url, accept="text/csv") == "first"
    assert second.get_text(url, accept="text/csv") == "second"
    assert len(first_session.calls) == 2
    assert clock.sleeps == [1.0, 1.0]


def test_transport_and_response_failures_do_not_leak_request_details():
    session = Session(
        requests.ConnectionError("https://secret.example/?token=do-not-print"),
        requests.ConnectionError("second secret"),
    )
    target = client(GITHUB_UNIVERSE_ENDPOINT, session)
    url = (
        "https://raw.githubusercontent.com/"
        "datasets/s-and-p-500-companies/main/data/constituents.csv"
    )
    with pytest.raises(PublicReadError, match="could not be reached") as caught:
        target.get_text(url, accept="text/csv")
    assert "secret" not in str(caught.value)

    oversized = PublicTextClient(
        GITHUB_UNIVERSE_ENDPOINT,
        session=Session(Response(200, raw=b"x" * (GITHUB_UNIVERSE_ENDPOINT.maximum_bytes + 1))),
        access=access(GITHUB_UNIVERSE_ENDPOINT, Clock(), attempts=1),
    )
    with pytest.raises(PublicReadError, match="parsing limit") as large:
        oversized.get_text(url, accept="text/csv")
    assert large.value.reason_code == "RESPONSE_TOO_LARGE"


@pytest.mark.parametrize("raw", [b"", b"\xff"])
def test_empty_or_non_utf8_public_response_fails_closed(raw):
    url = (
        "https://raw.githubusercontent.com/"
        "datasets/s-and-p-500-companies/main/data/constituents.csv"
    )
    with pytest.raises(PublicReadError, match="unusable") as caught:
        client(
            GITHUB_UNIVERSE_ENDPOINT,
            Session(Response(200, raw=raw)),
            attempts=1,
        ).get_text(url, accept="text/csv")
    assert caught.value.reason_code == "INVALID_RESPONSE"


def test_universe_engine_uses_injected_public_client(monkeypatch):
    monkeypatch.setattr(UniverseEngine, "MIN_SP500", 1)
    fake = FakeTextClient(
        ["Symbol,Security,GICS Sector,GICS Sub-Industry\nAAPL,Apple,Tech,Hardware\n"]
    )

    result = UniverseEngine.get_universe("sp500", public_client=fake)

    assert result["count"] == 1
    assert result["point_in_time"] is False
    assert result["survivorship_safe"] is False
    assert result["replay_eligible"] is False
    assert fake.calls == [
        (
            UniverseEngine.SP500_URL,
            {"accept": "text/csv,text/plain"},
        )
    ]


def test_combined_universe_uses_one_injected_client_for_both_fixed_urls(monkeypatch):
    monkeypatch.setattr(UniverseEngine, "MIN_SP500", 1)
    monkeypatch.setattr(UniverseEngine, "MIN_NASDAQ100", 1)
    fake = FakeTextClient(
        [
            "Symbol,Security,GICS Sector,GICS Sub-Industry\nAAPL,Apple,Tech,Hardware\n",
            "Ticker,Company,GICS_Sector,GICS_Sub_Industry\nMSFT,Microsoft,Tech,Software\n",
        ]
    )

    result = UniverseEngine.get_universe("both", public_client=fake)

    assert result["count"] == 2
    assert [url for url, _ in fake.calls] == [
        UniverseEngine.SP500_URL,
        UniverseEngine.NASDAQ100_URL,
    ]


def test_macro_engine_uses_injected_public_client_without_error_leakage():
    policy = "DATE,FEDFUNDS\n2025-01-01,4.0\n"
    inflation_dates = [
        *(f"2024-{month:02d}-01" for month in range(1, 13)),
        "2025-01-01",
    ]
    inflation = "DATE,CPIAUCSL\n" + "\n".join(
        f"{observed},{101 + index}" for index, observed in enumerate(inflation_dates)
    )
    gdp_dates = [
        "2024-01-01",
        "2024-04-01",
        "2024-07-01",
        "2024-10-01",
        "2025-01-01",
    ]
    gdp = "DATE,GDPC1\n" + "\n".join(
        f"{observed},{100 + index}" for index, observed in enumerate(gdp_dates)
    )
    fake = FakeTextClient([policy, inflation, gdp])

    result = MacroEnvironmentEngine(public_client=fake).analyse()

    assert result["status"] == "COMPLETE"
    assert [call[1]["params"]["id"] for call in fake.calls] == [
        "FEDFUNDS",
        "CPIAUCSL",
        "GDPC1",
    ]
    assert MacroEnvironmentEngine._cached_result is None

    failed = FakeTextClient(
        [PublicReadError("safe", reason_code="TRANSPORT_FAILURE")]
    )
    limited = MacroEnvironmentEngine(public_client=failed).analyse()
    assert limited == {
        "status": "LIMITED",
        "regime": "UNAVAILABLE",
        "reason": "Macro data could not be retrieved safely.",
    }


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_macro_parser_drops_non_finite_provider_values(value):
    assert MacroEnvironmentEngine.parse_series(
        f"DATE,FEDFUNDS\n2025-01-01,{value}\n", "FEDFUNDS"
    ) == []


def test_macro_parser_rejects_calendar_gaps_and_analyse_sanitizes_csv_errors():
    gapped = "DATE,CPIAUCSL\n" + "\n".join(
        [
            "2024-01-01,100",
            "2024-03-01,101",
            *(f"2024-{month:02d}-01,{100 + month}" for month in range(4, 13)),
            "2025-01-01,113",
            "2025-02-01,114",
        ]
    )
    assert MacroEnvironmentEngine.parse_series(gapped, "CPIAUCSL") == []

    malformed = "DATE,FEDFUNDS\n2025-01-01,\"" + "x" * 200_000
    limited = MacroEnvironmentEngine(
        public_client=FakeTextClient([malformed])
    ).analyse()
    assert limited == {
        "status": "LIMITED",
        "regime": "UNAVAILABLE",
        "reason": "Macro data could not be retrieved safely.",
    }


def test_catalyst_news_uses_injected_public_client(tmp_path):
    rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item><title>Apple launches product</title>
    <description>New product</description><link>https://example.com/item</link>
    <pubDate>Thu, 14 Aug 2025 12:00:00 GMT</pubDate></item></channel></rss>"""
    fake = FakeTextClient([rss])
    engine = CatalystEngine(
        cache_path=str(tmp_path / "cache" / "catalysts.json"),
        output_path=str(tmp_path / "output" / "catalysts.json"),
        sec_client=FakeSECClient(),
        public_client=fake,
    )

    items = engine.get_news_rss("AAPL", "Apple")

    assert len(items) == 1
    assert fake.calls[0] == (
        "https://news.google.com/rss/search",
        {
            "params": {
                "q": '"Apple" stock',
                "hl": "en-US",
                "gl": "US",
                "ceid": "US:en",
            },
            "accept": "application/rss+xml,application/xml,text/xml",
        },
    )


@pytest.mark.parametrize("declaration", ["<!DOCTYPE rss>", "<!ENTITY x 'boom'>"])
def test_catalyst_news_rejects_unsafe_xml_declarations(tmp_path, declaration):
    fake = FakeTextClient([f"{declaration}<rss><channel /></rss>"])
    engine = CatalystEngine(
        cache_path=str(tmp_path / "cache" / "catalysts.json"),
        output_path=str(tmp_path / "output" / "catalysts.json"),
        sec_client=FakeSECClient(),
        public_client=fake,
    )

    assert engine.get_news_rss("AAPL") == []


def test_catalyst_news_comment_cannot_hide_entity_declaration(tmp_path):
    rss = """<?xml version="1.0"?>
    <!-- misleading <rss> token -->
    <!DOCTYPE rss [<!ENTITY a "expanded">]>
    <rss><channel><item><title>&a;</title></item></channel></rss>"""
    engine = CatalystEngine(
        cache_path=str(tmp_path / "cache" / "catalysts.json"),
        output_path=str(tmp_path / "output" / "catalysts.json"),
        sec_client=FakeSECClient(),
        public_client=FakeTextClient([rss]),
    )
    assert engine.get_news_rss("AAPL") == []


@pytest.mark.parametrize("prefix", ["<!--" * 5_000, "<?" * 10_000])
def test_catalyst_news_unterminated_prolog_runs_fail_closed(tmp_path, prefix):
    engine = CatalystEngine(
        cache_path=str(tmp_path / "cache" / "catalysts.json"),
        output_path=str(tmp_path / "output" / "catalysts.json"),
        sec_client=FakeSECClient(),
        public_client=FakeTextClient([prefix]),
    )
    assert engine.get_news_rss("AAPL") == []


def test_catalyst_news_does_not_treat_declaration_text_in_content_as_xml_control(tmp_path):
    rss = """<rss><channel><item><title>Literal &lt;!DOCTYPE text</title>
    <description>Literal &lt;!ENTITY text</description></item></channel></rss>"""
    engine = CatalystEngine(
        cache_path=str(tmp_path / "cache" / "catalysts.json"),
        output_path=str(tmp_path / "output" / "catalysts.json"),
        sec_client=FakeSECClient(),
        public_client=FakeTextClient([rss]),
    )
    assert len(engine.get_news_rss("AAPL")) == 1


def test_common_direct_http_get_shapes_under_core_are_confined_to_reviewed_boundaries():
    root = Path(__file__).resolve().parents[1]
    allowed = {
        "core/broker/alpaca_paper_account.py",
        "core/broker/provider_paper_evidence_collection.py",
        "core/data_sources/provider_access.py",
    }
    found: set[str] = set()

    for path in (root / "core").rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            receiver = node.func.value
            direct_requests_get = (
                node.func.attr == "get"
                and isinstance(receiver, ast.Name)
                and receiver.id == "requests"
            )
            session_get = node.func.attr == "get" and (
                (isinstance(receiver, ast.Name) and receiver.id == "session")
                or (
                    isinstance(receiver, ast.Attribute)
                    and receiver.attr == "session"
                    and isinstance(receiver.value, ast.Name)
                    and receiver.value.id == "self"
                )
            )
            if direct_requests_get or session_get:
                found.add(relative)

    assert found == allowed
