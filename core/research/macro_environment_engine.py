from __future__ import annotations

import csv
from io import StringIO
from typing import Any, Callable

import requests


class MacroEnvironmentEngine:
    """Read a small, transparent public macro snapshot from FRED.

    The output is context, not a trading prediction.  If any critical public
    series is unavailable the engine returns a limited result instead of
    manufacturing an economic view.
    """

    VERSION = "1.0"
    FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    SERIES = {
        "policy_rate": "FEDFUNDS",
        "inflation_index": "CPIAUCSL",
        "real_gdp": "GDPC1",
    }
    _cached_result: dict[str, Any] | None = None

    @staticmethod
    def number(value: Any) -> float | None:
        try:
            return None if value in (None, ".", "") else float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def parse_series(cls, text: str, series_id: str) -> list[float]:
        values = []
        for row in csv.DictReader(StringIO(text)):
            value = cls.number(row.get(series_id))
            if value is not None:
                values.append(value)
        return values

    @classmethod
    def derive(cls, series: dict[str, list[float]]) -> dict[str, Any]:
        policy_values = series.get("policy_rate") or []
        inflation_values = series.get("inflation_index") or []
        gdp_values = series.get("real_gdp") or []

        if not policy_values or len(inflation_values) < 13 or len(gdp_values) < 5:
            return {
                "status": "LIMITED",
                "regime": "UNAVAILABLE",
                "reason": "Required macro series are incomplete.",
            }

        policy_rate = policy_values[-1]
        inflation_yoy = inflation_values[-1] / inflation_values[-13] - 1
        gdp_yoy = gdp_values[-1] / gdp_values[-5] - 1

        if gdp_yoy < 0:
            regime = "CONTRACTIONARY"
        elif policy_rate >= 4.5 or inflation_yoy >= 0.035:
            regime = "RESTRICTIVE"
        else:
            regime = "SUPPORTIVE"

        return {
            "status": "COMPLETE",
            "regime": regime,
            "policy_rate": round(policy_rate, 3),
            "inflation_yoy": round(inflation_yoy, 6),
            "real_gdp_yoy": round(gdp_yoy, 6),
            "source": "FRED",
            "reason": "Classification uses public policy-rate, CPI and real-GDP data.",
        }

    def analyse(
        self,
        series_provider: Callable[[str], list[float]] | None = None,
    ) -> dict[str, Any]:
        live_provider = series_provider is None

        if live_provider and self.__class__._cached_result is not None:
            return dict(self.__class__._cached_result)

        try:
            if series_provider is None:
                def series_provider(name: str) -> list[float]:
                    series_id = self.SERIES[name]
                    response = requests.get(
                        self.FRED_URL.format(series_id=series_id),
                        timeout=20,
                    )
                    response.raise_for_status()
                    return self.parse_series(response.text, series_id)

            result = self.derive(
                {name: series_provider(name) for name in self.SERIES}
            )
        except Exception as exc:
            return {
                "status": "LIMITED",
                "regime": "UNAVAILABLE",
                "reason": f"Macro data could not be retrieved: {exc}",
            }

        if result.get("status") == "COMPLETE" and not live_provider:
            # Do not cache synthetic/injected values used by a test.
            return result
        if result.get("status") == "COMPLETE":
            self.__class__._cached_result = dict(result)
        return result
