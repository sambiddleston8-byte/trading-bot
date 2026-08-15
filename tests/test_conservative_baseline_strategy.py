from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from pathlib import Path

import pandas as pd
import pytest

from core.guardrailed_backtest import (
    ACTION_ENTER_LONG,
    ACTION_EXIT_LONG,
    ACTION_HOLD,
    AUTHENTICATED_REPLAY_ROLES,
    BacktestConfig,
    ExchangeFeeSchedule,
    ExchangeFeeTier,
    GuardrailedBacktestEngine,
    MarketBar,
    ReplayDataAttestation,
    UniverseEvent,
    strategy_parameter_hash,
)
from core.research.conservative_baseline_strategy import (
    ConservativeBaselineStrategy,
    conservative_baseline_parameters,
)
from core.historical_signal import HistoricalSignalEngine


UTC = timezone.utc
DAY = timedelta(days=1)
START = datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
SOURCE = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "research"
    / "conservative_baseline_strategy.py"
)


def bars(closes: list[Decimal], *, symbol: str = "AAA") -> list[MarketBar]:
    rows = []
    for index, close in enumerate(closes):
        opened = START + index * DAY
        open_price = close - Decimal("0.25")
        rows.append(
            MarketBar(
                symbol=symbol,
                open_at=opened,
                close_at=opened + timedelta(hours=6, minutes=30),
                available_at=opened + timedelta(hours=6, minutes=30),
                open=open_price,
                high=close + Decimal("0.50"),
                low=open_price - Decimal("0.50"),
                close=close,
                volume=Decimal("1000000"),
            )
        )
    return rows


def attestation() -> ReplayDataAttestation:
    return ReplayDataAttestation._from_authenticated_artifacts(
        source_id="SYNTHETIC-TEST-ATTESTATION",
        source_content_sha256="a" * 64,
        validation_receipt_sha256="b" * 64,
        derivation_policy_version="synthetic-test-only-v1",
        evidence_role_hashes=tuple(
            sorted((role, "c" * 64) for role in AUTHENTICATED_REPLAY_ROLES)
        ),
    )


def strategy() -> ConservativeBaselineStrategy:
    return ConservativeBaselineStrategy()


def scored_history(*, trend: str, momentum_percent: int) -> list[MarketBar]:
    if trend == "BULLISH":
        reference = Decimal("100")
        closes = [reference] * 30 + [Decimal("200")] * 19
    elif trend == "BEARISH":
        reference = Decimal("200")
        closes = [reference] * 30 + [Decimal("100")] * 19
    else:
        raise ValueError("unsupported synthetic trend")
    closes.append(reference * (Decimal("1") + Decimal(momentum_percent) / Decimal("100")))
    return bars(closes)


def test_fixed_parameters_are_fresh_complete_and_parameter_search_is_disabled():
    first = conservative_baseline_parameters()
    second = conservative_baseline_parameters()

    assert first == {
        "fast_period": 20,
        "slow_period": 50,
        "momentum_period": 20,
        "entry_minimum_score": 80,
        "exit_maximum_score": 60,
        "insufficient_history_action": ACTION_HOLD,
        "parameter_search_allowed": False,
    }
    assert first is not second
    first["fast_period"] = 10
    assert second["fast_period"] == 20


@pytest.mark.parametrize(
    ("closes", "expected_score", "expected_action"),
    [
        ([Decimal(100) + index for index in range(50)], 90, ACTION_ENTER_LONG),
        (
            [Decimal("100") + Decimal(index) / Decimal("2") for index in range(50)],
            80,
            ACTION_ENTER_LONG,
        ),
        (
            [Decimal("100") + Decimal(index) / Decimal("10") for index in range(50)],
            70,
            ACTION_HOLD,
        ),
        ([Decimal(200) - Decimal(index * 2) for index in range(50)], 10, ACTION_EXIT_LONG),
    ],
)
def test_decimal_score_and_approved_action_mapping(closes, expected_score, expected_action):
    history = bars(closes)
    parameters = conservative_baseline_parameters()

    assert strategy().score("AAA", history, parameters) == expected_score
    assert strategy().decide("AAA", history, parameters) == expected_action


def test_insufficient_history_holds_without_fabricating_a_score():
    history = bars([Decimal(100) + index for index in range(49)])
    parameters = conservative_baseline_parameters()

    assert strategy().score("AAA", history, parameters) is None
    assert strategy().decide("AAA", history, parameters) == ACTION_HOLD


def test_decimal_kernel_matches_legacy_scores_across_the_full_attainable_set():
    parameters = conservative_baseline_parameters()
    legacy = HistoricalSignalEngine(fast_period=20, slow_period=50, momentum_period=20)
    observed = set()
    for trend in ("BULLISH", "BEARISH"):
        for momentum_percent in (-20, -10, 0, 10, 20):
            history = scored_history(trend=trend, momentum_percent=momentum_percent)
            decimal_score = strategy().score("AAA", history, parameters)
            legacy_score = legacy.generate_signal(
                pd.DataFrame({"Close": [float(row.close) for row in history]})
            )["Score"]
            assert decimal_score == legacy_score
            observed.add(decimal_score)
    assert observed == {10, 20, 30, 40, 50, 60, 70, 80, 90}


@pytest.mark.parametrize(
    ("trend", "momentum_percent", "expected_score"),
    [
        ("BULLISH", 15, 80),
        ("BULLISH", 5, 70),
        ("BEARISH", -5, 30),
        ("BEARISH", -15, 20),
    ],
)
def test_exact_momentum_boundaries_preserve_strict_legacy_comparisons(
    trend, momentum_percent, expected_score
):
    assert strategy().score(
        "AAA",
        scored_history(trend=trend, momentum_percent=momentum_percent),
        conservative_baseline_parameters(),
    ) == expected_score


def test_exact_moving_average_tie_uses_the_legacy_bearish_branch():
    history = bars([Decimal("100")] * 50)

    assert strategy().score(
        "AAA", history, conservative_baseline_parameters()
    ) == 30
    assert strategy().decide(
        "AAA", history, conservative_baseline_parameters()
    ) == ACTION_EXIT_LONG


@pytest.mark.parametrize(
    "change",
    [
        {"fast_period": 10},
        {"entry_minimum_score": True},
        {"parameter_search_allowed": 0},
        {"extra": "field"},
    ],
)
def test_parameter_substitution_and_type_confusion_fail_closed(change):
    parameters = conservative_baseline_parameters()
    parameters.update(change)

    with pytest.raises(ValueError, match="fixed baseline"):
        strategy().decide(
            "AAA",
            bars([Decimal(100) + index for index in range(50)]),
            parameters,
        )


def test_mixed_symbol_and_delayed_prior_bar_are_rejected():
    history = bars([Decimal(100) + index for index in range(50)])
    mixed = [*history[:-1], bars([Decimal("149")], symbol="BBB")[0]]
    with pytest.raises(ValueError, match="matching symbol"):
        strategy().decide("AAA", mixed, conservative_baseline_parameters())

    first, second = history[0], history[1]
    delayed = MarketBar(
        symbol=first.symbol,
        open_at=first.open_at,
        close_at=first.close_at,
        available_at=second.open_at + timedelta(minutes=1),
        open=first.open,
        high=first.high,
        low=first.low,
        close=first.close,
        volume=first.volume,
    )
    with pytest.raises(ValueError, match="causally ordered"):
        strategy().decide(
            "AAA",
            [delayed, *history[1:]],
            conservative_baseline_parameters(),
        )


def test_tampered_non_positive_close_fails_closed_before_momentum_division():
    history = bars([Decimal(100) + index for index in range(50)])
    object.__setattr__(history[-21], "close", Decimal("0"))

    with pytest.raises(ValueError, match="close values must remain positive"):
        strategy().decide("AAA", history, conservative_baseline_parameters())


def test_repeated_decisions_are_pure_and_do_not_mutate_inputs():
    history = bars([Decimal(100) + index for index in range(50)])
    parameters = conservative_baseline_parameters()
    original_history = tuple(history)
    original_parameters = dict(parameters)
    target = strategy()

    first = target.decide("AAA", history, parameters)
    second = target.decide("AAA", history, parameters)

    assert first == second == ACTION_ENTER_LONG
    assert tuple(history) == original_history
    assert parameters == original_parameters
    assert target.__dict__ == {}


def test_guardrailed_engine_executes_strategy_signals_only_at_next_open():
    closes = [Decimal(100) + index for index in range(55)]
    closes.extend(Decimal(154) - Decimal(2 * (index + 1)) for index in range(35))
    market = bars(closes)
    parameters = conservative_baseline_parameters()
    actions = [
        strategy().decide("AAA", market[: index + 1], parameters)
        for index in range(len(market))
    ]
    entry_index = actions.index(ACTION_ENTER_LONG)
    exit_index = next(
        index
        for index, action in enumerate(actions[entry_index + 1 :], start=entry_index + 1)
        if action == ACTION_EXIT_LONG
    )
    evaluation_end = market[min(exit_index + 3, len(market) - 1)].open_at
    fee_schedule = ExchangeFeeSchedule(
        "SYNTHETIC-TEST-FEES-v1",
        (ExchangeFeeTier(None, Decimal("1"), Decimal("0.01")),),
    )
    engine = GuardrailedBacktestEngine(
        config=BacktestConfig(
            initial_cash=Decimal("100000"),
            atr_window=3,
            atr_stop_multiple=Decimal("100"),
            lagged_liquidity_lookback=3,
        ),
        fee_schedule=fee_schedule,
        data_attestation=attestation(),
    )

    result = engine.run(
        bars=market,
        universe_events=[
            UniverseEvent(
                "AAA", "ADD", START - DAY, START - 2 * DAY, "synthetic:membership"
            )
        ],
        terminal_outcomes=[],
        corporate_actions=[],
        strategy=strategy(),
        parameters=parameters,
        evaluation_start=market[0].close_at,
        evaluation_end=evaluation_end,
        prices_are_unadjusted=True,
    )

    buy = next(row for row in result.executions if row.action == "BUY")
    sell = next(
        row
        for row in result.executions
        if row.action == "SELL" and row.reason == "STRATEGY_SIGNAL"
    )
    assert buy.signal_at == market[entry_index].available_at
    assert buy.executed_at == market[entry_index + 1].open_at
    assert sell.signal_at == market[exit_index].available_at
    assert sell.executed_at == market[exit_index + 1].open_at
    assert buy.executed_at > buy.signal_at
    assert sell.executed_at > sell.signal_at
    assert result.strategy_entrypoint.endswith(":ConservativeBaselineStrategy")
    assert result.parameter_hash == strategy_parameter_hash(parameters)
    assert result.strategy_source_sha256 == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert result.broker_connection_allowed is False
    assert result.orders_submitted is False
    assert result.live_trading_enabled is False


def test_strategy_module_has_no_network_provider_vectorbt_or_broker_capability():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imports.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert imports <= {
        "__future__",
        "decimal",
        "typing",
        "core.guardrailed_backtest",
    }
    dynamic_capabilities = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not dynamic_capabilities & {"__import__", "eval", "exec", "open"}
    source = SOURCE.read_text(encoding="utf-8")
    for forbidden in ("submit_order", "cancel_order", "replace_order", "MASSIVE_API_KEY"):
        assert forbidden not in source
