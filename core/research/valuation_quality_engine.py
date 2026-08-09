from __future__ import annotations

"""Audit whether a DCF output is reliable enough for portfolio construction."""

from typing import Any


class ValuationQualityEngine:
    VERSION = "1.0-dcf-evidence-check"

    @staticmethod
    def mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def number(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def assess(cls, valuation: Any, decision: Any) -> dict[str, Any]:
        valuation_data = cls.mapping(valuation)
        decision_data = cls.mapping(decision)
        decision_valuation = cls.mapping(decision_data.get("valuation"))
        confidence = cls.mapping(decision_data.get("confidence"))
        forecast_validation = cls.mapping(valuation_data.get("Forecast Validation"))
        scenarios = cls.mapping(valuation_data.get("Scenarios"))
        base_scenario = cls.mapping(scenarios.get("Base"))

        current_price = cls.number(
            decision_valuation.get("current_price")
            if decision_valuation else valuation_data.get("Current Price")
        )
        intrinsic_value = cls.number(
            decision_valuation.get("base_intrinsic_value")
            if decision_valuation else cls.mapping(valuation_data.get("Intrinsic Value")).get("Base")
        )
        expected_return = cls.number(
            decision_valuation.get("expected_return")
            if decision_valuation else cls.mapping(valuation_data.get("Expected Return")).get("Base")
        )
        forecast_confidence = str(
            confidence.get("forecast")
            or forecast_validation.get("Overall Confidence")
            or "UNAVAILABLE"
        ).upper()
        terminal_value_contribution = cls.number(
            valuation_data.get("Terminal Value Contribution")
            or base_scenario.get("Terminal Value % of Enterprise Value")
        )

        failures: list[str] = []
        warnings: list[str] = []
        if current_price is None or current_price <= 0:
            failures.append("Current market price is unavailable.")
        if intrinsic_value is None or intrinsic_value <= 0:
            failures.append("Base intrinsic value is unavailable or invalid.")
        if expected_return is None:
            failures.append("Expected return is unavailable.")
        # Missing forecast evidence is a structural valuation failure.  REVIEW
        # and LOW are usable but uncertain inputs, so they affect conviction
        # and sizing rather than automatically removing a candidate.
        if forecast_confidence in {"INSUFFICIENT_DATA", "UNAVAILABLE"}:
            failures.append("Forecast inputs do not have sufficient independent validation.")
        elif forecast_confidence in {"LOW", "REVIEW"}:
            warnings.append("Forecast inputs have limited independent validation.")
        if terminal_value_contribution is not None and terminal_value_contribution > 0.85:
            warnings.append("More than 85% of enterprise value comes from terminal value.")
        elif terminal_value_contribution is not None and terminal_value_contribution > 0.75:
            warnings.append("More than 75% of enterprise value comes from terminal value.")

        assessment = "FAIL" if failures else ("REVIEW" if warnings else "PASS")
        score = 0.0 if failures else (70.0 if warnings else 100.0)
        return {
            "version": cls.VERSION,
            "status": "COMPLETE",
            "assessment": assessment,
            "score": score,
            "forecast_confidence": forecast_confidence,
            "terminal_value_contribution": terminal_value_contribution,
            "failures": failures,
            "warnings": warnings,
        }
