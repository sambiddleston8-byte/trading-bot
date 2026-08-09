"""Transparent company-risk context for portfolio research."""

from __future__ import annotations

from typing import Any

from core.company_context import CompanyContext


class RiskAnalyser:
    VERSION = "2.0-market-and-balance-sheet-risk"

    @staticmethod
    def number(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def balance_value(cls, balance_sheet: Any, label: str) -> float | None:
        if balance_sheet is None or getattr(balance_sheet, "empty", True):
            return None
        if label not in balance_sheet.index:
            return None
        value = balance_sheet.loc[label]
        try:
            if hasattr(value, "iloc"):
                value = value.iloc[0]
            if hasattr(value, "iloc"):
                value = value.iloc[0]
        except (AttributeError, IndexError):
            return None
        return cls.number(value)

    @classmethod
    def close_series(cls, history: Any):
        if history is None or getattr(history, "empty", True) or "Close" not in history:
            return None
        close = history["Close"]
        if hasattr(close, "columns"):
            if close.empty:
                return None
            close = close.iloc[:, 0]
        try:
            close = close.dropna().astype(float)
        except (AttributeError, TypeError, ValueError):
            return None
        return close if len(close) else None

    @classmethod
    def analyse(cls, context: CompanyContext):
        info = context.info if isinstance(context.info, dict) else {}
        balance_sheet = context.balance_sheet
        close = cls.close_series(context.history)
        components: list[dict[str, Any]] = []
        score = 100.0

        beta = cls.number(info.get("beta"))
        if beta is not None:
            if beta > 2.0:
                score -= 25.0
                components.append({"factor": "beta", "impact": "HIGH", "reason": "Beta exceeds 2.0."})
            elif beta > 1.5:
                score -= 15.0
                components.append({"factor": "beta", "impact": "MEDIUM", "reason": "Beta exceeds 1.5."})
            elif beta > 1.0:
                score -= 5.0
                components.append({"factor": "beta", "impact": "LOW", "reason": "Beta exceeds 1.0."})

        debt = cls.balance_value(balance_sheet, "Total Debt")
        cash = cls.balance_value(balance_sheet, "Cash And Cash Equivalents")
        debt_to_cash = debt / cash if debt is not None and cash is not None and cash > 0 else None
        if debt is not None and cash is not None:
            if cash <= 0 < debt:
                score -= 18.0
                components.append({"factor": "debt_to_cash", "impact": "HIGH", "reason": "Debt is reported with no cash balance."})
            elif debt_to_cash is not None and debt_to_cash > 3.0:
                score -= 20.0
                components.append({"factor": "debt_to_cash", "impact": "HIGH", "reason": "Debt exceeds cash by more than three times."})
            elif debt_to_cash is not None and debt_to_cash > 2.0:
                score -= 14.0
                components.append({"factor": "debt_to_cash", "impact": "MEDIUM", "reason": "Debt exceeds cash by more than two times."})
            elif debt_to_cash is not None and debt_to_cash > 1.0:
                score -= 7.0
                components.append({"factor": "debt_to_cash", "impact": "LOW", "reason": "Debt exceeds cash."})

        annualised_volatility = None
        downside_volatility = None
        maximum_drawdown = None
        if close is not None and len(close) >= 30:
            returns = close.pct_change().dropna()
            annualised_volatility = float(returns.std() * (252 ** 0.5))
            negative_returns = returns[returns < 0]
            downside_volatility = (
                float(negative_returns.std() * (252 ** 0.5))
                if len(negative_returns) >= 10
                else None
            )
            rolling_peak = close.cummax()
            maximum_drawdown = float((close / rolling_peak - 1.0).min())

            if annualised_volatility > 0.60:
                score -= 25.0
                components.append({"factor": "volatility", "impact": "HIGH", "reason": "Annualised volatility exceeds 60%."})
            elif annualised_volatility > 0.40:
                score -= 15.0
                components.append({"factor": "volatility", "impact": "MEDIUM", "reason": "Annualised volatility exceeds 40%."})
            elif annualised_volatility > 0.25:
                score -= 5.0
                components.append({"factor": "volatility", "impact": "LOW", "reason": "Annualised volatility exceeds 25%."})

            if downside_volatility is not None and downside_volatility > 0.45:
                score -= 10.0
                components.append({"factor": "downside_volatility", "impact": "MEDIUM", "reason": "Downside volatility exceeds 45%."})
            if maximum_drawdown <= -0.40:
                score -= 15.0
                components.append({"factor": "maximum_drawdown", "impact": "HIGH", "reason": "Observed drawdown exceeds 40%."})
            elif maximum_drawdown <= -0.25:
                score -= 10.0
                components.append({"factor": "maximum_drawdown", "impact": "MEDIUM", "reason": "Observed drawdown exceeds 25%."})
            elif maximum_drawdown <= -0.15:
                score -= 5.0
                components.append({"factor": "maximum_drawdown", "impact": "LOW", "reason": "Observed drawdown exceeds 15%."})

        score = round(max(0.0, min(score, 100.0)), 2)
        return {
            "Risk Score": score,
            "Beta": beta,
            "Annualized Volatility": round(annualised_volatility, 4) if annualised_volatility is not None else None,
            "Downside Volatility": round(downside_volatility, 4) if downside_volatility is not None else None,
            "Maximum Drawdown": round(maximum_drawdown, 6) if maximum_drawdown is not None else None,
            "Debt to Cash": round(debt_to_cash, 4) if debt_to_cash is not None else None,
            "Risk Components": components,
            "Method": "BETA_BALANCE_SHEET_VOLATILITY_DOWNSIDE_AND_DRAWDOWN_CONTEXT",
        }
