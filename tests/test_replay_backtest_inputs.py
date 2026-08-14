from copy import deepcopy
import hashlib

import pytest

import core.orchestration.replay_backtest_inputs as module
from core.guardrailed_backtest import GuardrailedBacktestEngine
from core.orchestration.replay_backtest_inputs import load_authenticated_backtest_inputs


FROM = "2022-04-01T00:00:00.000000+00:00"
THROUGH = "2022-04-03T00:00:00.000000+00:00"
KNOW = "2022-04-04T00:00:00.000000+00:00"
ROLES = (
    "CORPORATE_ACTIONS", "DELISTING_OUTCOMES", "MARKET_CALENDARS_AND_HALTS",
    "RAW_DAILY_SESSION_BARS", "TOTAL_RETURN_PRICES", "UNIVERSE_MEMBERSHIP",
)


def artifact(role, rows):
    return {
        "schema_version": "1.2" if role != "TOTAL_RETURN_PRICES" else "1.1",
        "artifact_role": role,
        "source_input_sha256": hashlib.sha256(role.encode()).hexdigest(),
        "source_bytes_authenticated": True,
        "artifact_structure_validated": True,
        "rows": rows,
    }


def bundle():
    bars = [{
        "ticker": "AAA", "session_opens_at": "2022-04-01T09:30:00.000000+00:00",
        "session_closes_at": "2022-04-01T16:00:00.000000+00:00",
        "available_at": "2022-04-01T16:00:01.000000+00:00",
        "open": "100", "high": "105", "low": "99", "close": "103",
        "volume": "100000", "source_row_locator": "bar:1",
    }, {
        "ticker": "AAA", "session_opens_at": "2022-04-02T09:30:00.000000+00:00",
        "session_closes_at": "2022-04-02T16:00:00.000000+00:00",
        "available_at": "2022-04-02T16:00:01.000000+00:00",
        "open": "103", "high": "106", "low": "102", "close": "105",
        "volume": "110000", "source_row_locator": "bar:2",
    }]
    calendar = [{
        "ticker": "AAA", "starts_at": FROM,
        "ends_at": "2022-04-01T09:30:00.000000+00:00", "available_at": FROM,
        "status": "CLOSED", "source_row_locator": "cal:1",
    }, {
        "ticker": "AAA", "starts_at": "2022-04-01T09:30:00.000000+00:00",
        "ends_at": "2022-04-01T16:00:00.000000+00:00", "available_at": FROM,
        "status": "OPEN", "source_row_locator": "cal:2",
    }, {
        "ticker": "AAA", "starts_at": "2022-04-01T16:00:00.000000+00:00",
        "ends_at": "2022-04-02T09:30:00.000000+00:00", "available_at": FROM,
        "status": "CLOSED", "source_row_locator": "cal:3",
    }, {
        "ticker": "AAA", "starts_at": "2022-04-02T09:30:00.000000+00:00",
        "ends_at": "2022-04-02T16:00:00.000000+00:00", "available_at": FROM,
        "status": "OPEN", "source_row_locator": "cal:4",
    }, {
        "ticker": "AAA", "starts_at": "2022-04-02T16:00:00.000000+00:00",
        "ends_at": THROUGH, "available_at": FROM, "status": "CLOSED",
        "source_row_locator": "cal:5",
    }]
    initial_membership = {
        "ticker": "AAA", "state": "MEMBER", "as_of_at": FROM,
        "available_at": FROM, "source_row_locator": "membership:initial",
    }
    initial_instrument = {
        "ticker": "AAA", "state": "ACTIVE", "terminal_type": None,
        "as_of_at": FROM, "available_at": FROM, "source_row_locator": "terminal:initial",
    }
    artifacts = {
        "TOTAL_RETURN_PRICES": artifact("TOTAL_RETURN_PRICES", []),
        "MARKET_CALENDARS_AND_HALTS": artifact("MARKET_CALENDARS_AND_HALTS", calendar),
        "CORPORATE_ACTIONS": artifact("CORPORATE_ACTIONS", []),
        "DELISTING_OUTCOMES": artifact("DELISTING_OUTCOMES", []),
        "UNIVERSE_MEMBERSHIP": artifact("UNIVERSE_MEMBERSHIP", []),
        "RAW_DAILY_SESSION_BARS": artifact("RAW_DAILY_SESSION_BARS", bars),
    }
    artifacts["UNIVERSE_MEMBERSHIP"].update(
        initial_membership_states=[initial_membership],
        resolved_membership_states={"AAA": {
            "initial_state": initial_membership, "canonical_events": [],
            "resolved_state": "MEMBER", "membership_incoherent": False,
        }},
    )
    artifacts["DELISTING_OUTCOMES"].update(
        initial_instrument_states=[initial_instrument],
        resolved_ticker_states={"AAA": {
            "initial_state": initial_instrument, "canonical_terminal_outcome": None,
            "resolved_state": "ACTIVE", "resolved_terminal_type": None,
            "actual_state_ambiguous": False,
        }},
    )
    return {
        "schema_version": "1.2",
        "record_type": "AUTHENTICATED_BACKTEST_REPLAY_ARTIFACT_BUNDLE",
        "admission_id": "RDA-AUTHENTICATED",
        "admission_record_hash": "a" * 64,
        "replay_plan_id": "REPLAY-1", "replay_plan_record_hash": "b" * 64,
        "dataset_commitment_sha256": "c" * 64,
        "tickers": ["AAA"], "covers_from_at": FROM, "through_at": THROUGH,
        "shared_knowledge_as_of_at": KNOW, "quote_currency": "USD",
        "artifacts": artifacts,
    }


def load(monkeypatch, value=None):
    monkeypatch.setattr(
        module, "load_authenticated_backtest_artifact_bundle",
        lambda **kwargs: deepcopy(value or bundle()),
    )
    return load_authenticated_backtest_inputs(
        admission_ledger=object(), content_ledger=object(), admission_id="RDA-AUTHENTICATED"
    )


def test_derives_immutable_engine_inputs_and_receipt_from_authenticated_roles(monkeypatch):
    result = load(monkeypatch)
    assert len(result.bars) == 2
    assert result.bars[0].available_at > result.bars[0].close_at
    assert result.universe_events[0].action == "ADD"
    assert len(result.data_attestation.evidence_role_hashes) == 6
    assert result.validation_receipt_sha256 == result.data_attestation.validation_receipt_sha256
    assert result.replay_plan_id == "REPLAY-1"
    assert result.replay_plan_record_hash == "b" * 64
    assert result.dataset_commitment_sha256 == "c" * 64
    assert result.prices_are_unadjusted is True
    assert result.broker_connection_allowed is False
    assert result.orders_submitted is False
    assert result.live_trading_enabled is False
    assert set(result.engine_inputs()) == {
        "bars", "universe_events", "terminal_outcomes", "corporate_actions",
        "prices_are_unadjusted",
    }
    assert not hasattr(result, "strategy")
    assert not hasattr(result, "performance")
    assert GuardrailedBacktestEngine.__init__  # derived attestation is engine-compatible


def test_requires_exact_bar_for_every_open_session(monkeypatch):
    value = bundle(); value["artifacts"]["RAW_DAILY_SESSION_BARS"]["rows"].pop()
    with pytest.raises(ValueError, match="exactly cover"):
        load(monkeypatch, value)


def test_rejects_bar_not_available_before_next_session_open(monkeypatch):
    value = bundle()
    value["artifacts"]["RAW_DAILY_SESSION_BARS"]["rows"][0]["available_at"] = (
        "2022-04-02T09:30:00.000000+00:00"
    )
    with pytest.raises(ValueError, match="not available before"):
        load(monkeypatch, value)


def test_normalizes_authenticated_bar_order_and_rejects_duplicate_sessions(monkeypatch):
    value = bundle()
    value["artifacts"]["RAW_DAILY_SESSION_BARS"]["rows"].reverse()
    result = load(monkeypatch, value)
    assert result.bars[0].open_at < result.bars[1].open_at
    value = bundle()
    value["artifacts"]["RAW_DAILY_SESSION_BARS"]["rows"].append(
        deepcopy(value["artifacts"]["RAW_DAILY_SESSION_BARS"]["rows"][0])
    )
    with pytest.raises(ValueError, match="sessions must be unique"):
        load(monkeypatch, value)


def test_rejects_late_calendar_knowledge_and_halts(monkeypatch):
    value = bundle()
    value["artifacts"]["MARKET_CALENDARS_AND_HALTS"]["rows"][1]["available_at"] = (
        "2022-04-01T10:00:00.000000+00:00"
    )
    with pytest.raises(ValueError, match="known by the start"):
        load(monkeypatch, value)
    value = bundle()
    value["artifacts"]["MARKET_CALENDARS_AND_HALTS"]["rows"][1]["status"] = "HALTED"
    with pytest.raises(ValueError, match="dedicated intraday"):
        load(monkeypatch, value)


def test_rejects_ambiguous_terminal_or_unknown_recovery(monkeypatch):
    value = bundle()
    value["artifacts"]["DELISTING_OUTCOMES"]["resolved_ticker_states"]["AAA"][
        "actual_state_ambiguous"
    ] = True
    with pytest.raises(ValueError, match="ambiguous terminal"):
        load(monkeypatch, value)
    value = bundle()
    terminal = {
        "ticker": "AAA", "event_id": "terminal:1", "version": 1,
        "terminal_type": "DELISTED", "effective_at": "2022-04-02T18:00:00.000000+00:00",
        "available_at": "2022-04-02T17:00:00.000000+00:00", "record_status": "ACTIVE",
        "supersedes_version": None, "source_row_locator": "terminal:1",
        "settlement": {"recovery_per_share": "0", "currency": "USD",
            "cash_settled_at": "2022-04-02T18:00:00.000000+00:00",
            "recovery_basis": "UNKNOWN"},
        "actual_state_ambiguous": False, "ambiguity_reason": "NONE",
    }
    value["artifacts"]["DELISTING_OUTCOMES"]["rows"] = [terminal]
    with pytest.raises(ValueError, match="recovery basis"):
        load(monkeypatch, value)


def test_rejects_unsupported_corporate_economics_and_post_terminal_bars(monkeypatch):
    value = bundle()
    value["artifacts"]["CORPORATE_ACTIONS"]["rows"] = [{
        "ticker": "AAA", "event_id": "corp:1", "version": 1,
        "event_type": "SYMBOL_CHANGE", "effective_at": "2022-04-02T08:00:00.000000+00:00",
        "available_at": "2022-04-01T08:00:00.000000+00:00", "record_status": "ACTIVE",
        "supersedes_version": None, "source_row_locator": "corp:1",
        "economics": {"new_ticker": "BBB"},
    }]
    with pytest.raises(ValueError, match="explicit supported economic model"):
        load(monkeypatch, value)
    value = bundle()
    terminal = {
        "ticker": "AAA", "event_id": "terminal:1", "version": 1,
        "terminal_type": "BANKRUPT", "effective_at": "2022-04-02T08:00:00.000000+00:00",
        "available_at": "2022-04-01T17:00:00.000000+00:00", "record_status": "ACTIVE",
        "supersedes_version": None, "source_row_locator": "terminal:1",
        "settlement": {"recovery_per_share": "0", "currency": "USD",
            "cash_settled_at": "2022-04-02T08:00:00.000000+00:00",
            "recovery_basis": "ZERO_RECOVERY_CONFIRMED"},
        "actual_state_ambiguous": False, "ambiguity_reason": "NONE",
    }
    value["artifacts"]["DELISTING_OUTCOMES"]["rows"] = [terminal]
    with pytest.raises(ValueError, match="cannot continue"):
        load(monkeypatch, value)


def test_rejects_terminal_or_corporate_events_inside_aggregate_bar(monkeypatch):
    value = bundle()
    terminal = {
        "ticker": "AAA", "event_id": "terminal:inside", "version": 1,
        "terminal_type": "BANKRUPT", "effective_at": "2022-04-02T12:00:00.000000+00:00",
        "available_at": "2022-04-02T11:00:00.000000+00:00", "record_status": "ACTIVE",
        "supersedes_version": None, "source_row_locator": "terminal:inside",
        "settlement": {"recovery_per_share": "0", "currency": "USD",
            "cash_settled_at": "2022-04-02T12:00:00.000000+00:00",
            "recovery_basis": "ZERO_RECOVERY_CONFIRMED"},
        "actual_state_ambiguous": False, "ambiguity_reason": "NONE",
    }
    value["artifacts"]["DELISTING_OUTCOMES"]["rows"] = [terminal]
    with pytest.raises(ValueError, match="inside an aggregate daily bar"):
        load(monkeypatch, value)
    value = bundle()
    value["artifacts"]["CORPORATE_ACTIONS"]["rows"] = [{
        "ticker": "AAA", "event_id": "corp:inside", "version": 1,
        "event_type": "SPLIT", "effective_at": "2022-04-02T12:00:00.000000+00:00",
        "available_at": "2022-04-02T08:00:00.000000+00:00", "record_status": "ACTIVE",
        "supersedes_version": None, "source_row_locator": "corp:inside",
        "economics": {"split_ratio": "2"},
    }]
    with pytest.raises(ValueError, match="inside an aggregate daily bar"):
        load(monkeypatch, value)


@pytest.mark.parametrize(
    "role,label,row",
    [
        ("UNIVERSE_MEMBERSHIP", "universe membership", {
            "ticker": "AAA", "event_id": "member:1", "version": 1,
            "membership_action": "REMOVE", "effective_at": "2022-04-01T12:00:00.000000+00:00",
            "available_at": "2022-04-01T08:00:00.000000+00:00", "record_status": "ACTIVE",
            "supersedes_version": None, "source_row_locator": "member:1",
        }),
        ("CORPORATE_ACTIONS", "corporate action", {
            "ticker": "AAA", "event_id": "corp:1", "version": 1,
            "event_type": "SPLIT", "effective_at": "2022-04-01T12:00:00.000000+00:00",
            "available_at": "2022-04-01T08:00:00.000000+00:00", "record_status": "ACTIVE",
            "supersedes_version": None, "source_row_locator": "corp:1",
            "economics": {"split_ratio": "2"},
        }),
        ("DELISTING_OUTCOMES", "terminal outcome", {
            "ticker": "AAA", "event_id": "terminal:late", "version": 1,
            "terminal_type": "BANKRUPT",
            "effective_at": "2022-04-01T12:00:00.000000+00:00",
            "available_at": "2022-04-01T08:00:00.000000+00:00",
            "record_status": "ACTIVE", "supersedes_version": None,
            "source_row_locator": "terminal:late",
            "settlement": {"recovery_per_share": "0", "currency": "USD",
                "cash_settled_at": "2022-04-01T12:00:00.000000+00:00",
                "recovery_basis": "ZERO_RECOVERY_CONFIRMED"},
            "actual_state_ambiguous": True,
            "ambiguity_reason": "ACTIVE_AT_EFFECTIVE_THEN_REVISED",
        }),
    ],
)
def test_late_revisions_cannot_rewrite_actual_historical_state(monkeypatch, role, label, row):
    value = bundle()
    retraction = deepcopy(row)
    retraction.update(
        version=2, available_at="2022-04-01T13:00:00.000000+00:00",
        record_status="RETRACTED", supersedes_version=1,
        source_row_locator=row["source_row_locator"] + ":v2",
    )
    if role == "CORPORATE_ACTIONS":
        retraction["economics"] = None
    if role == "DELISTING_OUTCOMES":
        retraction["terminal_type"] = None
        retraction["settlement"] = None
    value["artifacts"][role]["rows"] = [row, retraction]
    if role == "UNIVERSE_MEMBERSHIP":
        value["artifacts"][role]["resolved_membership_states"]["AAA"][
            "canonical_events"
        ] = []
    elif role == "DELISTING_OUTCOMES":
        value["artifacts"][role]["resolved_ticker_states"]["AAA"][
            "actual_state_ambiguous"
        ] = True
    with pytest.raises(ValueError, match=f"{label} was revised after"):
        load(monkeypatch, value)
