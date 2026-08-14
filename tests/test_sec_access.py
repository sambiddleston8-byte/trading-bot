from __future__ import annotations

import json

import pytest
import requests

from core.data_quality.sec_evidence_provider import SECEvidenceProvider
from core.data_sources.provider_access import (
    ProviderAccessCoordinator,
    ProviderAccessPolicy,
)
from core.data_sources.sec_access import SECJSONClient, SECProviderError
from core.data_sources.sec_source import SECSource
from core.filings.sec_engine import SECFilingEngine
from core.catalyst_engine import CatalystEngine
from core.sec_historical_fundamentals import SECHistoricalFundamentals


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
    def __init__(self, status: int, payload=None, *, raw=None, headers=None) -> None:
        self.status_code = status
        self.ok = 200 <= status < 300
        self.headers = headers or {}
        self.content = (
            raw
            if raw is not None
            else json.dumps(payload if payload is not None else {}).encode("utf-8")
        )


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


class FakeSECClient:
    def __init__(self, payloads) -> None:
        self.payloads = list(payloads)
        self.urls: list[str] = []

    def get_json(self, url):
        self.urls.append(url)
        value = self.payloads.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture(autouse=True)
def reset_provider_state():
    ProviderAccessCoordinator.reset()
    yield
    ProviderAccessCoordinator.reset()


def access(clock, *, attempts=2, interval=0):
    return ProviderAccessCoordinator.for_provider(
        "SEC_EDGAR",
        "SEC EDGAR",
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


def client(session, clock=None, **changes):
    clock = clock or Clock()
    return SECJSONClient(
        user_agent="SamPat tests test@example.com",
        session=session,
        access=access(clock, **changes),
    )


def test_official_sec_json_uses_no_redirects_and_returns_strict_object():
    session = Session(Response(200, {"ok": True}))
    result = client(session).get_json("https://data.sec.gov/submissions/CIK0000000001.json")

    assert result == {"ok": True}
    assert session.calls == [
        (
            "https://data.sec.gov/submissions/CIK0000000001.json",
            {
                "params": None,
                "headers": {
                    "User-Agent": "SamPat tests test@example.com",
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip, deflate",
                },
                "timeout": 30,
                "allow_redirects": False,
            },
        )
    ]


def test_provider_redirect_option_rejects_ambiguous_values_before_access():
    clock = Clock()
    coordinator = access(clock, attempts=1)
    session = Session(Response(200, {}))

    with pytest.raises(TypeError, match="allow_redirects"):
        coordinator.get(session, "https://example.com", allow_redirects=0)

    assert session.calls == []
    assert clock.sleeps == []


def test_provider_omits_redirect_option_when_caller_does_not_set_it():
    clock = Clock()
    coordinator = access(clock, attempts=1)
    session = Session(Response(200, {}))

    coordinator.get(session, "https://example.com")

    assert "allow_redirects" not in session.calls[0][1]


@pytest.mark.parametrize(
    "url",
    [
        "http://data.sec.gov/submissions/CIK0000000001.json",
        "https://example.com/company_tickers.json",
        "https://data.sec.gov/Archives/not-an-api.json",
        "https://data.sec.gov/submissions/CIK123.json",
        "https://www.sec.gov/files/unapproved.json",
        "https://user@data.sec.gov/submissions/CIK0000000001.json",
        "https://data.sec.gov/submissions/CIK0000000001.json?token=secret",
        "https://data.sec.gov/sub\nmissions/CIK0000000001.json",
        "https://data.sec.gov/sub\tmissions/CIK0000000001.json",
        " https://data.sec.gov/submissions/CIK0000000001.json",
    ],
)
def test_noncanonical_or_non_sec_targets_fail_before_network(url):
    session = Session(Response(200, {}))
    with pytest.raises(SECProviderError, match="target") as caught:
        client(session).get_json(url)
    assert caught.value.reason_code == "INVALID_TARGET"
    assert session.calls == []


def test_retryable_failure_retries_once_but_quota_failure_does_not():
    retried = Session(Response(503), Response(200, {"ok": True}))
    assert client(retried).get_json("https://www.sec.gov/files/company_tickers.json") == {
        "ok": True
    }
    assert len(retried.calls) == 2

    quota = Session(Response(429), Response(200, {"must_not": "run"}))
    with pytest.raises(SECProviderError, match="HTTP 429") as caught:
        client(quota).get_json("https://www.sec.gov/files/company_tickers.json")
    assert caught.value.reason_code == "HTTP_REJECTED"
    assert len(quota.calls) == 1


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_response_body_is_never_accepted_as_sec_data(status):
    session = Session(Response(status, {"must_not": "be trusted"}))

    with pytest.raises(SECProviderError, match=f"HTTP {status}") as caught:
        client(session).get_json("https://data.sec.gov/submissions/CIK0000000001.json")

    assert caught.value.reason_code == "HTTP_REJECTED"


def test_transport_failure_and_open_circuit_never_leak_request_details():
    session = Session(
        requests.ConnectionError("https://data.sec.gov/?secret=do-not-print"),
        requests.ConnectionError("second secret"),
        requests.ConnectionError("third secret"),
        requests.ConnectionError("fourth secret"),
    )
    target = client(session)
    with pytest.raises(SECProviderError, match="could not be reached") as first:
        target.get_json("https://data.sec.gov/submissions/CIK0000000001.json")
    assert "secret" not in str(first.value)
    assert first.value.access["circuit_state"] == "CLOSED"

    with pytest.raises(SECProviderError, match="could not be reached") as second:
        target.get_json("https://data.sec.gov/submissions/CIK0000000001.json")
    assert second.value.access["circuit_state"] == "OPEN"
    with pytest.raises(SECProviderError, match="temporarily paused") as blocked:
        target.get_json("https://data.sec.gov/submissions/CIK0000000001.json")
    assert blocked.value.access["attempts"] == 0


@pytest.mark.parametrize(
    "raw",
    [b'{"a":1,"a":2}', b'{"a":NaN}', b"[]", b"\xff", b""],
)
def test_malformed_or_ambiguous_json_fails_closed(raw):
    with pytest.raises(SECProviderError, match="unusable"):
        client(Session(Response(200, raw=raw))).get_json(
            "https://data.sec.gov/submissions/CIK0000000001.json"
        )


def test_oversized_response_fails_closed(monkeypatch):
    monkeypatch.setattr("core.data_sources.sec_access.SEC_MAX_JSON_BYTES", 3)
    with pytest.raises(SECProviderError, match="parsing limit") as caught:
        client(Session(Response(200, raw=b'{"a":1}'))).get_json(
            "https://data.sec.gov/submissions/CIK0000000001.json"
        )
    assert caught.value.reason_code == "RESPONSE_TOO_LARGE"


@pytest.mark.parametrize(
    "user_agent",
    ["bot\r\nX-Injected: 1", "bot\x00contact@example.com", "bot\té@example.com"],
)
def test_user_agent_rejects_control_or_non_ascii_characters(user_agent):
    with pytest.raises(ValueError, match="user agent"):
        SECJSONClient(user_agent=user_agent, session=Session(Response(200, {})))


def test_shared_sec_coordinator_paces_separate_clients():
    clock = Clock()
    first_session = Session(Response(200, {"first": True}))
    second_session = Session(Response(200, {"second": True}))
    first = client(first_session, clock, attempts=1, interval=1)
    second = client(second_session, clock, attempts=1, interval=1)

    first.get_json("https://data.sec.gov/submissions/CIK0000000001.json")
    second.get_json("https://data.sec.gov/submissions/CIK0000000002.json")
    assert clock.sleeps == [1.0]


def test_sec_source_uses_injected_safe_client_for_mapping_and_facts():
    safe = FakeSECClient(
        [
            {"0": {"ticker": "AAPL", "cik_str": 320193}},
            {"entityName": "Apple", "facts": {}},
        ]
    )
    source = SECSource(sec_client=safe)
    assert source.resolve_cik("aapl") == "0000320193"
    assert source.fetch_company_facts("320193")["entityName"] == "Apple"
    assert safe.urls == [
        "https://www.sec.gov/files/company_tickers.json",
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
    ]


def test_historical_sec_path_uses_injected_safe_client_without_raw_error(capsys):
    safe = FakeSECClient(
        [
            {"0": {"ticker": "AAPL", "cik_str": 320193}},
            {"facts": {}},
        ]
    )
    engine = SECHistoricalFundamentals(sec_client=safe, cache=object())
    assert engine.get_company_facts("AAPL") == {"facts": {}}
    assert "secret" not in capsys.readouterr().out


def test_historical_ticker_shape_mismatch_degrades_to_known_fallbacks(capsys):
    safe = FakeSECClient(
        [
            {},
            {"fields": ["cik", "ticker"], "data": [[320193, "AAPL"]]},
        ]
    )

    engine = SECHistoricalFundamentals(sec_client=safe, cache=object())

    assert engine.company_tickers["AAPL"] == "0000320193"
    assert "SEC mapping failed." in capsys.readouterr().out


def test_filing_engine_uses_injected_safe_client_for_all_sec_json():
    safe = FakeSECClient(
        [
            {"0": {"ticker": "AAPL", "cik_str": 320193}},
            {
                "filings": {
                    "recent": {
                        "form": ["10-K"],
                        "accessionNumber": ["1-2-3"],
                        "filingDate": ["2025-01-01"],
                        "reportDate": ["2024-12-31"],
                        "primaryDocument": ["annual.htm"],
                    }
                }
            },
        ]
    )

    filings = SECFilingEngine(sec_client=safe).get_filings("AAPL")

    assert [item["Form"] for item in filings] == ["10-K"]
    assert safe.urls == [
        "https://www.sec.gov/files/company_tickers.json",
        "https://data.sec.gov/submissions/CIK0000320193.json",
    ]


def test_catalyst_engine_uses_injected_safe_client_for_all_sec_json(tmp_path):
    safe = FakeSECClient(
        [
            {"0": {"ticker": "AAPL", "cik_str": 320193}},
            {
                "filings": {
                    "recent": {
                        "form": ["8-K"],
                        "filingDate": ["2025-01-01"],
                        "accessionNumber": ["1-2-3"],
                        "primaryDocument": ["event.htm"],
                        "primaryDocDescription": ["Event"],
                    }
                }
            },
        ]
    )
    engine = CatalystEngine(
        cache_path=str(tmp_path / "cache" / "catalysts.json"),
        output_path=str(tmp_path / "output" / "catalysts.json"),
        sec_client=safe,
    )

    filings = engine.get_sec_filings("AAPL")

    assert [item["Form"] for item in filings] == ["8-K"]
    assert safe.urls == [
        "https://www.sec.gov/files/company_tickers.json",
        "https://data.sec.gov/submissions/CIK0000320193.json",
    ]


def test_legacy_evidence_provider_delegates_to_safe_client(monkeypatch):
    safe = FakeSECClient([{"0": {"ticker": "AAPL", "cik_str": 320193}}])
    monkeypatch.setattr(
        "core.data_quality.sec_evidence_provider.SECJSONClient",
        lambda **kwargs: safe,
    )
    assert SECEvidenceProvider.get_cik("AAPL") == "0000320193"
    assert safe.urls == ["https://www.sec.gov/files/company_tickers.json"]
