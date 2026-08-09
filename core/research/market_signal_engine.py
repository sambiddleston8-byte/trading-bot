from __future__ import annotations

from typing import Any

from bots.risk.analyser import RiskAnalyser
from bots.technical.analyser import TechnicalAnalyser
from core.financial_data import FinancialDataEngine


class MarketSignalEngine:
    """Normalize transparent technical and risk specialist outputs."""

    VERSION = "2.1-volume-support-resistance-fibonacci"

    def __init__(
        self,
        financial_data: FinancialDataEngine | None = None,
        technical_analyser: TechnicalAnalyser | None = None,
        risk_analyser: RiskAnalyser | None = None,
    ):
        self.financial_data = financial_data or FinancialDataEngine()
        self.technical_analyser = technical_analyser or TechnicalAnalyser()
        self.risk_analyser = risk_analyser or RiskAnalyser()

    @staticmethod
    def number(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def score(cls, value: Any) -> float | None:
        value = cls.number(value)
        return None if value is None else max(0.0, min(100.0, value))

    def analyse(self, ticker: str, context=None) -> dict[str, Any]:
        context = context or self.financial_data.build_context(ticker)
        technical = self.technical_analyser.analyse(context)
        risk = self.risk_analyser.analyse(context)

        technical_status = str(technical.get("Status") or "COMPLETE").upper()
        return {
            "version": self.VERSION,
            "status": "COMPLETE" if technical_status == "COMPLETE" else "LIMITED",
            "ticker": str(ticker).upper(),
            "technical": {
                "status": technical_status,
                "reason": technical.get("Reason"),
                "data_points": self.number(technical.get("Data Points")),
                "momentum_score": self.score(technical.get("Momentum")),
                "moving_average_score": self.score(technical.get("Moving Average")),
                "drawdown_score": self.score(technical.get("Drawdown")),
                "trend_persistence_score": self.score(technical.get("Trend Persistence")),
                "support_resistance_score": self.score(technical.get("Support Resistance")),
                "volume_confirmation_score": self.score(technical.get("Volume Confirmation")),
                "score": self.score(technical.get("Technical Score")),
                "return_20d": self.number(technical.get("Return 20d")),
                "return_60d": self.number(technical.get("Return 60d")),
                "return_120d": self.number(technical.get("Return 120d")),
                "return_252d": self.number(technical.get("Return 252d")),
                "drawdown_from_252d_high": self.number(technical.get("Drawdown From 252d High")),
                "support_level": self.number(technical.get("Support Level")),
                "resistance_level": self.number(technical.get("Resistance Level")),
                "range_position": self.number(technical.get("Range Position")),
                "volume_ratio_20d_to_60d": self.number(technical.get("Volume Ratio 20d To 60d")),
                "fibonacci_window_low": self.number(technical.get("Fibonacci Window Low")),
                "fibonacci_window_high": self.number(technical.get("Fibonacci Window High")),
                "fibonacci_levels": technical.get("Fibonacci Levels") if isinstance(technical.get("Fibonacci Levels"), dict) else {},
                "nearest_fibonacci_level": technical.get("Nearest Fibonacci Level"),
                "distance_to_nearest_fibonacci_level": self.number(technical.get("Distance To Nearest Fibonacci Level")),
                "fibonacci_note": technical.get("Fibonacci Note"),
            },
            "risk": {
                "score": self.score(risk.get("Risk Score")),
                "beta": self.number(risk.get("Beta")),
                "annualised_volatility": self.number(
                    risk.get("Annualized Volatility")
                ),
                "downside_volatility": self.number(risk.get("Downside Volatility")),
                "maximum_drawdown": self.number(risk.get("Maximum Drawdown")),
                "debt_to_cash": self.number(risk.get("Debt to Cash")),
                "components": risk.get("Risk Components") if isinstance(risk.get("Risk Components"), list) else [],
                "method": risk.get("Method"),
            },
        }
