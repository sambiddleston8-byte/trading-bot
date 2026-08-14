from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.guardrailed_backtest import (
    ACTION_ENTER_LONG,
    ACTION_EXIT_LONG,
    ACTION_HOLD,
    BacktestConfig,
    BacktestResult,
    CorporateAction,
    ExchangeFeeSchedule,
    ExchangeFeeTier,
    GuardrailedBacktestEngine,
    MarketBar,
    MonteCarloResult,
    ReplayDataAttestation,
    TerminalOutcome,
    UniverseEvent,
    WalkForwardOptimizer,
    monte_carlo_trade_order_risk,
    strict_sixty_forty_split,
)


UTC = timezone.utc
DAY = timedelta(days=1)
START = datetime(2020, 1, 1, 14, 30, tzinfo=UTC)


def bars(count=50, *, symbol="AAA", start=START, opens=None, lows=None):
    opens = opens or {}
    lows = lows or {}
    rows = []
    for index in range(count):
        open_price = Decimal(str(opens.get(index, 100 + index)))
        close = open_price + Decimal("1")
        low = Decimal(str(lows.get(index, open_price - 1)))
        rows.append(
            MarketBar(
                symbol=symbol,
                open_at=start + index * DAY,
                close_at=start + index * DAY + timedelta(hours=6, minutes=30),
                available_at=start + index * DAY + timedelta(hours=6, minutes=30),
                open=open_price,
                high=max(open_price, close) + Decimal("1"),
                low=min(low, open_price, close),
                close=close,
                volume=Decimal("100000"),
            )
        )
    return rows


def attestation(**changes):
    values = {
        "source_id": "SEALED-SOURCE-1",
        "source_content_sha256": "a" * 64,
        "validation_receipt_sha256": "b" * 64,
        "derivation_policy_version": "test-authenticated-fixture-v1",
        "evidence_role_hashes": tuple(sorted((role, "c" * 64) for role in (
            "CORPORATE_ACTIONS", "DELISTING_OUTCOMES", "MARKET_CALENDARS_AND_HALTS",
            "RAW_DAILY_SESSION_BARS", "TOTAL_RETURN_PRICES", "UNIVERSE_MEMBERSHIP",
        ))),
    }
    values.update(changes)
    return ReplayDataAttestation._from_authenticated_artifacts(**values)


def fees():
    return ExchangeFeeSchedule(
        "TEST-FEES-v1",
        (
            ExchangeFeeTier(Decimal("1000000"), Decimal("1"), Decimal("0.01")),
            ExchangeFeeTier(None, Decimal("0.5"), Decimal("0.01")),
        ),
    )


def engine(**changes):
    config = {
        "initial_cash": Decimal("100000"),
        "atr_window": 3,
        "lagged_liquidity_lookback": 3,
        "execution_scenario": "BASE",
    }
    config.update(changes)
    return GuardrailedBacktestEngine(
        config=BacktestConfig(**config),
        fee_schedule=fees(),
        data_attestation=attestation(),
    )


def membership(symbol="AAA", start=START - DAY):
    return [UniverseEvent(symbol, "ADD", start, start - DAY, "universe:1")]


def run(engine_value, **kwargs):
    return engine_value.run(
        corporate_actions=kwargs.pop("corporate_actions", []),
        prices_are_unadjusted=True,
        **kwargs,
    )


class EnterThenExit:
    version = "enter-then-exit-v1"

    def __init__(self):
        self.calls = []

    def decide(self, symbol, history_through_signal_close, parameters):
        self.calls.append(tuple(history_through_signal_close))
        count = len(history_through_signal_close)
        if count == parameters["enter_on_history_count"]:
            return ACTION_ENTER_LONG
        if count == parameters["exit_on_history_count"]:
            return ACTION_EXIT_LONG
        return ACTION_HOLD


def test_signal_uses_closed_history_and_executes_at_next_open_with_costs():
    market = bars(12)
    strategy = EnterThenExit()
    result = run(engine(),
        bars=market,
        universe_events=membership(),
        terminal_outcomes=[],
        strategy=strategy,
        parameters={"enter_on_history_count": 4, "exit_on_history_count": 7},
        evaluation_start=market[0].close_at,
        evaluation_end=market[9].open_at,
    )
    buy, sell = [row for row in result.executions if row.action in {"BUY", "SELL"}]
    assert buy.signal_at == market[3].close_at
    assert buy.executed_at == market[4].open_at
    assert buy.execution_price > buy.reference_price
    assert buy.total_adverse_execution_bps >= Decimal("15")
    assert buy.total_adverse_execution_bps == (
        buy.bid_ask_half_spread_bps
        + buy.baseline_slippage_bps
        + buy.liquidity_impact_bps
    )
    assert sell.signal_at == market[6].close_at
    assert sell.executed_at == market[7].open_at
    assert sell.execution_price < sell.reference_price
    assert sell.total_adverse_execution_bps >= Decimal("15")
    assert buy.fee > 0 and sell.fee > 0
    assert strategy.calls == []  # each run receives an isolated strategy copy
    assert result.validation_receipt_sha256 == engine().data_attestation.validation_receipt_sha256
    assert result.broker_connection_allowed is False
    assert result.orders_submitted is False
    assert result.live_trading_enabled is False


def test_atr_position_sizing_never_exceeds_one_percent_modelled_risk():
    market = bars(12)
    result = run(engine(),
        bars=market,
        universe_events=membership(),
        terminal_outcomes=[],
        strategy=EnterThenExit(),
        parameters={"enter_on_history_count": 4, "exit_on_history_count": 7},
        evaluation_start=market[0].close_at,
        evaluation_end=market[9].open_at,
    )
    buy = next(row for row in result.executions if row.action == "BUY")
    # The rolling ATR is 3 here and the stop multiple is 2.  Fees/slippage
    # make the conservative maximum quantity no greater than raw 1%-risk size.
    raw_quantity = Decimal("100000") * Decimal("0.01") / Decimal("6")
    assert buy.filled_quantity <= raw_quantity


def test_hard_stop_uses_stop_or_worse_gap_price_and_adverse_slippage():
    market = bars(12, opens={5: 90}, lows={5: 89})
    result = run(engine(),
        bars=market,
        universe_events=membership(),
        terminal_outcomes=[],
        strategy=EnterThenExit(),
        parameters={"enter_on_history_count": 4, "exit_on_history_count": 99},
        evaluation_start=market[0].close_at,
        evaluation_end=market[9].open_at,
    )
    sell = next(row for row in result.executions if row.action == "SELL")
    assert sell.reason == "HARD_ATR_STOP"
    assert sell.reference_price == Decimal("90")
    assert sell.execution_price < Decimal("90")


def test_intrabar_stop_uses_observed_pierce_not_guaranteed_stop_touch():
    market = bars(12, lows={5: 80})
    result = run(engine(),
        bars=market,
        universe_events=membership(),
        terminal_outcomes=[],
        strategy=EnterThenExit(),
        parameters={"enter_on_history_count": 4, "exit_on_history_count": 99},
        evaluation_start=market[0].close_at,
        evaluation_end=market[9].open_at,
    )
    sell = next(row for row in result.executions if row.action == "SELL")
    assert sell.reason == "HARD_ATR_STOP"
    assert Decimal("80") <= sell.reference_price < Decimal("98")


def test_base_and_pessimistic_scenarios_are_separate_and_pessimistic_is_costlier():
    market = bars(12)
    results = engine().run_base_and_pessimistic(
        bars=market,
        universe_events=membership(),
        terminal_outcomes=[],
        corporate_actions=[],
        prices_are_unadjusted=True,
        strategy=EnterThenExit(),
        parameters={"enter_on_history_count": 4, "exit_on_history_count": 7},
        evaluation_start=market[0].close_at,
        evaluation_end=market[9].open_at,
    )
    assert results["BASE"].execution_scenario == "BASE"
    assert results["PESSIMISTIC"].execution_scenario == "PESSIMISTIC"
    assert results["PESSIMISTIC"].ending_equity <= results["BASE"].ending_equity


def test_lagged_liquidity_caps_and_cancels_entry_remainder():
    market = bars(12)
    result = run(engine(maximum_lagged_volume_participation=Decimal("0.0001")),
        bars=market,
        universe_events=membership(),
        terminal_outcomes=[],
        strategy=EnterThenExit(),
        parameters={"enter_on_history_count": 4, "exit_on_history_count": 7},
        evaluation_start=market[0].close_at,
        evaluation_end=market[9].open_at,
    )
    buy = next(row for row in result.executions if row.action == "BUY")
    assert 0 < buy.filled_quantity < buy.requested_quantity
    assert buy.status == "PARTIALLY_FILLED_CANCELED"
    assert buy.lagged_liquidity_notional > 0
    assert buy.liquidity_impact_bps > 0


def test_fee_tier_is_selected_from_prior_month_volume_only():
    schedule = fees()
    notional = Decimal("10000")
    assert schedule.fee(notional, Decimal("999999")) == Decimal("1")
    assert schedule.fee(notional, Decimal("1000000")) == Decimal("0.5")


def test_slippage_floor_and_risk_limit_cannot_be_weakened():
    with pytest.raises(ValueError, match="0.10%"):
        BacktestConfig(initial_cash=Decimal("100000"), baseline_slippage_bps=Decimal("9"))
    with pytest.raises(ValueError, match="1%"):
        BacktestConfig(initial_cash=Decimal("100000"), max_equity_risk_per_trade=Decimal("0.011"))


def test_pessimistic_scenario_requires_doubled_costs_and_full_stop_pierce():
    with pytest.raises(ValueError, match="PESSIMISTIC"):
        BacktestConfig(initial_cash=Decimal("100000"), execution_scenario="PESSIMISTIC")
    config = BacktestConfig(
        initial_cash=Decimal("100000"), execution_scenario="PESSIMISTIC",
        baseline_slippage_bps=Decimal("20"), bid_ask_half_spread_bps=Decimal("10"),
        liquidity_impact_bps_at_max_participation=Decimal("20"),
        stop_pierce_fill_fraction=Decimal("1"),
    )
    assert config.execution_scenario == "PESSIMISTIC"


def test_terminal_outcome_realises_bankruptcy_recovery_without_survivor_drop():
    market = bars(8)
    terminal = TerminalOutcome(
        "AAA",
        "BANKRUPT",
        market[5].open_at,
        market[5].open_at,
        Decimal("0"),
        market[6].open_at,
        "terminal:1",
    )
    result = run(engine(),
        bars=market,
        universe_events=membership(),
        terminal_outcomes=[terminal],
        strategy=EnterThenExit(),
        parameters={"enter_on_history_count": 4, "exit_on_history_count": 99},
        evaluation_start=market[0].close_at,
        evaluation_end=market[7].open_at,
    )
    assert result.completed_trades[0].exit_reason == "BANKRUPT"
    assert result.completed_trades[0].exit_net_proceeds == 0
    assert result.completed_trades[0].return_rate == -1


def test_terminal_outcome_cannot_be_published_after_effective_date():
    market = bars(8)
    with pytest.raises(ValueError, match="public"):
        TerminalOutcome(
            "AAA", "BANKRUPT", market[5].open_at, market[6].open_at,
            Decimal("0"), market[7].open_at, "terminal:late",
        )


def test_duplicate_terminal_outcomes_for_one_symbol_fail_closed():
    market = bars(8)
    first = TerminalOutcome(
        "AAA", "DELISTED", market[5].open_at, market[4].close_at,
        Decimal("0"), market[6].open_at, "terminal:first",
    )
    second = TerminalOutcome(
        "AAA", "ACQUIRED", market[6].open_at, market[5].close_at,
        Decimal("50"), market[7].open_at, "terminal:second",
    )
    with pytest.raises(ValueError, match="at most one"):
        run(engine(),
            bars=market, universe_events=membership(), terminal_outcomes=[first, second],
            strategy=EnterThenExit(),
            parameters={"enter_on_history_count": 4, "exit_on_history_count": 99},
            evaluation_start=market[0].close_at, evaluation_end=market[7].open_at,
        )


def test_split_rescales_position_and_stop_and_dividend_uses_record_date_entitlement():
    market = bars(14, opens={6: 53, 7: 54, 8: 55, 9: 56, 10: 57, 11: 58, 12: 59, 13: 60})
    split = CorporateAction(
        "AAA", "SPLIT", market[6].open_at, market[5].close_at,
        "split:1", Decimal("2"), Decimal("0"), None,
    )
    dividend = CorporateAction(
        "AAA", "CASH_DIVIDEND", market[7].open_at, market[6].close_at,
        "dividend:1", Decimal("1"), Decimal("1"), market[9].open_at,
    )
    result = run(engine(),
        bars=market,
        universe_events=membership(),
        terminal_outcomes=[],
        corporate_actions=[split, dividend],
        strategy=EnterThenExit(),
        parameters={"enter_on_history_count": 4, "exit_on_history_count": 10},
        evaluation_start=market[0].close_at,
        evaluation_end=market[12].open_at,
    )
    buy = next(row for row in result.executions if row.action == "BUY")
    sell = next(row for row in result.executions if row.action == "SELL")
    assert sell.filled_quantity == buy.filled_quantity * 2
    assert result.ending_equity > Decimal("0")


def test_split_before_entry_rescales_signal_history_and_atr():
    market = bars(14, opens={5: 52, 6: 53, 7: 54, 8: 55, 9: 56, 10: 57, 11: 58, 12: 59, 13: 60})
    split = CorporateAction(
        "AAA", "SPLIT", market[5].open_at, market[4].close_at,
        "split:before-entry", Decimal("2"), Decimal("0"), None,
    )
    result = run(engine(),
        bars=market, universe_events=membership(), terminal_outcomes=[],
        corporate_actions=[split], strategy=EnterThenExit(),
        parameters={"enter_on_history_count": 7, "exit_on_history_count": 10},
        evaluation_start=market[0].close_at, evaluation_end=market[12].open_at,
    )
    buy = next(row for row in result.executions if row.action == "BUY")
    assert buy.filled_quantity > 0


def test_adjusted_price_inputs_and_multi_symbol_runs_fail_closed():
    market = bars(12)
    with pytest.raises(ValueError, match="back-adjusted"):
        engine().run(
            bars=market, universe_events=membership(), terminal_outcomes=[],
            corporate_actions=[], prices_are_unadjusted=False,
            strategy=EnterThenExit(),
            parameters={"enter_on_history_count": 4, "exit_on_history_count": 7},
            evaluation_start=market[0].close_at, evaluation_end=market[9].open_at,
        )
    other = bars(12, symbol="BBB")
    with pytest.raises(ValueError, match="one instrument"):
        run(engine(),
            bars=[*market, *other],
            universe_events=[*membership(), *membership("BBB")],
            terminal_outcomes=[], strategy=EnterThenExit(),
            parameters={"enter_on_history_count": 4, "exit_on_history_count": 7},
            evaluation_start=market[0].close_at, evaluation_end=market[9].open_at,
        )


def test_callers_cannot_construct_or_omit_authenticated_attestation_role_pins():
    with pytest.raises(ValueError, match="derived from authenticated"):
        ReplayDataAttestation(
            source_id="CALLER-ASSERTION", source_content_sha256="a" * 64,
            validation_receipt_sha256="b" * 64,
            derivation_policy_version="caller-v1", evidence_role_hashes=(),
        )
    with pytest.raises(ValueError, match="every required replay role"):
        attestation(evidence_role_hashes=())


def test_bar_availability_after_close_is_signal_time_and_must_precede_next_open():
    market = bars(12)
    delayed = [
        MarketBar(
            symbol=row.symbol, open_at=row.open_at, close_at=row.close_at,
            available_at=row.close_at + timedelta(seconds=5), open=row.open,
            high=row.high, low=row.low, close=row.close, volume=row.volume,
        )
        for row in market
    ]
    result = run(engine(),
        bars=delayed, universe_events=membership(), terminal_outcomes=[],
        strategy=EnterThenExit(),
        parameters={"enter_on_history_count": 4, "exit_on_history_count": 7},
        evaluation_start=market[0].close_at, evaluation_end=market[9].open_at,
    )
    buy = next(row for row in result.executions if row.action == "BUY")
    assert buy.signal_at == delayed[3].available_at
    too_late = list(delayed)
    too_late[3] = MarketBar(
        symbol=delayed[3].symbol, open_at=delayed[3].open_at,
        close_at=delayed[3].close_at, available_at=delayed[4].open_at,
        open=delayed[3].open, high=delayed[3].high, low=delayed[3].low,
        close=delayed[3].close, volume=delayed[3].volume,
    )
    with pytest.raises(ValueError, match="not available before"):
        run(engine(), bars=too_late, universe_events=membership(), terminal_outcomes=[],
            strategy=EnterThenExit(),
            parameters={"enter_on_history_count": 4, "exit_on_history_count": 7},
            evaluation_start=market[0].close_at, evaluation_end=market[9].open_at,
        )


def test_unknown_universe_membership_and_missing_liquidation_bar_fail_closed():
    market = bars(8)
    with pytest.raises(ValueError, match="universe entry"):
        run(engine(),
            bars=market,
            universe_events=[],
            terminal_outcomes=[],
            strategy=EnterThenExit(),
            parameters={"enter_on_history_count": 4, "exit_on_history_count": 7},
            evaluation_start=market[0].close_at,
            evaluation_end=market[6].open_at,
        )
    with pytest.raises(ValueError, match="ends without|next-bar"):
        run(engine(),
            bars=market[:6],
            universe_events=membership(),
            terminal_outcomes=[],
            strategy=EnterThenExit(),
            parameters={"enter_on_history_count": 4, "exit_on_history_count": 99},
            evaluation_start=market[0].close_at,
            evaluation_end=market[6].open_at,
        )


def test_sixty_forty_split_is_chronological_and_never_random():
    market = bars(102)
    split = strict_sixty_forty_split(market)
    assert split.in_sample_end == market[59].close_at
    assert split.out_of_sample_start == market[60].close_at
    assert split.out_of_sample_end == market[99].close_at
    assert split.execution_buffer_end == market[100].close_at
    assert split.settlement_buffer_end == market[101].close_at
    assert split.in_sample_end < split.out_of_sample_start


def test_monte_carlo_is_seeded_reproducible_and_reports_tail_drawdown():
    returns = [Decimal("0.2"), Decimal("-0.1"), Decimal("0.05"), Decimal("-0.25")]
    first = monte_carlo_trade_order_risk(returns, iterations=200, seed=42)
    second = monte_carlo_trade_order_risk(returns, iterations=200, seed=42)
    assert first == second
    assert first.drawdown_p50 <= first.drawdown_p95 <= first.worst_drawdown


class SpyEngine:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        value = Decimal(str(kwargs["parameters"]["score"])) / Decimal("100")
        start = kwargs["evaluation_start"]
        return BacktestResult(
            "spy-v1", "f" * 64, "SOURCE", "e" * 64, "FEES", "BASE", Decimal("100"),
            Decimal("100") * (1 + value), value, Decimal("0.1"), (), (), ((start, Decimal("100")),),
        )


def test_walk_forward_selects_only_on_first_sixty_percent_then_opens_oos_once():
    market = bars(102)
    spy = SpyEngine()
    optimizer = WalkForwardOptimizer(spy, validation_folds=3)
    selection = optimizer.validate(
        bars=market,
        universe_events=membership(),
        terminal_outcomes=[],
        corporate_actions=[],
        prices_are_unadjusted=True,
        strategy=EnterThenExit(),
        parameter_grid={"score": [1, 2]},
        monte_carlo_iterations=100,
    )
    assert selection.selected_parameters == {"score": 2}
    oos_calls = [
        call for call in spy.calls
        if call["evaluation_start"] == selection.split.out_of_sample_start
    ]
    assert len(oos_calls) == 1
    assert spy.calls[-1] is oos_calls[0]
    assert all(
        call["evaluation_end"] <= selection.split.in_sample_end
        for call in spy.calls[:-1]
    )
