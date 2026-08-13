import json

import pytest

from core.orchestration.authenticated_replay_artifact import load_authenticated_replay_artifact


class VerifiedAdmission:
    def __init__(self, record): self.record = record
    def verify(self): return [self.record]


class VerifiedContent:
    def __init__(self, record, payload): self.record, self.payload = record, payload
    def read_verified(self, content_id):
        if content_id != self.record["content_evidence_id"]: raise ValueError("missing")
        return self.record, self.payload


def source(role, coverage, rows):
    payload = json.dumps({
        "schema_version": "1.0", "artifact_role": role,
        "coverage": coverage, "rows": rows,
    }, separators=(",", ":")).encode()
    content = {
        "content_evidence_id": "SRC-1", "record_hash": "c" * 64,
        "source_input_sha256": "d" * 64,
    }
    admission = {
        "admission_id": "RDA-1", "record_hash": "a" * 64,
        "replay_plan_id": "REPLAY-1", "replay_plan_record_hash": "b" * 64,
        "dataset_commitment_sha256": "e" * 64,
        "artifacts": [{"role": role, "content_evidence_id": "SRC-1"}],
    }
    return VerifiedAdmission(admission), VerifiedContent(content, payload)


COVERAGE = {
    "tickers": ["AAPL"], "covers_from_at": "2022-04-01T00:00:00Z",
    "through_at": "2022-04-02T00:00:00Z", "provider_declared_completeness": "COMPLETE",
}


def load(role, rows, coverage=COVERAGE):
    admission, content = source(role, coverage, rows)
    return load_authenticated_replay_artifact(
        admission_ledger=admission, content_ledger=content,
        admission_id="RDA-1", role=role,
    )


def test_prices_are_normalized_from_authenticated_bytes_without_execution():
    result = load("TOTAL_RETURN_PRICES", [{
        "ticker": "aapl", "observed_at": "2022-04-01T14:30:00-04:00",
        "available_at": "2022-04-01T18:30:01Z", "bid": "99.00", "ask": "101",
        "last": "100.0", "volume": "0", "source_row_locator": "row-1",
    }])
    assert result["rows"][0]["observed_at"] == "2022-04-01T18:30:00+00:00"
    assert result["rows"][0]["bid"] == "99"
    assert result["source_bytes_authenticated"] is True
    assert result["coverage_completeness_proven"] is False
    for field in (
        "observation_selected", "replay_executed", "costs_calculated", "fills_generated",
        "performance_calculated", "model_trained", "learning_applied", "network_allowed",
        "broker_connection_allowed", "orders_submitted", "live_trading_enabled",
    ): assert result[field] is False


def test_crossed_or_stale_price_rows_remain_source_evidence():
    result = load("TOTAL_RETURN_PRICES", [{
        "ticker": "AAPL", "observed_at": "2022-04-01T10:00:00Z",
        "available_at": "2022-04-03T10:00:00Z", "bid": "102", "ask": "100",
        "last": "103", "volume": "1", "source_row_locator": "row-1",
    }])
    assert result["rows"][0]["available_at"] == "2022-04-03T10:00:00+00:00"


@pytest.mark.parametrize("field,value", [
    ("bid", "Infinity"), ("ask", "NaN"), ("last", "1E+2"),
    ("volume", "0.0000000000001"),
])
def test_nonfinite_exponential_or_overprecise_numbers_fail(field, value):
    row = {
        "ticker": "AAPL", "observed_at": "2022-04-01T10:00:00Z",
        "available_at": "2022-04-01T10:00:01Z", "bid": "99", "ask": "101",
        "last": "100", "volume": "1", "source_row_locator": "row-1",
    }
    row[field] = value
    with pytest.raises(ValueError): load("TOTAL_RETURN_PRICES", [row])


def test_calendar_must_tile_every_declared_ticker_exactly():
    result = load("MARKET_CALENDARS_AND_HALTS", [{
        "ticker": "AAPL", "starts_at": "2022-04-01T00:00:00Z",
        "ends_at": "2022-04-02T00:00:00Z", "status": "CLOSED",
        "source_row_locator": "calendar-1",
    }])
    assert result["calendar_interval_coverage_proven"] is True


@pytest.mark.parametrize("rows", [[], [{
    "ticker": "AAPL", "starts_at": "2022-04-01T00:00:00Z",
    "ends_at": "2022-04-01T12:00:00Z", "status": "OPEN",
    "source_row_locator": "calendar-1",
}]])
def test_missing_or_gapped_calendar_rejects(rows):
    with pytest.raises(ValueError, match="calendar"):
        load("MARKET_CALENDARS_AND_HALTS", rows)


def test_corporate_action_identity_is_unique():
    row = {
        "ticker": "AAPL", "event_id": "event-1", "event_type": "SPLIT",
        "effective_at": "2022-04-01T12:00:00Z", "available_at": "2022-04-01T12:01:00Z",
        "source_row_locator": "event-row",
    }
    with pytest.raises(ValueError, match="identities"):
        load("CORPORATE_ACTIONS", [row, {**row, "source_row_locator": "event-row-2"}])


def test_only_one_delisting_outcome_per_ticker():
    first = {
        "ticker": "AAPL", "event_id": "event-1", "status": "ACQUIRED",
        "effective_at": "2022-04-01T12:00:00Z", "available_at": "2022-04-01T12:01:00Z",
        "source_row_locator": "event-row-1",
    }
    second = {**first, "event_id": "event-2", "source_row_locator": "event-row-2"}
    with pytest.raises(ValueError, match="one terminal"):
        load("DELISTING_OUTCOMES", [first, second])


def test_rows_must_be_sorted_and_tickers_declared():
    coverage = {**COVERAGE, "tickers": ["AAPL", "MSFT"]}
    rows = [{
        "ticker": ticker, "observed_at": "2022-04-01T10:00:00Z",
        "available_at": "2022-04-01T10:00:01Z", "bid": "99", "ask": "101",
        "last": "100", "volume": "1", "source_row_locator": ticker,
    } for ticker in ("MSFT", "AAPL")]
    with pytest.raises(ValueError, match="sorted"):
        load("TOTAL_RETURN_PRICES", rows, coverage)


def test_naive_timestamps_and_unknown_fields_fail():
    row = {
        "ticker": "AAPL", "observed_at": "2022-04-01T10:00:00",
        "available_at": "2022-04-01T10:00:01Z", "bid": "99", "ask": "101",
        "last": "100", "volume": "1", "source_row_locator": "row-1", "extra": True,
    }
    with pytest.raises(ValueError): load("TOTAL_RETURN_PRICES", [row])


def test_text_fields_do_not_coerce_json_numbers_or_booleans():
    row = {
        "ticker": "AAPL", "observed_at": "2022-04-01T10:00:00Z",
        "available_at": "2022-04-01T10:00:01Z", "bid": "99", "ask": "101",
        "last": "100", "volume": "1", "source_row_locator": True,
    }
    with pytest.raises(ValueError, match="must be text"):
        load("TOTAL_RETURN_PRICES", [row])


def test_unsupported_role_and_unknown_admission_fail():
    admission, content = source("TOTAL_RETURN_PRICES", COVERAGE, [])
    with pytest.raises(ValueError, match="outside"):
        load_authenticated_replay_artifact(
            admission_ledger=admission, content_ledger=content,
            admission_id="RDA-1", role="POINT_IN_TIME_FUNDAMENTALS",
        )
    with pytest.raises(ValueError, match="verified"):
        load_authenticated_replay_artifact(
            admission_ledger=admission, content_ledger=content,
            admission_id="RDA-MISSING", role="TOTAL_RETURN_PRICES",
        )


def test_verified_admission_missing_requested_role_fails_explicitly():
    admission, content = source("TOTAL_RETURN_PRICES", COVERAGE, [])
    admission.record["artifacts"] = []
    with pytest.raises(ValueError, match="does not contain"):
        load_authenticated_replay_artifact(
            admission_ledger=admission, content_ledger=content,
            admission_id="RDA-1", role="TOTAL_RETURN_PRICES",
        )
