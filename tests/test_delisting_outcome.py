import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.portfolio.delisting_outcome import DelistingOutcomeLedger
from core.portfolio.historical_universe import HistoricalUniverseEventLedger


def ledgers(tmp_path):
    universe = HistoricalUniverseEventLedger(tmp_path / "universe.jsonl")
    outcomes = DelistingOutcomeLedger(tmp_path / "outcomes.jsonl", universe)
    return universe, outcomes


def add_and_delist(universe, *, treatment="BANKRUPTCY_OR_LIQUIDATION_OUTCOME_REQUIRED"):
    universe.record_event(
        universe="SP500", ticker="AAA", issuer_name="AAA Inc", event_type="ADDED",
        effective_date="2019-01-02", publicly_available_at="2019-01-01T12:00:00+00:00",
        retrieved_at="2019-01-01T13:00:00+00:00",
        recorded_at="2019-01-01T14:00:00+00:00",
        source_uri="https://index.example/aaa/add", source_input_sha256="a" * 64,
        source_locator="$.aaa.add",
    )
    return universe.record_event(
        universe="SP500", ticker="AAA", issuer_name="AAA Inc", event_type="DELISTED",
        effective_date="2020-01-02", publicly_available_at="2020-01-01T12:00:00+00:00",
        retrieved_at="2020-01-01T13:00:00+00:00",
        recorded_at="2020-01-01T14:00:00+00:00",
        source_uri="https://index.example/aaa/delist", source_input_sha256="b" * 64,
        source_locator="$.aaa.delist", terminal_outcome_treatment=treatment,
    )


def outcome(outcomes, event, **overrides):
    values = {
        "delisting_event_id": event["event_id"],
        "delisting_event_record_hash": event["record_hash"],
        "outcome_type": "BANKRUPTCY_OR_LIQUIDATION",
        "terminal_value_per_share": "0",
        "currency": "USD",
        "valuation_method": "ZERO_RECOVERY_FINAL_OUTCOME",
        "outcome_effective_at": "2020-04-01T00:00:00+00:00",
        "publicly_available_at": "2020-04-02T00:00:00+00:00",
        "retrieved_at": "2020-04-03T00:00:00+00:00",
        "recorded_at": "2020-04-04T00:00:00+00:00",
        "source_uri": "https://court.example/aaa/final-outcome",
        "source_input_sha256": "c" * 64,
        "source_locator": "page:14",
    }
    values.update(overrides)
    return outcomes.record_outcome(**values)


def test_records_zero_recovery_and_links_exact_delisting(tmp_path):
    universe, outcomes = ledgers(tmp_path)
    event = add_and_delist(universe)
    record = outcome(outcomes, event)
    assert record["previous_hash"] == GENESIS_HASH
    assert record["delisting_event_record_hash"] == event["record_hash"]
    assert record["terminal_value_per_share"] == "0"
    assert record["actual_terminal_outcome_evidence"] is True
    assert record["survivorship_safe_replay_ready"] is False
    assert outcomes.verify() == [record]


def test_evidence_respects_point_in_time_availability(tmp_path):
    universe, outcomes = ledgers(tmp_path)
    event = add_and_delist(universe)
    record = outcome(outcomes, event)
    before = outcomes.evidence_for_event(
        event["event_id"], known_as_of="2020-04-01T12:00:00+00:00"
    )
    after = outcomes.evidence_for_event(
        event["event_id"], known_as_of="2020-04-03T00:00:00+00:00"
    )
    assert before["terminal_outcome_evidence_complete"] is False
    assert after["terminal_outcome_evidence_complete"] is True
    assert after["outcome"]["outcome_id"] == record["outcome_id"]
    assert after["coverage_completeness_proven"] is False


def test_acquisition_treatment_requires_matching_method(tmp_path):
    universe, outcomes = ledgers(tmp_path)
    event = add_and_delist(
        universe, treatment="ACQUISITION_CASH_OR_STOCK_CONSIDERATION_REQUIRED"
    )
    with pytest.raises(ValueError, match="outcome_type"):
        outcome(outcomes, event)
    record = outcome(
        outcomes, event, outcome_type="ACQUISITION_CONSIDERATION",
        terminal_value_per_share="12.50", valuation_method="CASH_PROCEEDS_PER_SHARE",
    )
    assert record["terminal_value_per_share"] == "12.5"


def test_acquisition_terms_may_be_public_before_completion(tmp_path):
    universe, outcomes = ledgers(tmp_path)
    event = add_and_delist(
        universe, treatment="ACQUISITION_CASH_OR_STOCK_CONSIDERATION_REQUIRED"
    )
    record = outcome(
        outcomes, event, outcome_type="ACQUISITION_CONSIDERATION",
        terminal_value_per_share="12.50", valuation_method="CASH_PROCEEDS_PER_SHARE",
        publicly_available_at="2020-03-01T00:00:00+00:00",
    )
    assert record["publicly_available_at"] < record["outcome_effective_at"]


def test_future_terminal_outcome_cannot_be_recorded_as_completed_evidence(tmp_path):
    universe, outcomes = ledgers(tmp_path)
    event = add_and_delist(
        universe, treatment="ACQUISITION_CASH_OR_STOCK_CONSIDERATION_REQUIRED"
    )
    with pytest.raises(ValueError, match="before its evidence was retrieved"):
        outcome(
            outcomes, event, outcome_type="ACQUISITION_CONSIDERATION",
            terminal_value_per_share="12.50", valuation_method="CASH_PROCEEDS_PER_SHARE",
            outcome_effective_at="2099-04-01T00:00:00+00:00",
        )


def test_cannot_attach_outcome_to_removal_or_wrong_hash(tmp_path):
    universe, outcomes = ledgers(tmp_path)
    universe.record_event(
        universe="SP500", ticker="AAA", issuer_name="AAA Inc", event_type="ADDED",
        effective_date="2019-01-02", publicly_available_at="2019-01-01T12:00:00+00:00",
        retrieved_at="2019-01-01T13:00:00+00:00", recorded_at="2019-01-01T14:00:00+00:00",
        source_uri="https://index.example/add", source_input_sha256="a" * 64,
        source_locator="$.add",
    )
    removed = universe.record_event(
        universe="SP500", ticker="AAA", issuer_name="AAA Inc", event_type="REMOVED",
        effective_date="2020-01-02", publicly_available_at="2020-01-01T12:00:00+00:00",
        retrieved_at="2020-01-01T13:00:00+00:00", recorded_at="2020-01-01T14:00:00+00:00",
        source_uri="https://index.example/remove", source_input_sha256="b" * 64,
        source_locator="$.remove",
    )
    with pytest.raises(ValueError, match="not a delisting"):
        outcome(outcomes, removed)
    with pytest.raises(ValueError, match="missing or has changed"):
        outcome(outcomes, {**removed, "event_id": "UEVT-" + "F" * 32})


def test_rejects_future_information_and_duplicate_economic_outcome(tmp_path):
    universe, outcomes = ledgers(tmp_path)
    event = add_and_delist(universe)
    with pytest.raises(ValueError, match="chronological"):
        outcome(
            outcomes, event, publicly_available_at="2020-04-04T00:00:00+00:00",
            retrieved_at="2020-04-03T00:00:00+00:00",
        )
    first = outcome(outcomes, event)
    assert outcome(outcomes, event) == first
    with pytest.raises(LedgerIntegrityError, match="already has"):
        outcome(
            outcomes, event, source_input_sha256="d" * 64,
            source_uri="https://court.example/aaa/other", source_locator="page:15",
        )


@pytest.mark.parametrize("change", [
    {"survivorship_safe_replay_ready": True},
    {"coverage_completeness_proven": True},
    {"performance_calculated": True},
    {"broker_submission_enabled": True},
    {"live_trading_enabled": True},
])
def test_rehashed_tampering_cannot_grant_authority(tmp_path, change):
    universe, outcomes = ledgers(tmp_path)
    record = outcome(outcomes, add_and_delist(universe))
    record.update(change)
    from core.portfolio import delisting_outcome as module
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    outcomes.path.write_text(json.dumps(record) + "\n")
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        outcomes.verify()
