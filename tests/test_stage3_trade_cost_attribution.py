from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.guardrailed_backtest import BacktestResult, CompletedTrade, ExecutionRecord
from core.research.stage3_feature_strategy_evaluation import (
    _trade_and_cost_attribution,
)


OPENED = datetime(2025, 1, 2, 14, 30, tzinfo=timezone.utc)
CLOSED = OPENED + timedelta(days=2)


def _execution(action: str) -> ExecutionRecord:
    is_buy = action == "BUY"
    reference = Decimal("100") if is_buy else Decimal("110")
    execution = Decimal("101") if is_buy else Decimal("109")
    return ExecutionRecord(
        symbol="AAPL",
        action=action,
        reason="STRATEGY_SIGNAL" if is_buy else "EVALUATION_END",
        signal_at=OPENED - timedelta(days=1) if is_buy else CLOSED - timedelta(days=1),
        executed_at=OPENED if is_buy else CLOSED,
        reference_price=reference,
        execution_price=execution,
        requested_quantity=Decimal("2"),
        filled_quantity=Decimal("2"),
        fee=Decimal("1"),
        status="FILLED",
        lagged_liquidity_notional=Decimal("100000"),
        bid_ask_half_spread_bps=Decimal("5"),
        baseline_slippage_bps=Decimal("10"),
        latency_adverse_bps=Decimal("0"),
        liquidity_impact_bps=Decimal("0"),
        total_adverse_execution_bps=Decimal("15"),
    )


def _result(symbol: str, *, traded: bool) -> BacktestResult:
    if traded:
        buy = _execution("BUY")
        sell = _execution("SELL")
        trade = CompletedTrade(
            symbol="AAPL",
            opened_at=OPENED,
            closed_at=CLOSED,
            entry_total_cost=Decimal("203"),
            exit_net_proceeds=Decimal("217"),
            return_rate=Decimal("217") / Decimal("203") - Decimal("1"),
            exit_reason="EVALUATION_END",
        )
        executions = (buy, sell)
        trades = (trade,)
        ending = Decimal("100014")
    else:
        executions = ()
        trades = ()
        ending = Decimal("100000")
    return BacktestResult(
        strategy_version="test-v1",
        parameter_hash="a" * 64,
        source_id=f"SOURCE-{symbol}",
        validation_receipt_sha256="b" * 64,
        fee_schedule_id="TEST-FEES",
        execution_scenario="BASE",
        starting_equity=Decimal("100000"),
        ending_equity=ending,
        total_return=ending / Decimal("100000") - Decimal("1"),
        maximum_drawdown=Decimal("0"),
        executions=executions,
        completed_trades=trades,
        equity_curve=(),
    )


def _results() -> dict[str, BacktestResult]:
    return {
        "AAPL": _result("AAPL", traded=True),
        "MSFT": _result("MSFT", traded=False),
        "SPY": _result("SPY", traded=False),
    }


def test_trade_cost_attribution_binds_stable_execution_indices_and_reconciles():
    logs, attribution = _trade_and_cost_attribution(
        _results(), initial_equity=Decimal("300000")
    )
    assert len(logs) == 1
    assert logs[0]["execution_indices"] == [0, 1]
    assert logs[0]["entry_execution_index"] == 0
    assert logs[0]["exit_execution_indices"] == [1]
    assert logs[0]["net_profit_loss"] == "14"
    assert logs[0]["fees"] == "2"
    assert logs[0]["adverse_execution_cost"] == "4"
    assert attribution["combined_execution_cost"] == "6"
    assert attribution["cash_pnl_reconciliation_residual"] == "0"
    repeated, _ = _trade_and_cost_attribution(
        _results(), initial_equity=Decimal("300000")
    )
    assert repeated[0]["trade_id"] == logs[0]["trade_id"]


def test_trade_cost_attribution_handles_multiple_trades_and_tied_partial_exits():
    results = _results()
    base = results["AAPL"]
    buy, sell = base.executions
    first_partial_exit = replace(
        sell,
        reason="LIQUIDITY_PARTIAL",
        requested_quantity=Decimal("2"),
        filled_quantity=Decimal("1"),
        fee=Decimal("0.5"),
        status="PARTIALLY_FILLED",
    )
    first_final_exit = replace(
        sell,
        requested_quantity=Decimal("1"),
        filled_quantity=Decimal("1"),
        fee=Decimal("0.5"),
    )
    second_opened = CLOSED + timedelta(days=1)
    second_closed = second_opened + timedelta(days=2)
    second_buy = replace(
        buy,
        signal_at=second_opened - timedelta(days=1),
        executed_at=second_opened,
    )
    second_sell = replace(
        sell,
        signal_at=second_closed - timedelta(days=1),
        executed_at=second_closed,
    )
    second_trade = replace(
        base.completed_trades[0],
        opened_at=second_opened,
        closed_at=second_closed,
    )
    results["AAPL"] = replace(
        base,
        ending_equity=Decimal("100028"),
        executions=(
            buy,
            first_partial_exit,
            first_final_exit,
            second_buy,
            second_sell,
        ),
        completed_trades=(base.completed_trades[0], second_trade),
    )
    logs, attribution = _trade_and_cost_attribution(
        results, initial_equity=Decimal("300000")
    )
    assert [row["execution_indices"] for row in logs] == [[0, 1, 2], [3, 4]]
    assert logs[0]["exit_execution_indices"] == [1, 2]
    assert logs[0]["exit_reason"] == "EVALUATION_END"
    assert attribution["filled_execution_count"] == 5

    rejected = replace(
        buy,
        executed_at=OPENED - timedelta(days=1),
        filled_quantity=Decimal("0"),
        status="REJECTED",
    )
    shifted = dict(results)
    shifted["AAPL"] = replace(
        results["AAPL"], executions=(rejected, *results["AAPL"].executions)
    )
    shifted_logs, _ = _trade_and_cost_attribution(
        shifted, initial_equity=Decimal("300000")
    )
    assert [row["trade_id"] for row in shifted_logs] == [
        row["trade_id"] for row in logs
    ]
    assert shifted_logs[0]["execution_indices"] == [1, 2, 3]


def test_trade_cost_attribution_rejects_overlapping_round_trip_windows():
    results = _results()
    base = results["AAPL"]
    overlapping = replace(
        base.completed_trades[0],
        opened_at=OPENED + timedelta(days=1),
        closed_at=CLOSED + timedelta(days=1),
    )
    results["AAPL"] = replace(
        base, completed_trades=(base.completed_trades[0], overlapping)
    )
    with pytest.raises(ValueError, match="windows overlap"):
        _trade_and_cost_attribution(results, initial_equity=Decimal("300000"))


@pytest.mark.parametrize(
    ("tamper", "message"),
    (
        (
            lambda result: replace(result, executions=result.executions[:1]),
            "exact filled execution support",
        ),
        (
            lambda result: replace(result, completed_trades=()),
            "not attributed to a completed trade",
        ),
        (
            lambda result: replace(
                result,
                completed_trades=(
                    replace(result.completed_trades[0], entry_total_cost=Decimal("202")),
                ),
            ),
            "cost differs from its filled entry",
        ),
        (
            lambda result: replace(result, ending_equity=Decimal("100015")),
            "cash P&L residual",
        ),
    ),
)
def test_trade_cost_attribution_tampering_fails_closed(tamper, message):
    results = _results()
    results["AAPL"] = tamper(results["AAPL"])
    with pytest.raises(ValueError, match=message):
        _trade_and_cost_attribution(results, initial_equity=Decimal("300000"))


def test_trade_cost_attribution_rejects_unexpected_result_sleeve():
    results = _results()
    results["QQQ"] = _result("QQQ", traded=False)
    with pytest.raises(ValueError, match="exact campaign symbol set"):
        _trade_and_cost_attribution(results, initial_equity=Decimal("400000"))
