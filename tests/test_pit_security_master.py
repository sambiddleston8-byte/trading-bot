import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.portfolio.pit_security_master import PointInTimeSecurityMasterLedger


def ledger(tmp_path):
    return PointInTimeSecurityMasterLedger(tmp_path / "security_master.jsonl")


def record(
    book,
    *,
    security_id="SEC-OLD-001",
    event_type="LISTED",
    ticker="AAA",
    effective_at="2020-01-02T00:00:00+00:00",
    marker="a",
    **overrides,
):
    values = {
        "security_id": security_id,
        "event_type": event_type,
        "ticker": ticker,
        "issuer_name": f"{security_id} issuer",
        "exchange_mic": "XNYS",
        "effective_at": effective_at,
        "reported_at": "2020-01-01T10:00:00+00:00",
        "available_at": "2020-01-01T11:00:00+00:00",
        "retrieved_at": "2020-01-01T12:00:00+00:00",
        "recorded_at": "2020-01-01T13:00:00+00:00",
        "source_uri": f"https://master.example/{security_id}/{event_type}",
        "source_input_sha256": marker * 64,
        "source_locator": f"$.{security_id}.{event_type}",
    }
    values.update(overrides)
    return book.record_event(**values)


def add_to_sp500(book, *, security_id="SEC-OLD-001", ticker="AAA", marker="b", **overrides):
    values = {
        "effective_at": "2020-01-03T00:00:00+00:00",
        "universe": "SP500",
        "reported_at": "2020-01-02T10:00:00+00:00",
        "available_at": "2020-01-02T11:00:00+00:00",
        "retrieved_at": "2020-01-02T12:00:00+00:00",
        "recorded_at": "2020-01-02T13:00:00+00:00",
    }
    values.update(overrides)
    return record(
        book, security_id=security_id, event_type="INDEX_ADDED", ticker=ticker,
        marker=marker, **values,
    )


def delist_old(book, **overrides):
    values = {
        "effective_at": "2021-06-01T20:00:00+00:00",
        "terminal_outcome_treatment": "BANKRUPTCY_OR_LIQUIDATION_OUTCOME_REQUIRED",
        "reported_at": "2021-05-28T10:00:00+00:00",
        "available_at": "2021-05-28T11:00:00+00:00",
        "retrieved_at": "2021-05-28T12:00:00+00:00",
        "recorded_at": "2021-05-28T13:00:00+00:00",
    }
    values.update(overrides)
    return record(book, event_type="DELISTED", marker="c", **values)


def test_permanent_identity_snapshot_retains_delisted_member_through_boundary(tmp_path):
    book = ledger(tmp_path)
    listed = record(book)
    membership = add_to_sp500(book)
    delisted = delist_old(book)

    before = book.snapshot(
        universe="SP500",
        effective_as_of="2021-06-01T19:59:59+00:00",
        known_as_of="2021-06-01T19:59:59+00:00",
    )
    after = book.snapshot(
        universe="SP500",
        effective_as_of="2021-06-01T20:00:00+00:00",
        known_as_of="2021-06-01T20:00:00+00:00",
    )

    assert before["members"] == [{
        "security_id": "SEC-OLD-001",
        "ticker": "AAA",
        "issuer_name": "SEC-OLD-001 issuer",
        "exchange_mic": "XNYS",
        "listing_effective_at": "2020-01-02T00:00:00+00:00",
        "listing_event_id": listed["event_id"],
        "membership_event_id": membership["event_id"],
        "membership_event_record_hash": membership["record_hash"],
    }]
    assert after["members"] == []
    assert after["exclusions_retained"] == [{
        "security_id": "SEC-OLD-001",
        "ticker": "AAA",
        "issuer_name": "SEC-OLD-001 issuer",
        "exit_type": "DELISTED",
        "exit_effective_at": "2021-06-01T20:00:00+00:00",
        "exit_event_id": delisted["event_id"],
        "exit_event_record_hash": delisted["record_hash"],
        "terminal_outcome_treatment": "BANKRUPTCY_OR_LIQUIDATION_OUTCOME_REQUIRED",
    }]
    assert after["permanent_identity_used"] is True
    assert after["current_membership_used"] is False
    assert after["partition_admission_authorized"] is False
    assert after["performance_claim_allowed"] is False


def test_ticker_reuse_resolves_to_different_permanent_security_by_time(tmp_path):
    book = ledger(tmp_path)
    record(book)
    add_to_sp500(book)
    delist_old(book)
    record(
        book,
        security_id="SEC-NEW-002",
        ticker="AAA",
        effective_at="2021-06-01T20:00:00+00:00",
        marker="d",
        reported_at="2021-05-29T10:00:00+00:00",
        available_at="2021-05-29T11:00:00+00:00",
        retrieved_at="2021-05-29T12:00:00+00:00",
        recorded_at="2021-05-29T13:00:00+00:00",
    )
    add_to_sp500(
        book,
        security_id="SEC-NEW-002",
        ticker="AAA",
        effective_at="2021-06-02T00:00:00+00:00",
        marker="e",
        reported_at="2021-06-01T10:00:00+00:00",
        available_at="2021-06-01T11:00:00+00:00",
        retrieved_at="2021-06-01T12:00:00+00:00",
        recorded_at="2021-06-01T13:00:00+00:00",
    )

    assert book.resolve_ticker(
        ticker="AAA",
        effective_as_of="2021-05-31T00:00:00+00:00",
        known_as_of="2021-05-31T00:00:00+00:00",
    ) == "SEC-OLD-001"
    assert book.resolve_ticker(
        ticker="AAA",
        effective_as_of="2021-06-02T00:00:00+00:00",
        known_as_of="2021-06-02T00:00:00+00:00",
    ) == "SEC-NEW-002"
    snapshot = book.snapshot(
        universe="SP500",
        effective_as_of="2021-06-02T00:00:00+00:00",
        known_as_of="2021-06-02T00:00:00+00:00",
    )
    assert [(item["security_id"], item["ticker"]) for item in snapshot["members"]] == [
        ("SEC-NEW-002", "AAA")
    ]
    assert snapshot["exclusions_retained"][0]["security_id"] == "SEC-OLD-001"


def test_active_ticker_overlap_is_rejected_even_across_security_ids(tmp_path):
    book = ledger(tmp_path)
    record(book)
    with pytest.raises(LedgerIntegrityError, match="overlapping permanent"):
        record(
            book,
            security_id="SEC-NEW-002",
            ticker="AAA",
            effective_at="2021-01-01T00:00:00+00:00",
            marker="d",
            reported_at="2020-12-01T10:00:00+00:00",
            available_at="2020-12-01T11:00:00+00:00",
            retrieved_at="2020-12-01T12:00:00+00:00",
            recorded_at="2020-12-01T13:00:00+00:00",
        )
    assert len(book.verify()) == 1


def test_ticker_change_preserves_identity_and_requires_active_prior_ticker(tmp_path):
    book = ledger(tmp_path)
    record(book)
    add_to_sp500(book)
    record(
        book,
        event_type="TICKER_CHANGED",
        ticker="AAB",
        prior_ticker="AAA",
        effective_at="2021-01-01T00:00:00+00:00",
        marker="f",
        reported_at="2020-12-01T10:00:00+00:00",
        available_at="2020-12-01T11:00:00+00:00",
        retrieved_at="2020-12-01T12:00:00+00:00",
        recorded_at="2020-12-01T13:00:00+00:00",
    )
    snapshot = book.snapshot(
        universe="SP500",
        effective_as_of="2021-02-01T00:00:00+00:00",
        known_as_of="2021-02-01T00:00:00+00:00",
    )
    assert snapshot["members"][0]["security_id"] == "SEC-OLD-001"
    assert snapshot["members"][0]["ticker"] == "AAB"
    assert book.resolve_ticker(
        ticker="AAA",
        effective_as_of="2021-02-01T00:00:00+00:00",
        known_as_of="2021-02-01T00:00:00+00:00",
    ) is None


def test_late_availability_cannot_leak_a_future_known_event(tmp_path):
    book = ledger(tmp_path)
    record(book)
    add_to_sp500(book)
    delist_old(
        book,
        available_at="2021-06-03T11:00:00+00:00",
        retrieved_at="2021-06-03T12:00:00+00:00",
        recorded_at="2021-06-03T13:00:00+00:00",
    )
    still_known_as_member = book.snapshot(
        universe="SP500",
        effective_as_of="2021-06-02T00:00:00+00:00",
        known_as_of="2021-06-02T00:00:00+00:00",
    )
    assert still_known_as_member["members"][0]["security_id"] == "SEC-OLD-001"
    after_publication = book.snapshot(
        universe="SP500",
        effective_as_of="2021-06-04T00:00:00+00:00",
        known_as_of="2021-06-04T00:00:00+00:00",
    )
    assert after_publication["members"] == []


def test_snapshot_and_ticker_resolution_reject_post_decision_knowledge(tmp_path):
    book = ledger(tmp_path)
    record(book)
    add_to_sp500(book)
    with pytest.raises(ValueError, match="knowledge cutoff cannot follow"):
        book.snapshot(
            universe="SP500",
            effective_as_of="2020-02-01T00:00:00+00:00",
            known_as_of="2020-02-02T00:00:00+00:00",
        )
    with pytest.raises(ValueError, match="knowledge cutoff cannot follow"):
        book.resolve_ticker(
            ticker="AAA",
            effective_as_of="2020-02-01T00:00:00+00:00",
            known_as_of="2020-02-02T00:00:00+00:00",
        )


def test_knowledge_cutoff_fails_when_ticker_reuse_would_be_ambiguous(tmp_path):
    book = ledger(tmp_path)
    record(book)
    add_to_sp500(book)
    delist_old(
        book,
        available_at="2021-06-03T11:00:00+00:00",
        retrieved_at="2021-06-03T12:00:00+00:00",
        recorded_at="2021-06-03T13:00:00+00:00",
    )
    record(
        book,
        security_id="SEC-NEW-002",
        ticker="AAA",
        effective_at="2021-06-01T20:00:00+00:00",
        marker="d",
        reported_at="2021-05-29T10:00:00+00:00",
        available_at="2021-05-29T11:00:00+00:00",
        retrieved_at="2021-05-29T12:00:00+00:00",
        recorded_at="2021-05-29T13:00:00+00:00",
    )
    with pytest.raises(LedgerIntegrityError, match="ambiguous"):
        book.snapshot(
            universe="SP500",
            effective_as_of="2021-06-02T00:00:00+00:00",
            known_as_of="2021-06-02T00:00:00+00:00",
        )


def test_membership_event_ticker_must_match_active_permanent_identity(tmp_path):
    book = ledger(tmp_path)
    record(book)
    with pytest.raises(LedgerIntegrityError, match="active identity"):
        add_to_sp500(book, ticker="ZZZ")


def test_lifecycle_event_cannot_silently_change_issuer_or_exchange(tmp_path):
    book = ledger(tmp_path)
    record(book)
    with pytest.raises(LedgerIntegrityError, match="contradicts active identity"):
        add_to_sp500(book, issuer_name="Different issuer")


def test_event_append_is_idempotent_but_conflicting_material_is_rejected(tmp_path):
    book = ledger(tmp_path)
    first = record(book)
    assert record(book) == first
    with pytest.raises(LedgerIntegrityError, match="already exists"):
        record(book, issuer_name="Conflicting issuer")


def test_five_source_timestamps_are_enforced(tmp_path):
    book = ledger(tmp_path)
    with pytest.raises(ValueError, match="must be chronological"):
        record(
            book,
            reported_at="2020-01-01T12:00:00+00:00",
            available_at="2020-01-01T11:00:00+00:00",
        )


def test_index_transition_and_delisting_boundaries_fail_closed(tmp_path):
    book = ledger(tmp_path)
    record(book)
    with pytest.raises(LedgerIntegrityError, match="lacks active membership"):
        record(
            book,
            event_type="INDEX_REMOVED",
            universe="SP500",
            effective_at="2021-01-01T00:00:00+00:00",
            marker="d",
            reported_at="2020-12-01T10:00:00+00:00",
            available_at="2020-12-01T11:00:00+00:00",
            retrieved_at="2020-12-01T12:00:00+00:00",
            recorded_at="2020-12-01T13:00:00+00:00",
        )
    with pytest.raises(ValueError, match="explicit terminal"):
        record(
            book,
            event_type="DELISTED",
            effective_at="2021-01-01T00:00:00+00:00",
            marker="e",
            reported_at="2020-12-01T10:00:00+00:00",
            available_at="2020-12-01T11:00:00+00:00",
            retrieved_at="2020-12-01T12:00:00+00:00",
            recorded_at="2020-12-01T13:00:00+00:00",
        )


@pytest.mark.parametrize(
    "change",
    [
        {"current_membership_used": True},
        {"partition_admission_authorized": True},
        {"performance_claim_allowed": True},
        {"broker_submission_enabled": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_tampering_cannot_create_authority(tmp_path, change):
    book = ledger(tmp_path)
    record(book)
    from core.portfolio import pit_security_master as module

    stored = json.loads(book.path.read_text())
    stored.update(change)
    material = {key: value for key, value in stored.items() if key != "record_hash"}
    stored["record_hash"] = module._record_hash(material)
    book.path.write_text(json.dumps(stored) + "\n")
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        book.verify()


def test_delisted_fixture_is_byte_reproducible(tmp_path):
    first = PointInTimeSecurityMasterLedger(tmp_path / "one.jsonl")
    second = PointInTimeSecurityMasterLedger(tmp_path / "two.jsonl")
    for book in (first, second):
        listed = record(book)
        assert listed["previous_hash"] == GENESIS_HASH
        add_to_sp500(book)
        delist_old(book)
    assert first.path.read_bytes() == second.path.read_bytes()
    first_snapshot = first.snapshot(
        universe="SP500",
        effective_as_of="2021-06-02T00:00:00+00:00",
        known_as_of="2021-06-02T00:00:00+00:00",
    )
    second_snapshot = second.snapshot(
        universe="SP500",
        effective_as_of="2021-06-02T00:00:00+00:00",
        known_as_of="2021-06-02T00:00:00+00:00",
    )
    assert first_snapshot == second_snapshot
