from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from core.decision_ledger import InvestmentDecisionLedger, LedgerIntegrityError
from core.guardrailed_backtest import (
    ACTION_ENTER_LONG,
    ACTION_EXIT_LONG,
    ACTION_HOLD,
    BacktestConfig,
    ExchangeFeeSchedule,
    ExchangeFeeTier,
    GuardrailedBacktestEngine,
    MarketBar,
    ReplayDataAttestation,
    UniverseEvent,
)
from core.orchestration import recorded_decision_signal as module
from core.orchestration.recorded_decision_signal import (
    RecordedDecisionSignalLedger,
    RecordedDecisionSignalStrategy,
)


UTC = timezone.utc
START = datetime(2025, 1, 1, 9, 30, tzinfo=UTC)


def decision(
    ledger,
    *,
    decision_id="DEC-1",
    value="BUY",
    ticker="AAA",
    decided_at="2025-01-03T15:55:00+00:00",
    data_as_of="2025-01-03T15:50:00+00:00",
):
    return ledger.append(
        ticker=ticker,
        decision=value,
        decision_payload={"expected_return": "0.1"},
        model_versions=[{"component": "research", "version": "1"}],
        data_as_of=data_as_of,
        portfolio_version="PORT-1",
        git_revision="1" * 40,
        decided_at=decided_at,
        decision_id=decision_id,
    )


def ledgers(tmp_path, **changes):
    decisions = InvestmentDecisionLedger(tmp_path / "decisions.jsonl")
    source = decision(decisions, **changes)
    signals = RecordedDecisionSignalLedger(tmp_path / "signals.jsonl", decisions)
    return decisions, signals, source


def record(signals, **changes):
    values = {
        "decision_id": "DEC-1",
        "market_bars": [bar(index) for index in range(8)],
        "data_attestation": attestation(),
        "recorded_by": "REPLAY_PREPARER",
        "recorded_at": "2025-01-04T00:00:00+00:00",
    }
    values.update(changes)
    return signals.record(**values)


@pytest.mark.parametrize(
    "source_decision,action",
    [
        ("STRONG_BUY", ACTION_ENTER_LONG),
        ("BUY", ACTION_ENTER_LONG),
        ("WATCHLIST", ACTION_HOLD),
        ("HOLD", ACTION_HOLD),
        ("AVOID", ACTION_EXIT_LONG),
    ],
)
def test_fixed_decision_mapping_records_no_execution_authority(
    tmp_path, source_decision, action
):
    _, signals, source = ledgers(tmp_path, value=source_decision)
    value = record(signals)

    assert value["source_decision"] == source_decision
    assert value["action"] == action
    assert value["decision_record_hash"] == source["record_hash"]
    assert value["available_at"] == source["decided_at"]
    assert value["mapping_is_fixed"] is True
    assert value["next_bar_execution_required"] is True
    assert value["simulation_only"] is True
    for field in module.FIXED_FALSE:
        assert value[field] is False
    assert signals.verify() == [value]


def test_unknown_decision_or_unsupported_recommendation_is_rejected(tmp_path):
    _, signals, _ = ledgers(tmp_path)
    with pytest.raises(ValueError, match="verified investment decision"):
        record(signals, decision_id="DEC-UNKNOWN")

    other_decisions = InvestmentDecisionLedger(tmp_path / "other-decisions.jsonl")
    decision(other_decisions, value="SELL")
    other_signals = RecordedDecisionSignalLedger(
        tmp_path / "other-signals.jsonl", other_decisions
    )
    with pytest.raises(ValueError, match="no approved replay-action mapping"):
        record(other_signals)


def test_decision_must_exist_and_be_available_by_bound_close(tmp_path):
    _, signals, _ = ledgers(tmp_path)
    with pytest.raises(ValueError, match="no eligible market close"):
        record(signals, market_bars=[bar(index) for index in range(2)])
    with pytest.raises(ValueError, match="cannot predate"):
        record(signals, recorded_at="2025-01-03T15:59:00+00:00")


def test_one_decision_and_one_ticker_close_slot_cannot_be_reused(tmp_path):
    decisions, signals, _ = ledgers(tmp_path)
    first = record(signals)
    assert record(signals) == first
    with pytest.raises(LedgerIntegrityError, match="one replay signal"):
        signals.record(
            decision_id="DEC-1",
            market_bars=[bar(index) for index in (0, 1, 3, 4, 5, 6, 7)],
            data_attestation=attestation(),
            recorded_by="REPLAY_PREPARER",
            recorded_at="2025-01-05T00:00:00+00:00",
        )
    decision(
        decisions,
        decision_id="DEC-2",
        value="HOLD",
        decided_at="2025-01-03T15:56:00+00:00",
        data_as_of="2025-01-03T15:51:00+00:00",
    )
    with pytest.raises(LedgerIntegrityError, match="market close"):
        signals.record(
            decision_id="DEC-2",
            market_bars=[bar(index) for index in range(8)],
            data_attestation=attestation(),
            recorded_by="REPLAY_PREPARER",
            recorded_at="2025-01-04T00:00:00+00:00",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("action", ACTION_HOLD),
        ("source_decision", "AVOID"),
        ("available_at", "2025-01-05T00:00:00+00:00"),
        ("mapping_is_fixed", False),
        ("performance_claim_allowed", True),
        ("order_submitted", True),
        ("live_trading_enabled", True),
    ],
)
def test_rehashed_signal_tampering_cannot_change_mapping_or_authority(
    tmp_path, field, value
):
    _, signals, _ = ledgers(tmp_path)
    record(signals)
    stored = json.loads(signals.path.read_text())
    stored[field] = value
    material = {key: item for key, item in stored.items() if key != "record_hash"}
    stored["record_hash"] = module._record_hash(material)
    signals.path.write_text(module._canonical_json(stored) + "\n")
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        signals.verify()


def bar(index):
    opens = START + timedelta(days=index)
    open_price = Decimal(100 + index)
    return MarketBar(
        symbol="AAA",
        open_at=opens,
        close_at=opens + timedelta(hours=6, minutes=30),
        available_at=opens + timedelta(hours=6, minutes=30, seconds=1),
        open=open_price,
        high=open_price + 2,
        low=open_price - 1,
        close=open_price + 1,
        volume=Decimal("100000"),
    )


def test_strategy_emits_only_at_exact_bound_close_and_only_once(tmp_path):
    _, signals, _ = ledgers(tmp_path)
    value = record(signals)
    market = [bar(index) for index in range(5)]
    strategy = RecordedDecisionSignalStrategy.from_ledger(
        signals,
        market_bars=[bar(index) for index in range(8)],
        data_attestation=attestation(),
    )

    assert strategy.decide("AAA", market[:2], strategy.parameters()) == ACTION_HOLD
    assert market[2].close_at.isoformat() == value["bound_bar_close_at"]
    assert (
        strategy.decide("AAA", market[:3], strategy.parameters())
        == ACTION_ENTER_LONG
    )
    assert strategy.decide("AAA", market[:3], strategy.parameters()) == ACTION_HOLD
    with pytest.raises(ValueError, match="do not pin"):
        strategy.decide("AAA", market[:4], {})

    missed = RecordedDecisionSignalStrategy.from_ledger(
        signals,
        market_bars=[bar(index) for index in range(8)],
        data_attestation=attestation(),
    )
    with pytest.raises(ValueError, match="skipped.*bound market close"):
        missed.decide("AAA", market[:4], missed.parameters())


def test_strategy_cannot_bind_to_a_schedule_that_omits_an_earlier_close(tmp_path):
    _, signals, _ = ledgers(tmp_path)
    record(signals)
    hindsight_schedule = [bar(index) for index in (0, 1, 3, 4, 5, 6, 7)]

    with pytest.raises(ValueError, match="not a valid recorded-decision signal"):
        RecordedDecisionSignalStrategy.from_ledger(
            signals,
            market_bars=hindsight_schedule,
            data_attestation=attestation(),
        )


def test_strategy_constructor_rejects_handcrafted_signal_authority(tmp_path):
    _, signals, _ = ledgers(tmp_path)
    recorded = dict(record(signals))

    with pytest.raises(ValueError, match="built from a verified ledger"):
        RecordedDecisionSignalStrategy([recorded])


@pytest.mark.parametrize(
    "field,value",
    [
        ("action", ACTION_EXIT_LONG),
        ("source_decision", "AVOID"),
        ("signal_id", "RSIG-FAKE"),
        ("mapping_is_fixed", False),
        ("simulation_only", False),
        ("broker_connection_allowed", True),
        ("market_schedule_sha256", "f" * 64),
    ],
)
def test_strategy_verified_boundary_rejects_changed_signal_fields(
    tmp_path, field, value
):
    _, signals, _ = ledgers(tmp_path)
    recorded = dict(record(signals))
    recorded[field] = value

    class ClaimedVerifiedLedger:
        def verify(self):
            return [recorded]

    with pytest.raises(ValueError, match="not a valid recorded-decision signal"):
        RecordedDecisionSignalStrategy.from_ledger(
            ClaimedVerifiedLedger(),
            market_bars=[bar(index) for index in range(8)],
            data_attestation=attestation(),
        )


def attestation(source_content_sha256="a" * 64):
    roles = (
        "CORPORATE_ACTIONS",
        "DELISTING_OUTCOMES",
        "MARKET_CALENDARS_AND_HALTS",
        "RAW_DAILY_SESSION_BARS",
        "TOTAL_RETURN_PRICES",
        "UNIVERSE_MEMBERSHIP",
    )
    return ReplayDataAttestation._from_authenticated_artifacts(
        source_id="AUTHENTICATED-SYNTHETIC-TEST",
        source_content_sha256=source_content_sha256,
        validation_receipt_sha256="b" * 64,
        derivation_policy_version="test-authenticated-fixture-v1",
        evidence_role_hashes=tuple((role, "c" * 64) for role in roles),
    )


def test_verified_decisions_execute_only_on_following_bar_through_real_engine(tmp_path):
    market = [bar(index) for index in range(12)]
    decisions = InvestmentDecisionLedger(tmp_path / "decisions.jsonl")
    decision(
        decisions,
        decision_id="DEC-BUY",
        value="BUY",
        decided_at=(market[2].close_at - timedelta(minutes=1)).isoformat(),
        data_as_of=(market[2].close_at - timedelta(minutes=2)).isoformat(),
    )
    decision(
        decisions,
        decision_id="DEC-EXIT",
        value="AVOID",
        decided_at=(market[7].close_at - timedelta(minutes=1)).isoformat(),
        data_as_of=(market[7].close_at - timedelta(minutes=2)).isoformat(),
    )
    signals = RecordedDecisionSignalLedger(tmp_path / "signals.jsonl", decisions)
    signals.record(
        decision_id="DEC-EXIT",
        market_bars=market,
        data_attestation=attestation(),
        recorded_by="TEST",
        recorded_at="2025-02-01T00:00:00+00:00",
    )
    signals.record(
        decision_id="DEC-BUY",
        market_bars=market,
        data_attestation=attestation(),
        recorded_by="TEST",
        recorded_at="2025-02-01T00:00:00+00:00",
    )
    strategy = RecordedDecisionSignalStrategy.from_ledger(
        signals, market_bars=market, data_attestation=attestation()
    )
    engine = GuardrailedBacktestEngine(
        config=BacktestConfig(
            initial_cash=Decimal("100000"),
            atr_window=2,
            lagged_liquidity_lookback=2,
        ),
        fee_schedule=ExchangeFeeSchedule(
            "TEST-FEES-v1", (ExchangeFeeTier(None, Decimal("1")),)
        ),
        data_attestation=attestation(),
    )
    result = engine.run(
        bars=market,
        universe_events=[
            UniverseEvent(
                "AAA", "ADD", market[0].open_at - timedelta(days=1),
                market[0].open_at - timedelta(days=2), "member:1",
            )
        ],
        terminal_outcomes=[],
        corporate_actions=[],
        prices_are_unadjusted=True,
        strategy=strategy,
        parameters=strategy.parameters(),
        evaluation_start=market[0].close_at,
        evaluation_end=market[10].close_at,
    )
    repeated = engine.run(
        bars=market,
        universe_events=[
            UniverseEvent(
                "AAA", "ADD", market[0].open_at - timedelta(days=1),
                market[0].open_at - timedelta(days=2), "member:1",
            )
        ],
        terminal_outcomes=[],
        corporate_actions=[],
        prices_are_unadjusted=True,
        strategy=strategy,
        parameters=strategy.parameters(),
        evaluation_start=market[0].close_at,
        evaluation_end=market[10].close_at,
    )

    buy = next(item for item in result.executions if item.action == "BUY")
    sell = next(item for item in result.executions if item.action == "SELL")
    assert buy.signal_at == market[2].available_at
    assert buy.executed_at == market[3].open_at
    assert sell.signal_at == market[7].available_at
    assert sell.executed_at == market[8].open_at
    assert buy.total_adverse_execution_bps >= Decimal("15")
    assert sell.total_adverse_execution_bps >= Decimal("15")
    assert result.strategy_version == strategy.version
    assert result.parameter_hash
    assert result.performance_claim_allowed is False
    assert result.broker_connection_allowed is False
    assert result.orders_submitted is False
    assert result.live_trading_enabled is False
    assert repeated.executions == result.executions
    with pytest.raises(ValueError, match="do not match.*causal schedule"):
        engine.run(
            bars=market[:-1],
            universe_events=[
                UniverseEvent(
                    "AAA", "ADD", market[0].open_at - timedelta(days=1),
                    market[0].open_at - timedelta(days=2), "member:1",
                )
            ],
            terminal_outcomes=[],
            corporate_actions=[],
            prices_are_unadjusted=True,
            strategy=strategy,
            parameters=strategy.parameters(),
            evaluation_start=market[0].close_at,
            evaluation_end=market[10].close_at,
        )
    with pytest.raises(ValueError, match="did not consume every recorded"):
        engine.run(
            bars=market,
            universe_events=[
                UniverseEvent(
                    "AAA", "ADD", market[0].open_at - timedelta(days=1),
                    market[0].open_at - timedelta(days=2), "member:1",
                )
            ],
            terminal_outcomes=[],
            corporate_actions=[],
            prices_are_unadjusted=True,
            strategy=strategy,
            parameters=strategy.parameters(),
            evaluation_start=market[0].close_at,
            evaluation_end=market[6].close_at,
        )


def test_strategy_is_bound_to_the_authenticated_replay_source(tmp_path):
    _, signals, _ = ledgers(tmp_path)
    record(signals)

    with pytest.raises(ValueError, match="not a valid recorded-decision signal"):
        RecordedDecisionSignalStrategy.from_ledger(
            signals,
            market_bars=[bar(index) for index in range(8)],
            data_attestation=attestation("d" * 64),
        )


def test_lowercase_decision_ticker_is_canonically_replayable(tmp_path):
    decisions, signals, _ = ledgers(tmp_path, ticker="aaa")
    recorded = record(signals)

    assert decisions.verify()[0]["ticker"] == "AAA"
    assert recorded["ticker"] == "AAA"
    assert RecordedDecisionSignalStrategy.from_ledger(
        signals,
        market_bars=[bar(index) for index in range(8)],
        data_attestation=attestation(),
    ).signals[0].ticker == "AAA"


def test_default_record_time_remains_idempotent(tmp_path):
    _, signals, _ = ledgers(tmp_path)
    values = {
        "decision_id": "DEC-1",
        "market_bars": [bar(index) for index in range(8)],
        "data_attestation": attestation(),
        "recorded_by": "REPLAY_PREPARER",
    }

    first = signals.record(**values)
    second = signals.record(**values)

    assert second == first
