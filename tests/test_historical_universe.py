import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.portfolio.historical_universe import HistoricalUniverseEventLedger


def ledger(tmp_path):
    return HistoricalUniverseEventLedger(tmp_path / "universe_events.jsonl")


def event(book, ticker="AAA", event_type="ADDED", effective_date="2020-01-02", **overrides):
    values = {
        "universe": "SP500", "ticker": ticker, "issuer_name": f"{ticker} Inc",
        "event_type": event_type, "effective_date": effective_date,
        "publicly_available_at": "2020-01-01T12:00:00+00:00",
        "retrieved_at": "2020-01-01T13:00:00+00:00",
        "recorded_at": "2020-01-01T14:00:00+00:00",
        "source_uri": f"https://index.example/{ticker}/{event_type}",
        "source_input_sha256": ("a" if event_type == "ADDED" else "b") * 64,
        "source_locator": f"$.{ticker}.{event_type}",
    }
    values.update(overrides)
    return book.record_event(**values)


def test_reconstructs_membership_and_retains_removal(tmp_path):
    book = ledger(tmp_path)
    added = event(book)
    removed = event(
        book, event_type="REMOVED", effective_date="2021-01-02",
        publicly_available_at="2021-01-01T12:00:00+00:00",
        retrieved_at="2021-01-01T13:00:00+00:00", recorded_at="2021-01-01T14:00:00+00:00",
    )
    before = book.snapshot(
        universe="SP500", effective_as_of="2020-06-01T00:00:00+00:00",
        known_as_of="2020-06-01T00:00:00+00:00",
    )
    after = book.snapshot(
        universe="SP500", effective_as_of="2021-06-01T00:00:00+00:00",
        known_as_of="2021-06-01T00:00:00+00:00",
    )
    assert before["members"][0]["added_event_id"] == added["event_id"]
    assert after["members"] == []
    assert after["exclusions_retained"][0]["exit_event_id"] == removed["event_id"]
    assert after["current_membership_used"] is False
    assert after["performance_calculated"] is False
    assert after["coverage_completeness_proven"] is False
    assert after["terminal_outcome_evidence_complete"] is False
    assert after["survivorship_safe_replay_ready"] is False


def test_late_known_event_is_not_used_before_knowledge_cutoff(tmp_path):
    book = ledger(tmp_path)
    event(
        book, publicly_available_at="2020-02-01T12:00:00+00:00",
        retrieved_at="2020-02-01T13:00:00+00:00", recorded_at="2020-02-01T14:00:00+00:00",
    )
    snapshot = book.snapshot(
        universe="SP500", effective_as_of="2020-03-01T00:00:00+00:00",
        known_as_of="2020-01-15T00:00:00+00:00",
    )
    assert snapshot["members"] == []
    assert snapshot["supporting_event_ids"] == []


def test_delisting_requires_terminal_outcome_treatment(tmp_path):
    book = ledger(tmp_path)
    event(book)
    with pytest.raises(ValueError, match="explicit terminal"):
        event(
            book, event_type="DELISTED", effective_date="2021-01-02",
            publicly_available_at="2021-01-01T12:00:00+00:00",
            retrieved_at="2021-01-01T13:00:00+00:00", recorded_at="2021-01-01T14:00:00+00:00",
        )
    delisted = event(
        book, event_type="DELISTED", effective_date="2021-01-02",
        publicly_available_at="2021-01-01T12:00:00+00:00",
        retrieved_at="2021-01-01T13:00:00+00:00", recorded_at="2021-01-01T14:00:00+00:00",
        terminal_outcome_treatment="BANKRUPTCY_OR_LIQUIDATION_OUTCOME_REQUIRED",
    )
    assert delisted["terminal_outcome_evidence_required"] is True


@pytest.mark.parametrize("change", [
    {"performance_claim_allowed": True}, {"survivorship_safe_replay_ready": True},
    {"broker_submission_enabled": True}, {"live_trading_enabled": True},
])
def test_rehashed_tampering_cannot_create_authority(tmp_path, change):
    book = ledger(tmp_path)
    event(book)
    from core.portfolio import historical_universe as module
    record = json.loads(book.path.read_text()); record.update(change)
    material = {k:v for k,v in record.items() if k != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    book.path.write_text(json.dumps(record) + "\n")
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        book.verify()


def test_invalid_transition_fails_before_append(tmp_path):
    book = ledger(tmp_path)
    with pytest.raises(LedgerIntegrityError, match="transition"):
        event(book, event_type="REMOVED")
    first = event(book)
    assert first["previous_hash"] == GENESIS_HASH
    with pytest.raises(LedgerIntegrityError, match="transition"):
        event(book, source_input_sha256="c" * 64)
