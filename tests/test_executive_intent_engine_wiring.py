from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, getcontext
import json
from types import SimpleNamespace

import pytest

from core.guardrailed_backtest import (
    BacktestConfig,
    ExchangeFeeSchedule,
    ExchangeFeeTier,
    GuardrailedBacktestEngine,
    MarketBar,
    ReplayDataAttestation,
    TerminalOutcome,
    UniverseEvent,
)
from core.research.executive_intent_signal_adapter import (
    ExecutiveIntentSignalAdapter,
    executive_intent_signal_parameters,
)
from core.research.specialist_signals import (
    ExecutiveAggregatorBot,
    ExecutivePortfolioIntent,
    RiskEnvelope,
    SpecialistSignal,
    StandingStopInstruction,
    SymbolIntent,
)
from core.research import stage3_executive_train_evaluation as train_evaluation
from core.orchestration.replay_run_audit import (
    _match_executions_and_sizing,
    _result_payload,
    _validate_sizing,
)


UTC = timezone.utc
START = datetime(2024, 10, 1, 13, 30, tzinfo=UTC)


def _bars(count=45, *, symbol="AAPL"):
    rows = []
    for index in range(count):
        opened = START + timedelta(days=index)
        price = Decimal("100") + Decimal(index) / Decimal("10")
        rows.append(
            MarketBar(
                symbol=symbol,
                open_at=opened,
                close_at=opened + timedelta(hours=6, minutes=30),
                available_at=opened + timedelta(hours=6, minutes=31),
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                volume=Decimal("100000"),
            )
        )
    return rows


class _Consumer:
    def consume_if_available(self, symbol, *, effective_at, decision_at):
        return SimpleNamespace(
            values={
                "sma_20": "110",
                "sma_50": "100",
                "momentum_20": "0.1",
                "atr_14": "2",
            },
            record_sha256=(symbol[0].lower() * 64),
            available_at=effective_at.isoformat(),
        )


class _Insider:
    def score_tick(self, symbol, *, decision_at):
        covered = symbol != "SPY"
        return SpecialistSignal(
            specialist_id="SEC_FORM4_INSIDER",
            specialist_version=ExecutiveAggregatorBot.SPECIALIST_VERSIONS[
                "SEC_FORM4_INSIDER"
            ],
            symbol=symbol,
            decision_at=decision_at.isoformat(),
            score=Decimal("1") if covered else Decimal("0"),
            evidence_count=1 if covered else 0,
            evidence_sha256=("f" if covered else "0") * 64,
            reason=(
                "TRAILING_60_DAY_P_S_CLUSTER_ROLE_INTENSITY"
                if covered
                else "NO_INSIDER_COVERAGE_FOR_SYMBOL"
            ),
        )


def test_executive_adapter_keeps_risk_separate_and_missing_coverage_abstains():
    history = _bars()
    adapter = ExecutiveIntentSignalAdapter(
        _Consumer(),
        insider_specialist=_Insider(),
        liquidation_signal_at=history[-1].close_at + timedelta(days=1),
    )
    intent = adapter.decide_portfolio(
        "AAPL",
        history,
        executive_intent_signal_parameters(),
        current_weight=Decimal("0"),
        eligible=True,
    )
    decision = intent.symbol_intents[0]
    assert decision.action == "ENTER_LONG"
    assert decision.target_weight == Decimal("0.1")
    assert decision.consensus == Decimal("1")
    assert decision.standing_stop is not None
    assert "RISK_REGIME" not in ExecutiveAggregatorBot.WEIGHTS

    spy_history = _bars(symbol="SPY")
    spy = adapter.decide_portfolio(
        "SPY",
        spy_history,
        executive_intent_signal_parameters(),
        current_weight=Decimal("0"),
        eligible=True,
    ).symbol_intents[0]
    assert spy.action == "CASH"
    assert "NO_QUORUM" in spy.reason_codes

    insufficient_history = adapter.decide_portfolio(
        "AAPL",
        history[:10],
        executive_intent_signal_parameters(),
        current_weight=Decimal("0.08"),
        eligible=True,
    ).symbol_intents[0]
    assert insufficient_history.action == "HOLD"
    assert insufficient_history.target_weight == Decimal("0.08")
    assert "RISK_STALE" in insufficient_history.reason_codes


def test_executive_adapter_emits_one_cross_symbol_portfolio_intent():
    histories = {
        symbol: tuple(_bars(symbol=symbol))
        for symbol in ("AAPL", "MSFT", "SPY")
    }
    adapter = ExecutiveIntentSignalAdapter(
        _Consumer(),
        insider_specialist=_Insider(),
        liquidation_signal_at=histories["SPY"][-1].close_at + timedelta(days=1),
    )
    intent = adapter.decide_portfolio_batch(
        histories,
        executive_intent_signal_parameters(),
        current_weights={symbol: Decimal("0") for symbol in histories},
        eligible_symbols=("SPY", "MSFT", "AAPL"),
    )
    assert [item.symbol for item in intent.symbol_intents] == [
        "AAPL", "MSFT", "SPY"
    ]
    assert {
        item.symbol: item.action for item in intent.symbol_intents
    } == {"AAPL": "ENTER_LONG", "MSFT": "ENTER_LONG", "SPY": "CASH"}
    assert sum(item.target_weight for item in intent.symbol_intents) <= (
        intent.gross_exposure_cap
    )
    assert len({item.current_weight for item in intent.symbol_intents}) == 1


def _attestation():
    roles = (
        "CORPORATE_ACTIONS",
        "DELISTING_OUTCOMES",
        "MARKET_CALENDARS_AND_HALTS",
        "RAW_DAILY_SESSION_BARS",
        "TOTAL_RETURN_PRICES",
        "UNIVERSE_MEMBERSHIP",
    )
    return ReplayDataAttestation._from_authenticated_artifacts(
        source_id="EXECUTIVE-ENGINE-TEST",
        source_content_sha256="a" * 64,
        validation_receipt_sha256="b" * 64,
        derivation_policy_version="executive-engine-test-v1",
        evidence_role_hashes=tuple(sorted((role, "c" * 64) for role in roles)),
    )


class _FixedExecutiveStrategy:
    version = "fixed-executive-intent-test-v1"

    def decide_portfolio(
        self,
        symbol,
        history,
        parameters,
        *,
        current_weight,
        eligible,
    ):
        current = history[-1]
        count = len(history)
        risk = RiskEnvelope(
            version="test-risk-v1",
            decision_at=current.available_at.isoformat(),
            status="VALID",
            regime="NORMAL",
            new_entries_allowed=True,
            forced_exit=False,
            gross_exposure_cap=Decimal("0.1"),
            symbol_exposure_cap=Decimal("0.1"),
            position_size_multiplier=Decimal("1"),
            maximum_input_available_at=current.available_at.isoformat(),
            evidence_sha256="d" * 64,
            reason_codes=("TEST_RISK",),
        )
        if count == parameters["enter_at"]:
            target = Decimal("0.08")
        elif count == parameters["exit_at"]:
            target = Decimal("0")
        else:
            target = Decimal(current_weight)
        if current_weight == 0 and target == 0:
            action = "CASH"
        elif current_weight > 0 and target == 0:
            action = "EXIT"
        elif target > current_weight:
            action = "ENTER_LONG"
        elif target < current_weight:
            action = "REDUCE"
        else:
            action = "HOLD"
        stop = None
        if action == "ENTER_LONG":
            stop = StandingStopInstruction(
                reference_price=current.close,
                trigger_rule=f"LAST_PRICE_LTE_{current.close - Decimal('10')}",
                order_type="STOP_MARKET",
                evidence_sha256="e" * 64,
            )
        symbol_intent = SymbolIntent(
            symbol=symbol,
            action=action,
            current_weight=Decimal(current_weight),
            target_weight=target,
            conviction=Decimal("1") if target > 0 else Decimal("0"),
            participation=Decimal("1"),
            consensus=Decimal("1") if target > 0 else Decimal("0"),
            disagreement=Decimal("0"),
            risk_multiplier=Decimal("1"),
            specialist_evidence_sha256=("f" * 64,),
            reason_codes=("FIXED_TEST_INTENT",),
            standing_stop=stop,
        )
        return ExecutivePortfolioIntent(
            version="test-executive-v1",
            decision_at=current.available_at.isoformat(),
            risk_envelope_sha256=risk.envelope_sha256,
            gross_exposure_cap=Decimal("0.1"),
            symbol_intents=(symbol_intent,),
            reason_codes=("TEST",),
        )


def _run_fixed_executive(*, exit_at: int):
    market = _bars(14)
    engine = GuardrailedBacktestEngine(
        config=BacktestConfig(
            initial_cash=Decimal("100000"),
            atr_window=3,
            lagged_liquidity_lookback=3,
        ),
        fee_schedule=ExchangeFeeSchedule(
            "EXECUTIVE-TEST-FEES",
            (ExchangeFeeTier(None, Decimal("1"), Decimal("0.01")),),
        ),
        data_attestation=_attestation(),
    )
    result = engine.run(
        bars=market,
        universe_events=(
            UniverseEvent(
                "AAPL", "ADD", market[0].open_at, market[0].open_at,
                "synthetic-membership",
            ),
        ),
        terminal_outcomes=(),
        corporate_actions=(),
        prices_are_unadjusted=True,
        strategy=_FixedExecutiveStrategy(),
        parameters={"enter_at": 4, "exit_at": exit_at},
        evaluation_start=market[0].close_at,
        evaluation_end=market[11].open_at,
    )
    return market, result


def test_engine_executes_and_traces_executive_target_weights():
    market, result = _run_fixed_executive(exit_at=8)
    buy = next(row for row in result.executions if row.action == "BUY")
    sell = next(row for row in result.executions if row.action == "SELL")
    assert buy.reason == "EXECUTIVE_TARGET"
    assert sell.reason == "EXECUTIVE_TARGET"
    assert buy.signal_at == market[3].available_at
    assert buy.executed_at == market[4].open_at
    assert buy.filled_quantity * buy.execution_price <= Decimal("8000")
    assert result.executive_intents
    assert [row.sequence for row in result.executive_intents] == list(
        range(1, len(result.executive_intents) + 1)
    )
    assert all(len(row.intent_sha256) == 64 for row in result.executive_intents)
    assert result.completed_trades[-1].exit_reason == "EXECUTIVE_TARGET"
    assert result.portfolio_states[-1].position_quantity == 0


def test_last_in_window_executive_exit_executes_before_terminal_fallback():
    market, result = _run_fixed_executive(exit_at=11)
    sell = next(row for row in result.executions if row.action == "SELL")
    assert sell.signal_at == market[10].available_at
    assert sell.executed_at == market[11].open_at
    assert sell.reason == "EXECUTIVE_TARGET"
    assert result.portfolio_states[-1].position_quantity == 0


def test_engine_forces_terminal_liquidation_when_executive_never_exits():
    market, result = _run_fixed_executive(exit_at=999)
    sell = next(row for row in result.executions if row.action == "SELL")
    assert sell.signal_at == market[11].open_at
    assert sell.executed_at == market[12].open_at
    assert sell.reason == "EVALUATION_END"
    assert result.portfolio_states[-1].position_quantity == 0


def test_executive_intent_hashes_ignore_ambient_decimal_precision():
    original_precision = getcontext().prec
    try:
        getcontext().prec = 12
        _, low_precision = _run_fixed_executive(exit_at=8)
        getcontext().prec = 50
        _, high_precision = _run_fixed_executive(exit_at=8)
    finally:
        getcontext().prec = original_precision
    assert [row.intent_sha256 for row in low_precision.executive_intents] == [
        row.intent_sha256 for row in high_precision.executive_intents
    ]
    assert [row.current_weight for row in low_precision.executive_intents] == [
        row.current_weight for row in high_precision.executive_intents
    ]


def test_standing_stop_trigger_is_machine_readable_and_below_reference():
    stop = StandingStopInstruction(
        reference_price=Decimal("100"),
        trigger_rule="LAST_PRICE_LTE_95.5",
        order_type="STOP_MARKET",
        evidence_sha256="a" * 64,
    )
    assert stop.trigger_price == Decimal("95.5")


def test_train_comparison_configures_only_sealed_train_paths(
    monkeypatch, tmp_path
):
    artifact_path = tmp_path / train_evaluation.FORM4_ARTIFACT
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("{}")
    monkeypatch.setattr(
        train_evaluation,
        "SECForm4InsiderSpecialistBot",
        lambda artifact, expected_sha256: SimpleNamespace(),
    )
    captured = {}

    def fake_evaluate(repository_root, **kwargs):
        captured.update(kwargs)
        return {"status": kwargs["status"]}

    monkeypatch.setattr(
        train_evaluation, "_evaluate_train_rolling", fake_evaluate
    )
    report = train_evaluation.evaluate_train_executive_intents(tmp_path)
    assert report["status"] == (
        "TRAIN_ONLY_EXECUTIVE_INTENT_ENGINE_COMPARISON_COMPLETE"
    )
    assert set(captured["policies"]) == {
        "LEGACY_RESEARCH_THREE_VOTE",
        "EXECUTIVE_INTENT_BRIDGE",
    }
    metadata = captured["report_metadata"]
    assert metadata["partition_role"] == "TRAIN"
    assert metadata["validation_data_read"] is False
    assert metadata["untouched_test_included"] is False
    assert metadata["promotion_allowed"] is False
    assert metadata["risk_counted_as_alpha_in_executive_path"] is False
    assert metadata["simultaneous_portfolio_batching_complete"] is False


def _portfolio_bars(count=14):
    rows = []
    for index in range(count):
        opened = START + timedelta(days=index)
        for symbol, offset in (("AAPL", Decimal("0")), ("MSFT", Decimal("10"))):
            price = Decimal("100") + offset + Decimal(index) / Decimal("10")
            rows.append(
                MarketBar(
                    symbol=symbol,
                    open_at=opened,
                    close_at=opened + timedelta(hours=6, minutes=30),
                    available_at=opened + timedelta(hours=6, minutes=31),
                    open=price,
                    high=price + Decimal("0.5"),
                    low=price - Decimal("0.5"),
                    close=price,
                    volume=Decimal("1000000"),
                )
            )
    return rows


class _FixedPortfolioStrategy:
    version = "fixed-executive-portfolio-test-v1"

    def decide_portfolio_batch(
        self,
        histories,
        parameters,
        *,
        current_weights,
        eligible_symbols,
    ):
        current = histories[sorted(histories)[0]][-1]
        count = len(histories[sorted(histories)[0]])
        risk = RiskEnvelope(
            version="test-portfolio-risk-v1",
            decision_at=current.available_at.isoformat(),
            status="VALID",
            regime="NORMAL",
            new_entries_allowed=True,
            forced_exit=False,
            gross_exposure_cap=Decimal("1"),
            symbol_exposure_cap=Decimal("0.5"),
            position_size_multiplier=Decimal("1"),
            maximum_input_available_at=current.available_at.isoformat(),
            evidence_sha256="9" * 64,
            reason_codes=("TEST_PORTFOLIO_RISK",),
        )
        intents = []
        for symbol in sorted(set(eligible_symbols) | {
            name for name, weight in current_weights.items() if weight > 0
        }):
            current_weight = Decimal(current_weights[symbol])
            target = (
                Decimal("0.5") if count == parameters["enter_at"]
                else (
                    Decimal("0")
                    if symbol == "AAPL" or symbol.endswith("AAPL-001")
                    else Decimal("0.5")
                ) if count == parameters.get("mixed_at")
                else Decimal("0") if count == parameters["exit_at"]
                else current_weight
            )
            if current_weight == 0 and target == 0:
                action = "CASH"
            elif current_weight > 0 and target == 0:
                action = "EXIT"
            elif target > current_weight:
                action = "ENTER_LONG"
            elif target < current_weight:
                action = "REDUCE"
            else:
                action = "HOLD"
            bar = histories[symbol][-1]
            stop = (
                StandingStopInstruction(
                    reference_price=bar.close,
                    trigger_rule=f"LAST_PRICE_LTE_{bar.close - Decimal('1')}",
                    order_type="STOP_MARKET",
                    evidence_sha256=("a" if symbol == "AAPL" else "b") * 64,
                )
                if action == "ENTER_LONG" else None
            )
            intents.append(
                SymbolIntent(
                    symbol=symbol,
                    action=action,
                    current_weight=current_weight,
                    target_weight=target,
                    conviction=Decimal("1") if target > 0 else Decimal("0"),
                    participation=Decimal("1"),
                    consensus=Decimal("1") if target > 0 else Decimal("0"),
                    disagreement=Decimal("0"),
                    risk_multiplier=Decimal("1"),
                    specialist_evidence_sha256=("f" * 64,),
                    reason_codes=("FIXED_PORTFOLIO_TEST",),
                    standing_stop=stop,
                )
            )
        return ExecutivePortfolioIntent(
            version="test-executive-portfolio-v1",
            decision_at=current.available_at.isoformat(),
            risk_envelope_sha256=risk.envelope_sha256,
            gross_exposure_cap=Decimal("1"),
            symbol_intents=tuple(intents),
            reason_codes=("TEST_PORTFOLIO",),
        )


def _run_fixed_portfolio(
    *,
    reverse=False,
    market=None,
    parameters=None,
    evaluation_end=None,
    initial_cash=Decimal("100000"),
    universe_events=None,
    terminal_outcomes=None,
):
    market = _portfolio_bars() if market is None else market
    parameters = (
        {"enter_at": 4, "exit_at": 8} if parameters is None else parameters
    )
    supplied = list(reversed(market)) if reverse else market
    engine = GuardrailedBacktestEngine(
        config=BacktestConfig(
            initial_cash=initial_cash,
            atr_window=3,
            lagged_liquidity_lookback=3,
            maximum_position_fraction=Decimal("0.5"),
        ),
        fee_schedule=ExchangeFeeSchedule(
            "EXECUTIVE-PORTFOLIO-TEST-FEES",
            (ExchangeFeeTier(None, Decimal("1"), Decimal("0.01")),),
        ),
        data_attestation=_attestation(),
    )
    result = engine.run(
        bars=supplied,
        universe_events=(
            tuple(universe_events)
            if universe_events is not None
            else tuple(
                UniverseEvent(
                    symbol, "ADD", market[0].open_at, market[0].open_at,
                    "synthetic-portfolio-membership",
                )
                for symbol in (("MSFT", "AAPL") if reverse else ("AAPL", "MSFT"))
            )
        ),
        terminal_outcomes=(
            tuple(terminal_outcomes) if terminal_outcomes is not None else ()
        ),
        corporate_actions=(),
        prices_are_unadjusted=True,
        strategy=_FixedPortfolioStrategy(),
        parameters=parameters,
        evaluation_start=market[0].close_at,
        evaluation_end=(market[-6].open_at if evaluation_end is None else evaluation_end),
    )
    return market, result


def test_portfolio_batch_reserves_shared_cash_and_traces_one_intent():
    _, result = _run_fixed_portfolio()
    buys = [row for row in result.executions if row.action == "BUY"]
    assert {row.symbol for row in buys} == {"AAPL", "MSFT"}
    assert len({row.executed_at for row in buys}) == 1
    assert result.cash_reservations
    first_batch = [
        row for row in result.cash_reservations if row.batch_sequence == 1
    ]
    assert {row.symbol for row in first_batch} == {"AAPL", "MSFT"}
    assert sum(row.reserved_cash for row in first_batch) <= Decimal("100000")
    assert all(row.reserved_cash >= row.consumed_cash for row in first_batch)
    assert all(row.released_cash >= 0 for row in first_batch)
    entry_traces = [
        row for row in result.executive_intents if row.action == "ENTER_LONG"
    ]
    assert len({row.intent_sha256 for row in entry_traces}) == 1
    assert all(state.settled_cash >= 0 for state in result.portfolio_states)
    assert {trade.symbol for trade in result.completed_trades} == {"AAPL", "MSFT"}


def test_trading_portfolio_result_is_accepted_by_replay_audit_validators():
    _, result = _run_fixed_portfolio()
    assert all(
        type(trace.open_risk_before) is Decimal
        for trace in result.sizing_decisions
    )
    payload = _result_payload(result)
    engine = json.loads(result.engine_config_canonical_json)
    for trace in payload["sizing_decisions"]:
        _validate_sizing(trace, engine)
    _match_executions_and_sizing(
        payload["executions"], payload["sizing_decisions"], engine
    )


def test_portfolio_batch_is_invariant_to_input_symbol_order():
    _, first = _run_fixed_portfolio()
    _, second = _run_fixed_portfolio(reverse=True)
    assert first.executions == second.executions
    assert first.executive_intents == second.executive_intents
    assert first.cash_reservations == second.cash_reservations
    assert first.ending_equity == second.ending_equity


def test_portfolio_batch_never_opens_new_risk_at_evaluation_end():
    market = _portfolio_bars(16)
    evaluation_end = START + timedelta(days=11)
    _, result = _run_fixed_portfolio(
        market=market,
        parameters={"enter_at": 4, "exit_at": 999, "mixed_at": 11},
        evaluation_end=evaluation_end,
    )
    assert any(
        row.action == "SELL"
        and row.symbol == "AAPL"
        and row.executed_at == evaluation_end
        and row.reason == "EXECUTIVE_PORTFOLIO_TARGET"
        for row in result.executions
    )
    assert not any(
        row.action == "BUY" and row.executed_at >= evaluation_end
        for row in result.executions
    )
    assert any(
        row.action == "SELL"
        and row.symbol == "MSFT"
        and row.reason == "EVALUATION_END"
        and row.executed_at > evaluation_end
        for row in result.executions
    )


def test_partial_portfolio_stop_remains_mandatory_after_price_recovers():
    market = []
    for row in _portfolio_bars(22):
        session = (row.open_at - START).days
        changed = replace(
            row,
            volume=(Decimal("2500") if session >= 5 else row.volume),
        )
        if row.symbol == "AAPL" and session == 8:
            changed = replace(changed, low=Decimal("90"))
        if row.symbol == "AAPL" and session == 9:
            changed = replace(
                changed,
                open=Decimal("110"), high=Decimal("111"),
                low=Decimal("109"), close=Decimal("110"),
            )
        market.append(changed)
    evaluation_end = START + timedelta(days=15)
    _, result = _run_fixed_portfolio(
        market=market,
        parameters={"enter_at": 4, "exit_at": 999},
        evaluation_end=evaluation_end,
        initial_cash=Decimal("20000"),
    )
    stops = [
        row for row in result.executions
        if row.symbol == "AAPL" and row.reason == "HARD_ATR_STOP"
    ]
    assert len(stops) >= 2
    assert stops[0].status == "PARTIALLY_FILLED"
    assert stops[1].signal_at == stops[0].signal_at
    assert stops[1].executed_at == START + timedelta(days=9)
    assert stops[1].reference_price == Decimal("110")


def test_portfolio_batch_rejects_cross_symbol_clock_misalignment():
    market = _portfolio_bars()
    broken = [
        row if not (row.symbol == "MSFT" and row == market[1])
        else MarketBar(
            symbol=row.symbol,
            open_at=row.open_at + timedelta(minutes=1),
            close_at=row.close_at + timedelta(minutes=1),
            available_at=row.available_at + timedelta(minutes=1),
            open=row.open, high=row.high, low=row.low, close=row.close,
            volume=row.volume,
        )
        for row in market
    ]
    engine = GuardrailedBacktestEngine(
        config=BacktestConfig(
            initial_cash=Decimal("100000"), atr_window=3,
            lagged_liquidity_lookback=3,
        ),
        fee_schedule=ExchangeFeeSchedule(
            "EXECUTIVE-PORTFOLIO-TEST-FEES",
            (ExchangeFeeTier(None, Decimal("1"), Decimal("0.01")),),
        ),
        data_attestation=_attestation(),
    )
    with pytest.raises(ValueError, match="cross-symbol synchronized"):
        engine.run(
            bars=broken,
            universe_events=tuple(
                UniverseEvent(
                    symbol, "ADD", market[0].open_at, market[0].open_at,
                    "synthetic-portfolio-membership",
                )
                for symbol in ("AAPL", "MSFT")
            ),
            terminal_outcomes=(), corporate_actions=(),
            prices_are_unadjusted=True,
            strategy=_FixedPortfolioStrategy(),
            parameters={"enter_at": 4, "exit_at": 8},
            evaluation_start=market[0].close_at,
            evaluation_end=market[-6].open_at,
        )


def _dynamic_portfolio_fixture():
    market = [
        replace(row, symbol=f"SEC-{row.symbol}-001")
        for row in _portfolio_bars(14)
        if row.symbol == "AAPL"
        or 2 <= (row.open_at - START).days <= 10
    ]
    events = (
        UniverseEvent(
            "SEC-AAPL-001", "ADD", START, START,
            "synthetic-dynamic-membership:AAPL:add",
        ),
        UniverseEvent(
            "SEC-MSFT-001", "ADD", START + timedelta(days=2),
            START + timedelta(days=2),
            "synthetic-dynamic-membership:MSFT:add",
        ),
        UniverseEvent(
            "SEC-MSFT-001", "REMOVE",
            START + timedelta(days=10, hours=6, minutes=30),
            START + timedelta(days=10, hours=6, minutes=30),
            "synthetic-dynamic-membership:MSFT:remove",
        ),
    )
    return market, events


def test_portfolio_batch_supports_dynamic_permanent_id_session_sets():
    market, events = _dynamic_portfolio_fixture()
    _, first = _run_fixed_portfolio(
        market=market,
        universe_events=events,
        parameters={"enter_at": 999, "exit_at": 999},
        evaluation_end=START + timedelta(days=13),
    )
    _, second = _run_fixed_portfolio(
        reverse=True,
        market=market,
        universe_events=tuple(reversed(events)),
        parameters={"enter_at": 999, "exit_at": 999},
        evaluation_end=START + timedelta(days=13),
    )
    counts = {
        symbol: sum(row.symbol == symbol for row in first.executive_intents)
        for symbol in ("SEC-AAPL-001", "SEC-MSFT-001")
    }
    assert counts == {"SEC-AAPL-001": 13, "SEC-MSFT-001": 8}
    assert first.executive_intents == second.executive_intents
    assert first.cash_reservations == second.cash_reservations == ()
    assert first.executions == second.executions == ()


def test_portfolio_batch_rejects_missing_bar_for_active_dynamic_symbol():
    market, events = _dynamic_portfolio_fixture()
    broken = [
        row
        for row in market
        if not (
            row.symbol == "SEC-MSFT-001"
            and (row.open_at - START).days == 2
        )
    ]
    with pytest.raises(ValueError, match="active, held or pending-intent symbol"):
        _run_fixed_portfolio(
            market=broken,
            universe_events=events,
            parameters={"enter_at": 999, "exit_at": 999},
            evaluation_end=START + timedelta(days=13),
        )


def test_portfolio_batch_rejects_inactive_gap_that_would_splice_reentry_history():
    market = [
        replace(row, symbol=f"SEC-{row.symbol}-001")
        for row in _portfolio_bars(10)
        if row.symbol == "AAPL"
        or (row.open_at - START).days not in {3, 4}
    ]
    events = (
        UniverseEvent("SEC-AAPL-001", "ADD", START, START, "gap:AAPL"),
        UniverseEvent("SEC-MSFT-001", "ADD", START, START, "gap:MSFT:add-1"),
        UniverseEvent(
            "SEC-MSFT-001", "REMOVE", START + timedelta(days=3),
            START + timedelta(days=3), "gap:MSFT:remove",
        ),
        UniverseEvent(
            "SEC-MSFT-001", "ADD", START + timedelta(days=5),
            START + timedelta(days=5), "gap:MSFT:add-2",
        ),
    )
    with pytest.raises(ValueError, match="internal session gap"):
        _run_fixed_portfolio(
            market=market,
            universe_events=events,
            parameters={"enter_at": 999, "exit_at": 999},
            evaluation_end=START + timedelta(days=8),
        )


def test_dynamic_removal_executes_held_exit_on_next_tradable_bar():
    market = [
        replace(row, symbol=f"SEC-{row.symbol}-001")
        for row in _portfolio_bars(14)
        if row.symbol == "AAPL" or (row.open_at - START).days <= 11
    ]
    events = (
        UniverseEvent(
            "SEC-AAPL-001", "ADD", START, START,
            "synthetic-dynamic-exit:AAPL:add",
        ),
        UniverseEvent(
            "SEC-MSFT-001", "ADD", START, START,
            "synthetic-dynamic-exit:MSFT:add",
        ),
        UniverseEvent(
            "SEC-MSFT-001", "REMOVE",
            START + timedelta(days=10, hours=6, minutes=30),
            START + timedelta(days=10, hours=6, minutes=30),
            "synthetic-dynamic-exit:MSFT:remove",
        ),
    )
    _, result = _run_fixed_portfolio(
        market=market,
        universe_events=events,
        parameters={"enter_at": 4, "exit_at": 11},
        evaluation_end=START + timedelta(days=12),
    )
    exits = [
        row
        for row in result.executions
        if row.symbol == "SEC-MSFT-001" and row.action == "SELL"
    ]
    assert len(exits) == 1
    assert exits[0].reason == "UNIVERSE_REMOVAL"
    assert exits[0].signal_at == START + timedelta(days=10, hours=6, minutes=31)
    assert exits[0].executed_at == START + timedelta(days=11)
    assert any(
        trade.symbol == "SEC-MSFT-001"
        and trade.exit_reason == "UNIVERSE_REMOVAL"
        for trade in result.completed_trades
    )


def test_partial_universe_removal_exit_has_specific_coverage_failure():
    market = []
    for row in _portfolio_bars(14):
        session = (row.open_at - START).days
        if row.symbol == "MSFT" and session > 11:
            continue
        market.append(replace(
            row,
            symbol=f"SEC-{row.symbol}-001",
            volume=(
                Decimal("2500")
                if row.symbol == "MSFT" and session >= 7
                else row.volume
            ),
        ))
    events = (
        UniverseEvent("SEC-AAPL-001", "ADD", START, START, "partial:AAPL"),
        UniverseEvent("SEC-MSFT-001", "ADD", START, START, "partial:MSFT"),
        UniverseEvent(
            "SEC-MSFT-001",
            "REMOVE",
            START + timedelta(days=10, hours=6, minutes=30),
            START + timedelta(days=10, hours=6, minutes=30),
            "partial:MSFT:remove",
        ),
    )
    with pytest.raises(
        ValueError,
        match="universe-removal exit was not completed within its covered liquidity",
    ):
        _run_fixed_portfolio(
            market=market,
            universe_events=events,
            parameters={"enter_at": 4, "exit_at": 999},
            evaluation_end=START + timedelta(days=13),
        )


def test_late_membership_evidence_delays_eligibility_without_lookahead():
    market = _portfolio_bars(8)
    events = (
        UniverseEvent("AAPL", "ADD", START, START, "late-evidence:AAPL"),
        UniverseEvent(
            "MSFT",
            "ADD",
            START,
            START + timedelta(days=2),
            "late-evidence:MSFT",
        ),
    )
    _, result = _run_fixed_portfolio(
        market=market,
        universe_events=events,
        parameters={"enter_at": 999, "exit_at": 999},
        evaluation_end=START + timedelta(days=5),
    )
    counts = {
        symbol: sum(row.symbol == symbol for row in result.executive_intents)
        for symbol in ("AAPL", "MSFT")
    }
    assert counts == {"AAPL": 5, "MSFT": 3}


def test_late_removal_evidence_exits_only_after_it_is_available():
    market = [
        replace(row, symbol=f"SEC-{row.symbol}-001")
        for row in _portfolio_bars(14)
    ]
    msft_remove_effective = next(
        row.close_at
        for row in market
        if row.symbol == "SEC-MSFT-001" and (row.open_at - START).days == 4
    )
    msft_remove_available = next(
        row.open_at
        for row in market
        if row.symbol == "SEC-MSFT-001" and (row.open_at - START).days == 7
    )
    events = (
        UniverseEvent("SEC-AAPL-001", "ADD", START, START, "late-remove:AAPL"),
        UniverseEvent("SEC-MSFT-001", "ADD", START, START, "late-remove:MSFT"),
        UniverseEvent(
            "SEC-MSFT-001",
            "REMOVE",
            msft_remove_effective,
            msft_remove_available,
            "late-remove:MSFT:remove",
        ),
    )
    _, result = _run_fixed_portfolio(
        market=market,
        universe_events=events,
        parameters={"enter_at": 4, "exit_at": 10},
        evaluation_end=START + timedelta(days=12),
    )
    exits = [
        row
        for row in result.executions
        if row.symbol == "SEC-MSFT-001" and row.action == "SELL"
    ]
    assert len(exits) == 1
    assert exits[0].reason == "UNIVERSE_REMOVAL"
    assert exits[0].signal_at == msft_remove_available
    assert exits[0].executed_at == START + timedelta(days=7)
    assert not any(row.executed_at < START + timedelta(days=7) for row in exits)


def test_dynamic_membership_change_reduces_before_contested_cash_increase():
    market = []
    for row in _portfolio_bars(14):
        symbol = f"SEC-{row.symbol}-001"
        if row.symbol == "AAPL" and (row.open_at - START).days >= 5:
            market.append(replace(
                row,
                symbol=symbol,
                open=row.open + Decimal("50"),
                high=row.high + Decimal("50"),
                low=row.low + Decimal("50"),
                close=row.close + Decimal("50"),
            ))
        else:
            market.append(replace(row, symbol=symbol))
    msft_add = next(
        row.available_at
        for row in market
        if row.symbol == "SEC-MSFT-001" and (row.open_at - START).days == 5
    )
    events = (
        UniverseEvent("SEC-AAPL-001", "ADD", START, START, "dynamic-cash:AAPL"),
        UniverseEvent(
            "SEC-MSFT-001", "ADD", msft_add, msft_add, "dynamic-cash:MSFT"
        ),
    )
    parameters = {"enter_at": 4, "mixed_at": 6, "exit_at": 10}
    _, first = _run_fixed_portfolio(
        market=market,
        universe_events=events,
        parameters=parameters,
        evaluation_end=START + timedelta(days=12),
    )
    _, second = _run_fixed_portfolio(
        reverse=True,
        market=market,
        universe_events=tuple(reversed(events)),
        parameters=parameters,
        evaluation_end=START + timedelta(days=12),
    )
    change_execution_at = START + timedelta(days=6)
    change_actions = [
        (row.symbol, row.action)
        for row in first.executions
        if row.executed_at == change_execution_at and row.filled_quantity > 0
    ]
    assert change_actions == [
        ("SEC-AAPL-001", "SELL"),
        ("SEC-MSFT-001", "BUY"),
    ]
    msft_sizing = next(
        row
        for row in first.sizing_decisions
        if row.symbol == "SEC-MSFT-001"
        and row.action == "BUY"
        and row.evaluated_at == change_execution_at
    )
    assert "SHARED_CASH_RESERVATION" in msft_sizing.limiting_constraints
    assert first.executions == second.executions
    assert first.cash_reservations == second.cash_reservations


def test_terminal_settlement_supersedes_pending_partial_portfolio_stop():
    market = []
    for row in _portfolio_bars(16):
        session = (row.open_at - START).days
        if row.symbol == "AAPL" and session > 8:
            continue
        changed = replace(
            row,
            volume=(Decimal("2500") if session >= 5 else row.volume),
        )
        if row.symbol == "AAPL" and session == 8:
            changed = replace(changed, low=Decimal("90"))
        market.append(changed)
    terminal_at = START + timedelta(days=8, hours=6, minutes=30)
    outcome = TerminalOutcome(
        symbol="AAPL",
        terminal_type="DELISTED",
        effective_at=terminal_at,
        available_at=terminal_at,
        recovery_per_share=Decimal("0"),
        cash_settled_at=START + timedelta(days=10),
        source_locator="synthetic-terminal-after-partial-stop",
    )
    _, result = _run_fixed_portfolio(
        market=market,
        universe_events=(
            UniverseEvent("AAPL", "ADD", START, START, "terminal:AAPL:add"),
            UniverseEvent("MSFT", "ADD", START, START, "terminal:MSFT:add"),
            UniverseEvent(
                "AAPL", "REMOVE", terminal_at, terminal_at,
                "terminal:AAPL:remove",
            ),
        ),
        terminal_outcomes=(outcome,),
        parameters={"enter_at": 4, "exit_at": 12},
        evaluation_end=START + timedelta(days=13),
        initial_cash=Decimal("20000"),
    )
    stops = [
        row
        for row in result.executions
        if row.symbol == "AAPL" and row.reason == "HARD_ATR_STOP"
    ]
    assert stops and stops[-1].status == "PARTIALLY_FILLED"
    assert any(
        row.symbol == "AAPL"
        and row.action == "TERMINAL_SETTLEMENT"
        and row.executed_at == START + timedelta(days=9)
        for row in result.executions
    )
    assert any(
        trade.symbol == "AAPL" and trade.exit_reason == "DELISTED"
        for trade in result.completed_trades
    )
    assert any(
        state.event_type == "CASH_SETTLEMENT"
        and state.as_of_at == START + timedelta(days=10)
        and state.symbol == "__PORTFOLIO__"
        for state in result.portfolio_states
    )
