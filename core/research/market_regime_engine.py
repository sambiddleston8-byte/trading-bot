from __future__ import annotations

from typing import Any

import pandas as pd

from core.financial_data import FinancialDataEngine


class MarketRegimeEngine:
    """Classify the broad equity-market backdrop from benchmark price data."""

    VERSION = "1.0"
    BENCHMARK = "^GSPC"
    _cached_result: dict[str, Any] | None = None

    def __init__(self, financial_data: FinancialDataEngine | None = None):
        self.financial_data = financial_data or FinancialDataEngine()

    @staticmethod
    def number(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def classify(cls, history: pd.DataFrame | None) -> dict[str, Any]:
        if history is None or history.empty or "Close" not in history:
            return {
                "status": "LIMITED",
                "benchmark": cls.BENCHMARK,
                "regime": "UNAVAILABLE",
                "score": None,
                "reason": "Benchmark price history is unavailable.",
            }

        close = history["Close"].dropna()
        if len(close) < 200:
            return {
                "status": "LIMITED",
                "benchmark": cls.BENCHMARK,
                "regime": "UNAVAILABLE",
                "score": None,
                "reason": "At least 200 benchmark observations are required.",
            }

        current = float(close.iloc[-1])
        ma_50 = float(close.tail(50).mean())
        ma_200 = float(close.tail(200).mean())
        return_20 = (current / float(close.iloc[-21]) - 1) if len(close) >= 21 else 0.0
        return_60 = (current / float(close.iloc[-61]) - 1) if len(close) >= 61 else 0.0
        drawdown = current / float(close.cummax().iloc[-1]) - 1

        score = 50.0
        score += 15 if current >= ma_200 else -15
        score += 10 if ma_50 >= ma_200 else -10
        score += 10 if return_20 >= 0 else -10
        score += 5 if return_60 >= 0 else -5
        score += 5 if drawdown >= -0.05 else -10
        score = round(max(0.0, min(100.0, score)), 2)

        if score >= 70:
            regime = "RISK_ON"
        elif score <= 35:
            regime = "RISK_OFF"
        else:
            regime = "NEUTRAL"

        return {
            "status": "COMPLETE",
            "benchmark": cls.BENCHMARK,
            "regime": regime,
            "score": score,
            "current_price": current,
            "moving_average_50": ma_50,
            "moving_average_200": ma_200,
            "return_20d": round(return_20, 6),
            "return_60d": round(return_60, 6),
            "drawdown_from_high": round(drawdown, 6),
            "reason": "Benchmark trend, momentum and drawdown are combined into a market-regime classification.",
        }

    def analyse(self, history: pd.DataFrame | None = None) -> dict[str, Any]:
        if history is None and self.__class__._cached_result is not None:
            return dict(self.__class__._cached_result)

        if history is None:
            try:
                history = self.financial_data.get_price_history(
                    self.BENCHMARK,
                    period="1y",
                )
            except Exception as exc:
                return {
                    "status": "LIMITED",
                    "benchmark": self.BENCHMARK,
                    "regime": "UNAVAILABLE",
                    "score": None,
                    "reason": f"Benchmark data could not be retrieved: {exc}",
                }

        result = self.classify(history)
        if result.get("status") == "COMPLETE":
            self.__class__._cached_result = dict(result)
        return result
