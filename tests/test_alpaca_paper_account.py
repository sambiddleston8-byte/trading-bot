import hashlib
import json
import os

import pytest

from core.broker import (
    AlpacaPaperAccountError,
    AlpacaPaperAccountReader,
    AlpacaPaperConfiguration,
    PaperBrokerAccountSnapshotLedger,
)
from core.data_sources.provider_configuration import ProviderConfiguration


PAYLOAD = {
    "id": "paper-account-id",
    "status": "ACTIVE",
    "currency": "USD",
    "cash": "1000",
    "buying_power": "900",
    "equity": "1500",
}


class Response:
    ok = True
    status_code = 200

    def __init__(self, payload=None):
        self.payload = PAYLOAD if payload is None else payload

    def json(self):
        return self.payload


class Session:
    def __init__(self, response=None):
        self.response = response or Response()
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "paper-key")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "paper-secret")
    monkeypatch.setattr(
        ProviderConfiguration, "load_local_environment", classmethod(lambda cls: None)
    )


def test_missing_credentials_is_safe_and_makes_no_request(monkeypatch):
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_API_SECRET", raising=False)
    monkeypatch.setattr(
        ProviderConfiguration, "load_local_environment", classmethod(lambda cls: None)
    )
    session = Session()
    result = AlpacaPaperAccountReader(session=session).read()
    assert result["status"] == "NOT_CONFIGURED"
    assert result["account_read"] is False
    assert result["order_submitted"] is False
    assert result["live_trading_enabled"] is False
    assert session.calls == []


def test_read_uses_only_paper_endpoint_and_secret_headers(credentials):
    session = Session()
    result = AlpacaPaperAccountReader(session=session).read()
    assert result["status"] == "COMPLETE"
    assert result["broker_environment"] == "PAPER"
    assert result["account_reference_sha256"] == hashlib.sha256(
        b"paper-account-id"
    ).hexdigest()
    assert result["raw_payload_returned"] is False
    assert result["settled_cash"] is None
    assert result["unsettled_cash"] is None
    assert result["settlement_breakdown_available"] is False
    assert result["order_submitted"] is False
    url, kwargs = session.calls[0]
    assert url == "https://paper-api.alpaca.markets/v2/account"
    assert "paper-key" not in url and "paper-secret" not in url
    assert kwargs["headers"] == {
        "APCA-API-KEY-ID": "paper-key",
        "APCA-API-SECRET-KEY": "paper-secret",
    }


def test_live_endpoint_is_impossible(credentials):
    with pytest.raises(ValueError, match="official paper"):
        AlpacaPaperAccountReader(
            configuration=AlpacaPaperConfiguration("https://api.alpaca.markets")
        )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {**PAYLOAD, "status": "SUSPENDED"},
        {**PAYLOAD, "currency": "GBP"},
        {**PAYLOAD, "id": ""},
    ],
)
def test_incomplete_or_unsupported_account_fails_closed(credentials, payload):
    reader = AlpacaPaperAccountReader(session=Session(Response(payload)))
    with pytest.raises(AlpacaPaperAccountError):
        reader.read()


def test_http_error_is_secret_safe(credentials):
    response = Response()
    response.ok = False
    response.status_code = 401
    with pytest.raises(AlpacaPaperAccountError) as caught:
        AlpacaPaperAccountReader(session=Session(response)).read()
    assert "paper-key" not in str(caught.value)
    assert "paper-secret" not in str(caught.value)
    assert "401" in str(caught.value)


def test_snapshot_waits_for_separate_settlement_evidence(credentials, tmp_path):
    ledger = PaperBrokerAccountSnapshotLedger(tmp_path / "snapshots.jsonl")
    result = AlpacaPaperAccountReader(session=Session()).record_snapshot(
        ledger, observed_at="2025-01-01T12:00:00+00:00"
    )
    assert result["status"] == "SETTLEMENT_EVIDENCE_REQUIRED"
    assert result["snapshot_recorded"] is False
    assert ledger.records() == []


def test_snapshot_records_when_exact_settlement_evidence_is_supplied(credentials, tmp_path):
    ledger = PaperBrokerAccountSnapshotLedger(tmp_path / "snapshots.jsonl")
    result = AlpacaPaperAccountReader(session=Session()).record_snapshot(
        ledger,
        observed_at="2025-01-01T12:00:00+00:00",
        recorded_at="2025-01-01T12:01:00+00:00",
        settled_cash="900",
        unsettled_cash="100",
    )
    assert result["status"] == "RECORDED"
    assert result["snapshot"]["broker_environment"] == "PAPER"
    assert result["snapshot"]["order_submitted"] is False
    assert ledger.verify() == [result["snapshot"]]
