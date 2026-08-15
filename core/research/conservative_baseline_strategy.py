"""Fixed causal trend/momentum baseline for authenticated replay research.

This module contains decision logic only.  It has no data-loading, VectorBT,
broker, credential, order, persistence or network capability.  The replay
engine supplies history through one completed bar and exclusively controls any
later simulated execution.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from core.guardrailed_backtest import (
    ACTION_ENTER_LONG,
    ACTION_EXIT_LONG,
    ACTION_HOLD,
    MarketBar,
)


POLICY_VERSION = "fixed-conservative-trend-momentum-baseline-v1"

_FAST_PERIOD = 20
_SLOW_PERIOD = 50
_MOMENTUM_PERIOD = 20
_ENTRY_MINIMUM_SCORE = 80
_EXIT_MAXIMUM_SCORE = 60


def conservative_baseline_parameters() -> dict[str, Any]:
    """Return a fresh copy of the complete approved fixed parameter contract."""

    return {
        "fast_period": _FAST_PERIOD,
        "slow_period": _SLOW_PERIOD,
        "momentum_period": _MOMENTUM_PERIOD,
        "entry_minimum_score": _ENTRY_MINIMUM_SCORE,
        "exit_maximum_score": _EXIT_MAXIMUM_SCORE,
        "insufficient_history_action": ACTION_HOLD,
        "parameter_search_allowed": False,
    }


def _validate_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(parameters, Mapping):
        raise ValueError("strategy parameters must be a mapping")
    expected = conservative_baseline_parameters()
    resolved = dict(parameters)
    if set(resolved) != set(expected):
        raise ValueError("strategy parameters must contain exactly the fixed baseline fields")
    for name, expected_value in expected.items():
        value = resolved[name]
        if type(value) is not type(expected_value) or value != expected_value:
            raise ValueError("strategy parameters do not match the approved fixed baseline")
    return resolved


def _validate_history(
    symbol: str,
    history_through_signal_close: Sequence[MarketBar],
) -> tuple[MarketBar, ...]:
    resolved_symbol = str(symbol or "").strip().upper()
    if not resolved_symbol:
        raise ValueError("strategy symbol is required")
    rows = tuple(history_through_signal_close)
    for row in rows:
        if type(row) is not MarketBar:
            raise ValueError("strategy history must contain only exact MarketBar rows")
        if row.symbol != resolved_symbol:
            raise ValueError("strategy history must contain exactly one matching symbol")
        if row.close <= 0:
            raise ValueError("strategy history close values must remain positive")
    for left, right in zip(rows, rows[1:]):
        if (
            left.close_at >= right.open_at
            or left.available_at >= right.open_at
            or left.available_at > right.available_at
        ):
            raise ValueError("strategy history is not causally ordered")
    return rows


def _mean_close(rows: Sequence[MarketBar]) -> Decimal:
    return sum((row.close for row in rows), Decimal("0")) / Decimal(len(rows))


class ConservativeBaselineStrategy:
    """Approved fixed 20/50/20 baseline with no parameter search.

    Scores 80 or 90 request a long entry, scores 60 or below request an exit,
    and the neutral score of 70 holds.  Fewer than 50 completed observations
    also holds.  Repeated entry/exit actions are harmless because the engine
    owns position state and ignores inapplicable actions.
    """

    version = POLICY_VERSION

    @staticmethod
    def parameters() -> dict[str, Any]:
        return conservative_baseline_parameters()

    def score(
        self,
        symbol: str,
        history_through_signal_close: Sequence[MarketBar],
        parameters: Mapping[str, Any],
    ) -> int | None:
        resolved_parameters = _validate_parameters(parameters)
        rows = _validate_history(symbol, history_through_signal_close)
        fast_period = resolved_parameters["fast_period"]
        slow_period = resolved_parameters["slow_period"]
        momentum_period = resolved_parameters["momentum_period"]
        required = max(slow_period, momentum_period + 1)
        if len(rows) < required:
            return None

        fast_mean = _mean_close(rows[-fast_period:])
        slow_mean = _mean_close(rows[-slow_period:])
        momentum_reference = rows[-(momentum_period + 1)].close
        momentum_percent = (
            rows[-1].close / momentum_reference - Decimal("1")
        ) * Decimal("100")

        score = 50 + (20 if fast_mean > slow_mean else -20)
        if momentum_percent > Decimal("15"):
            score += 20
        elif momentum_percent > Decimal("5"):
            score += 10
        elif momentum_percent < Decimal("-15"):
            score -= 20
        elif momentum_percent < Decimal("-5"):
            score -= 10
        if score not in {10, 20, 30, 40, 50, 60, 70, 80, 90}:
            raise ValueError("strategy score is outside the fixed attainable set")
        return score

    def decide(
        self,
        symbol: str,
        history_through_signal_close: Sequence[MarketBar],
        parameters: Mapping[str, Any],
    ) -> str:
        resolved_parameters = _validate_parameters(parameters)
        score = self.score(symbol, history_through_signal_close, parameters)
        if score is None:
            return resolved_parameters["insufficient_history_action"]
        if score >= resolved_parameters["entry_minimum_score"]:
            return ACTION_ENTER_LONG
        if score <= resolved_parameters["exit_maximum_score"]:
            return ACTION_EXIT_LONG
        return ACTION_HOLD
