from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys

import pytest

import core.orchestration.massive_historical_adapter as module
from core.data_sources.provider_access import ProviderAttemptMetadata, ProviderHTTPResult
from core.orchestration.massive_historical_adapter import (
    MassiveHistoricalAdapter,
    MassiveHistoricalIngestError,
    MassiveHistoricalSampleClient,
    MassiveHistoricalSource,
    MassiveHistoricalStagingBatch,
)


RETRIEVED_AT = "2026-08-15T10:00:00+00:00"
BAR_TIME = 1_754_995_200_000
API_KEY = "test-key-that-must-never-appear-in-an-error"


def response_payload(*, adjusted: bool = False, results: list[dict] | None = None, **extra) -> bytes:
    bars = results or [
        {
            "o": 100.0,
            "h": 102.0,
            "l": 99.0,
            "c": 101.0,
            "v": 1_000,
            "t": BAR_TIME,
            "n": 250,
            "vw": 100.75,
        }
    ]
    value = {
        "adjusted": adjusted,
        "queryCount": len(bars),
        "request_id": "synthetic-request",
        "results": bars,
        "resultsCount": len(bars),
        "status": "OK",
        "ticker": "AAPL",
        **extra,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def source(payload: bytes | None = None, *, transport: str = "JSON") -> MassiveHistoricalSource:
    return MassiveHistoricalSource(
        transport_format=transport,
        retrieved_at=RETRIEVED_AT,
        payload_bytes=payload or response_payload(),
    )


def normalize(value: MassiveHistoricalSource | None = None, *, decision_at: str = RETRIEVED_AT):
    return MassiveHistoricalAdapter().normalize(
        source=value or source(),
        decision_at=decision_at,
    )


def test_synthetic_json_is_staged_through_canonical_cutoff_schema():
    original = source()

    result = normalize(original)

    summary = result.as_dict()
    assert summary["provider_id"] == "MASSIVE"
    assert summary["provider_dataset_id"] == "MASSIVE_STOCKS_CUSTOM_BARS_V2"
    assert summary["role"] == "RAW_DAILY_SESSION_BARS"
    assert summary["record_count"] == 1
    assert summary["source_payload_sha256"] == hashlib.sha256(original.payload_bytes).hexdigest()
    assert summary["provider_payload_semantics_qualified"] is False
    assert summary["source_bytes_authenticated"] is False
    assert summary["role_coverage_validated"] is False
    assert summary["engine_input_ready"] is False
    assert summary["replay_executed"] is False
    assert summary["broker_connection_allowed"] is False
    assert summary["orders_submitted"] is False
    assert summary["live_trading_enabled"] is False

    observation = result.observations[0]
    assert observation["effective_at"] == "2026-08-15T10:00:00.000000+00:00"
    assert observation["available_at"] == observation["effective_at"]
    assert observation["retrieved_at"] == observation["effective_at"]
    assert observation["observation_cutoff_at"] == observation["effective_at"]
    assert observation["payload"]["bar_window_start_at"] == "2025-08-12T10:40:00.000+00:00"
    assert observation["payload"]["available_at_basis"] == "LOCAL_RETRIEVAL_ONLY_UNQUALIFIED"
    assert observation["payload"]["effective_at_basis"] == "LOCAL_RETRIEVAL_ONLY_UNQUALIFIED"
    assert observation["payload"]["adjusted"] is False


def test_exact_csv_projection_uses_the_same_synthetic_normalization_logic():
    csv_payload = (
        b"ticker,adjusted,o,h,l,c,v,t,n,vw,otc\n"
        b"AAPL,false,100,102,99,101,1000,1754995200000,250,100.75,\n"
    )

    json_result = normalize()
    result = normalize(source(csv_payload, transport="CSV"))

    payload = result.observations[0]["payload"]
    assert payload["ticker"] == "AAPL"
    assert payload["close"] == 101.0
    assert payload["transaction_count"] == 250
    assert "otc" not in payload
    assert (
        result.observations[0]["normalized_payload_sha256"]
        == json_result.observations[0]["normalized_payload_sha256"]
    )
    assert result.source_payload_sha256 != json_result.source_payload_sha256
    assert result.staging_sha256 != json_result.staging_sha256


def test_provider_bar_timestamp_is_not_invented_as_historical_availability():
    with pytest.raises(ValueError, match="available_at is after the decision"):
        normalize(decision_at="2026-08-15T09:59:59.999999+00:00")


def test_future_provider_bar_window_is_rejected_even_at_a_current_decision():
    future_bar = {
        "o": 100,
        "h": 102,
        "l": 99,
        "c": 101,
        "v": 1,
        "t": 1_893_456_000_000,
    }

    with pytest.raises(ValueError, match="cannot start at or after local retrieval"):
        normalize(source(response_payload(results=[future_bar])))


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (response_payload(adjusted=True), "adjusted Massive bars"),
        (response_payload(next_url="https://api.massive.com/page/2"), "incomplete"),
        (
            response_payload(results=[{"o": 100, "h": 99, "l": 98, "c": 101, "v": 1, "t": BAR_TIME}]),
            "OHLC values are inconsistent",
        ),
        (response_payload(resultsCount=2), "resultsCount"),
    ],
)
def test_unsupported_or_inconsistent_provider_assumptions_fail_closed(payload, message):
    with pytest.raises(ValueError, match=message):
        normalize(source(payload))


def test_duplicate_derived_record_identity_is_rejected():
    bar = {"o": 100, "h": 102, "l": 99, "c": 101, "v": 1, "t": BAR_TIME}

    with pytest.raises(ValueError, match="repeat a provider record identity"):
        normalize(source(response_payload(results=[bar, dict(bar)])))


def test_csv_cannot_mix_provider_response_tickers():
    csv_payload = (
        b"ticker,adjusted,o,h,l,c,v,t,n,vw,otc\n"
        b"AAPL,false,100,102,99,101,1000,1754995200000,250,100.75,false\n"
        b"MSFT,false,100,102,99,101,1000,1755081600000,250,100.75,false\n"
    )

    with pytest.raises(ValueError, match="cannot mix tickers"):
        normalize(source(csv_payload, transport="CSV"))


def test_staging_batch_authority_and_false_safety_flags_cannot_be_forged():
    required = {
        "decision_at": RETRIEVED_AT,
        "retrieved_at": RETRIEVED_AT,
        "observations": (),
        "source_payload_sha256": "0" * 64,
        "staging_sha256": "1" * 64,
    }
    with pytest.raises(PermissionError):
        MassiveHistoricalStagingBatch(**required)
    with pytest.raises(ValueError, match="safety flags were altered"):
        MassiveHistoricalStagingBatch(
            **required,
            engine_input_ready=True,
            _authority=module._STAGING_AUTHORITY,
        )


def test_source_and_record_limits_fail_closed():
    with pytest.raises(ValueError, match="bounded nonempty bytes"):
        source(b"x" * (module.MAX_SOURCE_BYTES + 1))
    bar = {"o": 100, "h": 102, "l": 99, "c": 101, "v": 1, "t": BAR_TIME}
    with pytest.raises(ValueError, match="bounded nonempty list"):
        normalize(source(response_payload(results=[dict(bar, t=BAR_TIME + index) for index in range(121)])))


class Response:
    def __init__(self, payload: bytes, status_code: int = 200):
        self.content = payload
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json; charset=utf-8"}


class Access:
    def __init__(self, response: Response):
        self.response = response
        self.calls: list[tuple] = []

    def get(self, session, url, **kwargs):
        self.calls.append((session, url, kwargs))
        return ProviderHTTPResult(
            response=self.response,
            metadata=ProviderAttemptMetadata(
                provider="Massive historical sample",
                attempts=1,
                retry_count=0,
                retried_status_codes=(),
                total_wait_seconds=0.0,
                elapsed_seconds=0.01,
                circuit_state="CLOSED",
            ),
        )


def client(access: Access) -> MassiveHistoricalSampleClient:
    return MassiveHistoricalSampleClient(
        API_KEY,
        session=object(),
        access=access,
        clock=lambda: datetime(2026, 8, 15, 10, tzinfo=timezone.utc),
    )


def test_api_fetch_is_one_bounded_fixed_host_request_with_header_auth_and_no_redirects():
    access = Access(Response(response_payload()))

    result = client(access).fetch_daily_bars(
        symbol="AAPL", start="2026-08-01", end="2026-08-08"
    )

    assert result.payload_bytes == response_payload()
    assert result.retrieved_at == "2026-08-15T10:00:00.000000+00:00"
    assert len(access.calls) == 1
    _, url, kwargs = access.calls[0]
    assert url == "https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/2026-08-01/2026-08-08"
    assert kwargs == {
        "params": {"adjusted": "false", "sort": "asc", "limit": 120},
        "headers": {"Authorization": f"Bearer {API_KEY}"},
        "timeout": 20,
        "allow_redirects": False,
    }
    assert result.request_uri == url
    assert result.request_query_canonical == "adjusted=false&limit=120&sort=asc"
    assert result.response_status_code == 200
    assert result.media_type == "application/json"
    assert result.requested_at == "2026-08-15T10:00:00.000000+00:00"
    assert result.retrieved_at == "2026-08-15T10:00:00.000000+00:00"
    assert result.provider_access["request_headers_recorded"] is False


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 403, 429, 500])
def test_api_failure_is_secret_safe_and_never_accepts_redirects(status):
    access = Access(Response(b'{"error":"secret body"}', status))

    with pytest.raises(MassiveHistoricalIngestError) as caught:
        client(access).fetch_daily_bars(
            symbol="AAPL", start="2026-08-01", end="2026-08-02"
        )

    assert API_KEY not in str(caught.value)
    assert "secret body" not in str(caught.value)


def test_api_sample_range_and_symbol_are_bounded_before_access():
    access = Access(Response(response_payload()))
    instance = client(access)

    with pytest.raises(ValueError, match="at most 31 days"):
        instance.fetch_daily_bars(symbol="AAPL", start="2026-01-01", end="2026-03-01")
    with pytest.raises(ValueError, match="canonical uppercase"):
        instance.fetch_daily_bars(symbol="../AAPL", start="2026-08-01", end="2026-08-02")
    with pytest.raises(ValueError, match="must end before"):
        instance.fetch_daily_bars(symbol="AAPL", start="2026-08-14", end="2026-08-15")
    assert access.calls == []


def test_module_has_no_broker_execution_or_order_surface():
    tree = ast.parse(inspect.getsource(module))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    text = inspect.getsource(module).lower()

    assert not any("broker" in name or "execution" in name for name in imports)
    assert "submit_order" not in text
    assert "cancel_order" not in text
    assert "paper-api.alpaca" not in text


def test_local_cli_stages_a_synthetic_file_without_credentials_or_network(tmp_path: Path):
    sample = tmp_path / "massive-sample.json"
    sample.write_bytes(response_payload())

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/ingest_massive_historical_sample.py",
            "--sample-file",
            str(sample),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={},
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["provider_id"] == "MASSIVE"
    assert summary["record_count"] == 1
    assert summary["engine_input_ready"] is False
    assert summary["broker_connection_allowed"] is False
