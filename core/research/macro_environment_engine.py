from __future__ import annotations

import csv
import math
from datetime import date
from io import StringIO
from typing import Any, Callable

from core.data_sources.public_read_access import (
    FRED_GRAPH_ENDPOINT,
    PublicReadError,
    PublicTextClient,
)


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

    def __init__(self, public_client: PublicTextClient | None = None) -> None:
        self._public_client_was_injected = public_client is not None
        self.public_client = public_client or PublicTextClient(FRED_GRAPH_ENDPOINT)

    @staticmethod
    def number(value: Any) -> float | None:
        try:
            if value in (None, ".", ""):
                return None
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def parse_series(cls, text: str, series_id: str) -> list[float]:
        reader = csv.DictReader(StringIO(text))
        date_fields = [field for field in (reader.fieldnames or []) if field != series_id]
        if len(date_fields) != 1 or series_id not in (reader.fieldnames or []):
            return []
        observations: list[tuple[date, float]] = []
        for row in reader:
            value = cls.number(row.get(series_id))
            try:
                observed = date.fromisoformat(str(row.get(date_fields[0], "")))
            except ValueError:
                return []
            if value is not None:
                observations.append((observed, value))
        observations.sort(key=lambda item: item[0])

        intervals = {"CPIAUCSL": (13, 1), "GDPC1": (5, 3)}
        if series_id in intervals:
            count, months = intervals[series_id]
            window = observations[-count:]
            if len(window) != count:
                return []
            for (earlier, _), (later, _) in zip(window, window[1:]):
                expected_month = earlier.month + months
                expected_year = earlier.year + (expected_month - 1) // 12
                expected_month = (expected_month - 1) % 12 + 1
                if (later.year, later.month) != (expected_year, expected_month):
                    return []
        return [value for _, value in observations]

    @classmethod
    def derive(cls, series: dict[str, list[float]]) -> dict[str, Any]:
        policy_values = series.get("policy_rate") or []
        inflation_values = series.get("inflation_index") or []
        gdp_values = series.get("real_gdp") or []

        relevant = [
            *(policy_values[-1:] or []),
            *(inflation_values[-13:-12] or []),
            *(inflation_values[-1:] or []),
            *(gdp_values[-5:-4] or []),
            *(gdp_values[-1:] or []),
        ]
        if (
            not policy_values
            or len(inflation_values) < 13
            or len(gdp_values) < 5
            or len(relevant) != 5
            or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in relevant)
            or inflation_values[-13] <= 0
            or gdp_values[-5] <= 0
        ):
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
        live_provider = series_provider is None and not self._public_client_was_injected

        if live_provider and self.__class__._cached_result is not None:
            return dict(self.__class__._cached_result)

        try:
            if series_provider is None:
                def series_provider(name: str) -> list[float]:
                    series_id = self.SERIES[name]
                    text = self.public_client.get_text(
                        self.FRED_URL.split("?", 1)[0],
                        params={"id": series_id},
                        accept="text/csv,text/plain",
                    )
                    return self.parse_series(text, series_id)

            result = self.derive(
                {name: series_provider(name) for name in self.SERIES}
            )
        except Exception:
            return {
                "status": "LIMITED",
                "regime": "UNAVAILABLE",
                "reason": "Macro data could not be retrieved safely.",
            }

        if result.get("status") == "COMPLETE" and not live_provider:
            # Do not cache synthetic/injected values used by a test.
            return result
        if result.get("status") == "COMPLETE":
            self.__class__._cached_result = dict(result)
        return result
