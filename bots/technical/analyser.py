"""Transparent multi-horizon technical context for research, not trade timing."""

from __future__ import annotations

import math
from typing import Any

from core.company_context import CompanyContext


class TechnicalAnalyser:
    """Score trend health across several horizons without inventing a signal.

    The output is a contextual research input.  It does not create a buy or
    sell instruction by itself and it reports ``LIMITED`` when there is not
    enough price history to calculate a meaningful view.
    """

    VERSION = "2.1-volume-support-resistance-fibonacci"

    @staticmethod
    def close_series(history: Any):
        return TechnicalAnalyser.numeric_series(history, "Close")

    @staticmethod
    def numeric_series(history: Any, column: str):
        if history is None or getattr(history, "empty", True) or column not in history:
            return None
        values = history[column]
        # yfinance can return a one-column frame when MultiIndex columns are
        # enabled.  The pipeline analyses one company at a time.
        if hasattr(values, "columns"):
            if values.empty:
                return None
            values = values.iloc[:, 0]
        try:
            values = values.dropna().astype(float)
        except (AttributeError, TypeError, ValueError):
            return None
        return values if len(values) else None

    @staticmethod
    def bounded(value: float) -> float:
        return round(max(0.0, min(100.0, value)), 2)

    @classmethod
    def return_for_days(cls, close, days: int) -> float | None:
        if close is None or len(close) <= days:
            return None
        starting = float(close.iloc[-(days + 1)])
        ending = float(close.iloc[-1])
        if starting <= 0:
            return None
        return ending / starting - 1.0

    @classmethod
    def return_score(cls, value: float, scale: float) -> float:
        # Smoothly maps a return to 0–100.  Unlike fixed buckets, a 12% return
        # is not indistinguishable from a 19% return.
        return cls.bounded(50.0 + 45.0 * math.tanh(value / scale))

    @classmethod
    def weighted_average(cls, values: list[tuple[float | None, float]]) -> float | None:
        present = [(value, weight) for value, weight in values if value is not None]
        if not present:
            return None
        total_weight = sum(weight for _, weight in present)
        return round(sum(value * weight for value, weight in present) / total_weight, 2)

    @classmethod
    def analyse(cls, context: CompanyContext):
        close = cls.close_series(context.history)
        if close is None or len(close) < 60:
            return {
                "Status": "LIMITED",
                "Reason": "At least 60 closing-price observations are required for technical context.",
                "Data Points": 0 if close is None else len(close),
                "Momentum": None,
                "Moving Average": None,
                "Drawdown": None,
                "Trend Persistence": None,
                "Technical Score": None,
            }

        current = float(close.iloc[-1])
        return_20 = cls.return_for_days(close, 20)
        return_60 = cls.return_for_days(close, 60)
        return_120 = cls.return_for_days(close, 120)
        return_252 = cls.return_for_days(close, 252)
        momentum_score = cls.weighted_average(
            [
                (cls.return_score(return_20, 0.06) if return_20 is not None else None, 0.15),
                (cls.return_score(return_60, 0.12) if return_60 is not None else None, 0.35),
                (cls.return_score(return_120, 0.20) if return_120 is not None else None, 0.30),
                (cls.return_score(return_252, 0.32) if return_252 is not None else None, 0.20),
            ]
        )

        ma_20 = float(close.tail(20).mean())
        ma_50 = float(close.tail(50).mean())
        ma_200 = float(close.tail(200).mean()) if len(close) >= 200 else None
        price_vs_50 = cls.return_score(current / ma_50 - 1.0, 0.06) if ma_50 > 0 else None
        price_vs_200 = (
            cls.return_score(current / ma_200 - 1.0, 0.14)
            if ma_200 is not None and ma_200 > 0
            else None
        )
        ma_trend = (
            cls.return_score(ma_50 / ma_200 - 1.0, 0.08)
            if ma_200 is not None and ma_200 > 0
            else None
        )
        moving_average_score = cls.weighted_average(
            [(price_vs_50, 0.40), (price_vs_200, 0.30), (ma_trend, 0.30)]
        )

        rolling_peak = float(close.tail(min(len(close), 252)).cummax().iloc[-1])
        drawdown = current / rolling_peak - 1.0 if rolling_peak > 0 else None
        drawdown_score = cls.bounded(100.0 + 320.0 * drawdown) if drawdown is not None else None
        recent_returns = close.pct_change().dropna().tail(60)
        trend_persistence = (
            cls.bounded(float((recent_returns > 0).mean()) * 100.0)
            if len(recent_returns) >= 20
            else None
        )

        # Support and resistance use only previous observations.  Including
        # today's price in the ceiling would make every new high appear to be
        # exactly at resistance and erase useful breakout context.
        previous_range = close.iloc[-61:-1] if len(close) >= 61 else close.iloc[:-1]
        support_level = float(previous_range.min()) if len(previous_range) else None
        resistance_level = float(previous_range.max()) if len(previous_range) else None
        support_resistance_score = None
        range_position = None
        if (
            support_level is not None
            and resistance_level is not None
            and resistance_level > support_level
        ):
            range_position = (current - support_level) / (resistance_level - support_level)
            if current > resistance_level:
                support_resistance_score = cls.bounded(75.0 + 15.0 * math.tanh((current / resistance_level - 1.0) / 0.04))
            elif current < support_level:
                support_resistance_score = cls.bounded(25.0 + 15.0 * math.tanh((current / support_level - 1.0) / 0.04))
            else:
                support_resistance_score = cls.bounded(35.0 + 30.0 * range_position)

        # Fibonacci levels are provided as a reproducible range map, not a
        # claim that those levels predict a reversal.  They are therefore
        # displayed for review but not used as a direct allocation trigger.
        fibonacci_window = close.tail(min(len(close), 252))
        swing_low = float(fibonacci_window.min())
        swing_high = float(fibonacci_window.max())
        fibonacci_levels = {}
        nearest_fibonacci_level = None
        fibonacci_distance = None
        if swing_high > swing_low:
            price_range = swing_high - swing_low
            fibonacci_levels = {
                f"{ratio:.1%}": round(swing_high - price_range * ratio, 4)
                for ratio in (0.236, 0.382, 0.500, 0.618, 0.786)
            }
            nearest_label, nearest_value = min(
                fibonacci_levels.items(),
                key=lambda item: abs(current - item[1]),
            )
            nearest_fibonacci_level = nearest_label
            fibonacci_distance = (current - nearest_value) / current if current else None

        volume = cls.numeric_series(context.history, "Volume")
        volume_score = None
        volume_ratio_20_to_60 = None
        if volume is not None and len(volume) >= 60 and return_20 is not None:
            average_20 = float(volume.tail(20).mean())
            average_60 = float(volume.tail(60).mean())
            if average_60 > 0:
                volume_ratio_20_to_60 = average_20 / average_60
                direction = 1.0 if return_20 >= 0 else -1.0
                volume_score = cls.bounded(
                    50.0 + direction * 25.0 * math.tanh((volume_ratio_20_to_60 - 1.0) / 0.30)
                )

        technical_score = cls.weighted_average(
            [
                (momentum_score, 0.35),
                (moving_average_score, 0.30),
                (drawdown_score, 0.20),
                (trend_persistence, 0.10),
                (volume_score, 0.05),
            ]
        )

        return {
            "Status": "COMPLETE",
            "Reason": "Multi-horizon momentum, trend, drawdown and persistence are combined as technical context.",
            "Data Points": len(close),
            "Momentum": momentum_score,
            "Moving Average": moving_average_score,
            "Drawdown": drawdown_score,
            "Trend Persistence": trend_persistence,
            "Support Resistance": support_resistance_score,
            "Volume Confirmation": volume_score,
            "Technical Score": technical_score,
            "Return 20d": round(return_20, 6) if return_20 is not None else None,
            "Return 60d": round(return_60, 6) if return_60 is not None else None,
            "Return 120d": round(return_120, 6) if return_120 is not None else None,
            "Return 252d": round(return_252, 6) if return_252 is not None else None,
            "Current Price": current,
            "Moving Average 20": round(ma_20, 4),
            "Moving Average 50": round(ma_50, 4),
            "Moving Average 200": round(ma_200, 4) if ma_200 is not None else None,
            "Drawdown From 252d High": round(drawdown, 6) if drawdown is not None else None,
            "Support Level": round(support_level, 4) if support_level is not None else None,
            "Resistance Level": round(resistance_level, 4) if resistance_level is not None else None,
            "Range Position": round(range_position, 6) if range_position is not None else None,
            "Volume Ratio 20d To 60d": round(volume_ratio_20_to_60, 4) if volume_ratio_20_to_60 is not None else None,
            "Fibonacci Window Low": round(swing_low, 4),
            "Fibonacci Window High": round(swing_high, 4),
            "Fibonacci Levels": fibonacci_levels,
            "Nearest Fibonacci Level": nearest_fibonacci_level,
            "Distance To Nearest Fibonacci Level": round(fibonacci_distance, 6) if fibonacci_distance is not None else None,
            "Fibonacci Note": "Fibonacci levels are descriptive range context only and are not a standalone predictive allocation signal.",
        }
