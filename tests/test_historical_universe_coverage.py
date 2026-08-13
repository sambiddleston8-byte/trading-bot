import json

import pytest

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError
from core.portfolio.delisting_outcome import DelistingOutcomeLedger
from core.portfolio.historical_universe import HistoricalUniverseEventLedger
from core.portfolio.historical_universe_coverage import HistoricalUniverseCoverageLedger


def setup_ledgers(tmp_path):
    events = HistoricalUniverseEventLedger(tmp_path / "events.jsonl")
    outcomes = DelistingOutcomeLedger(tmp_path / "outcomes.jsonl", events)
    coverage = HistoricalUniverseCoverageLedger(tmp_path / "coverage.jsonl", events, outcomes)
    return events, outcomes, coverage


def record_event(events, ticker, event_type, effective, digest, treatment="NOT_APPLICABLE"):
    day = effective[:10]
    return events.record_event(
        universe="SP500", ticker=ticker, issuer_name=f"{ticker} Inc", event_type=event_type,
        effective_date=day, publicly_available_at=f"{day}T01:00:00+00:00",
        retrieved_at=f"{day}T02:00:00+00:00", recorded_at=f"{day}T03:00:00+00:00",
        source_uri=f"https://index.example/{ticker}/{event_type}",
        source_input_sha256=digest * 64, source_locator=f"$.{ticker}.{event_type}",
        terminal_outcome_treatment=treatment,
    )


def populated(tmp_path):
    events, outcomes, coverage = setup_ledgers(tmp_path)
    record_event(events, "AAA", "ADDED", "2019-01-01", "a")
    delisted = record_event(
        events, "AAA", "DELISTED", "2020-06-01", "b",
        "BANKRUPTCY_OR_LIQUIDATION_OUTCOME_REQUIRED",
    )
    added = record_event(events, "BBB", "ADDED", "2020-07-01", "c")
    terminal = outcomes.record_outcome(
        delisting_event_id=delisted["event_id"],
        delisting_event_record_hash=delisted["record_hash"],
        outcome_type="BANKRUPTCY_OR_LIQUIDATION", terminal_value_per_share="0",
        currency="USD", valuation_method="ZERO_RECOVERY_FINAL_OUTCOME",
        outcome_effective_at="2020-08-01T00:00:00+00:00",
        publicly_available_at="2020-08-02T00:00:00+00:00",
        retrieved_at="2020-08-03T00:00:00+00:00",
        recorded_at="2020-08-04T00:00:00+00:00",
        source_uri="https://court.example/aaa/final", source_input_sha256="d" * 64,
        source_locator="page:12",
    )
    return events, outcomes, coverage, delisted, added, terminal


def certificate(coverage, delisted, added, terminal, **overrides):
    values = {
        "universe": "SP500",
        "covers_from_at": "2020-01-01T00:00:00+00:00",
        "through_at": "2020-12-31T23:59:59+00:00",
        "starting_members": [{"ticker": "AAA", "issuer_name": "AAA Inc"}],
        "event_references": [
            {"event_id": item["event_id"], "event_record_hash": item["record_hash"]}
            for item in (delisted, added)
        ],
        "delisting_outcome_references": [{
            "outcome_id": terminal["outcome_id"],
            "outcome_record_hash": terminal["record_hash"],
        }],
        "source_declares_complete_membership_and_changes": True,
        "source_methodology": "Official historical constituent file and change log",
        "source_version": "2020-final",
        "publicly_available_at": "2021-01-01T00:00:00+00:00",
        "retrieved_at": "2021-01-02T00:00:00+00:00",
        "recorded_at": "2021-01-03T00:00:00+00:00",
        "source_uri": "https://index.example/sp500/history-2020",
        "source_input_sha256": "e" * 64,
        "source_locator": "members-and-changes",
    }
    values.update(overrides)
    return coverage.certify(**values)


def test_attests_and_reconciles_exact_bounded_membership_and_outcomes(tmp_path):
    _, _, coverage, delisted, added, terminal = populated(tmp_path)
    record = certificate(coverage, delisted, added, terminal)
    assert record["previous_hash"] == GENESIS_HASH
    assert record["source_coverage_attested"] is True
    assert record["ledger_reconciliation_complete"] is True
    assert record["coverage_completeness_proven"] is False
    assert record["terminal_outcome_evidence_complete"] is True
    assert record["survivorship_safe_replay_inputs_ready_for_bounded_interval"] is False
    assert record["ending_members"] == [{"ticker": "BBB", "issuer_name": "BBB Inc"}]
    assert record["global_historical_coverage_claimed"] is False
    assert record["performance_calculated"] is False
    assert coverage.verify() == [record]


def test_missing_event_or_delisting_outcome_fails_closed(tmp_path):
    _, _, coverage, delisted, added, terminal = populated(tmp_path)
    with pytest.raises(ValueError, match="exactly match"):
        certificate(coverage, delisted, added, terminal, event_references=[])
    with pytest.raises(ValueError, match="every bounded delisting"):
        certificate(coverage, delisted, added, terminal, delisting_outcome_references=[])


def test_source_must_attest_completeness_and_interval_cannot_reach_future_evidence(tmp_path):
    _, _, coverage, delisted, added, terminal = populated(tmp_path)
    with pytest.raises(ValueError, match="explicitly attest"):
        certificate(
            coverage, delisted, added, terminal,
            source_declares_complete_membership_and_changes=False,
        )
    with pytest.raises(ValueError, match="beyond evidence retrieval"):
        certificate(
            coverage, delisted, added, terminal,
            through_at="2022-01-01T00:00:00+00:00",
        )


def test_starting_population_must_make_event_transitions_valid(tmp_path):
    _, _, coverage, delisted, added, terminal = populated(tmp_path)
    with pytest.raises(ValueError, match="non-member"):
        certificate(
            coverage, delisted, added, terminal,
            starting_members=[{"ticker": "ZZZ", "issuer_name": "ZZZ Inc"}],
        )


def test_certificate_is_idempotent_but_conflicting_source_is_not(tmp_path):
    _, _, coverage, delisted, added, terminal = populated(tmp_path)
    first = certificate(coverage, delisted, added, terminal)
    assert certificate(coverage, delisted, added, terminal) == first
    with pytest.raises(LedgerIntegrityError, match="already exists"):
        certificate(
            coverage, delisted, added, terminal,
            source_methodology="Conflicting methodology",
        )


def test_successive_certificate_must_be_contiguous_and_carry_members_forward(tmp_path):
    _, _, coverage, delisted, added, terminal = populated(tmp_path)
    first = certificate(coverage, delisted, added, terminal)
    with pytest.raises(ValueError, match="exactly contiguous"):
        certificate(
            coverage, delisted, added, terminal,
            covers_from_at="2022-01-01T00:00:00+00:00",
            through_at="2022-12-31T23:59:59+00:00",
            event_references=[], delisting_outcome_references=[],
            starting_members=first["ending_members"], source_input_sha256="f" * 64,
            source_locator="2022-members", retrieved_at="2023-01-02T00:00:00+00:00",
            recorded_at="2023-01-03T00:00:00+00:00",
        )
    with pytest.raises(ValueError, match="starting members"):
        certificate(
            coverage, delisted, added, terminal,
            covers_from_at=first["through_at"],
            through_at="2021-12-31T23:59:59+00:00",
            event_references=[], delisting_outcome_references=[],
            starting_members=[{"ticker": "ZZZ", "issuer_name": "ZZZ Inc"}],
            source_input_sha256="f" * 64, source_locator="2021-members",
            retrieved_at="2022-01-02T00:00:00+00:00",
            recorded_at="2022-01-03T00:00:00+00:00",
        )


@pytest.mark.parametrize("change", [
    {"global_historical_coverage_claimed": True},
    {"performance_calculated": True},
    {"broker_submission_enabled": True},
    {"live_trading_enabled": True},
    {"coverage_completeness_proven": True},
    {"survivorship_safe_replay_inputs_ready_for_bounded_interval": True},
])
def test_rehashed_tampering_cannot_broaden_authority(tmp_path, change):
    _, _, coverage, delisted, added, terminal = populated(tmp_path)
    record = certificate(coverage, delisted, added, terminal)
    record.update(change)
    from core.portfolio import historical_universe_coverage as module
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    coverage.path.write_text(json.dumps(record) + "\n")
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        coverage.verify()
