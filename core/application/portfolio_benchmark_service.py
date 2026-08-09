from __future__ import annotations

"""Paper-portfolio performance comparison against the S&P 500.

The service uses dated monitoring snapshots.  It deliberately reports that a
one-month comparison is unavailable until there is a real month of saved
paper-portfolio observations; it never turns a valuation estimate into a
performance claim.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class PortfolioBenchmarkService:
    VERSION = "1.1-paper-performance-history"
    BENCHMARK_NAME = "S&P 500 Index"
    BENCHMARK_TICKER = "^GSPC"
    PERFORMANCE_WINDOWS = ("1 month", "3 months", "6 months", "12 months")

    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    SNAPSHOT_DIRECTORY = PROJECT_ROOT / "data" / "research" / "portfolio_monitoring"

    @staticmethod
    def number(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def checked_at(snapshot: dict[str, Any]) -> datetime | None:
        value = snapshot.get("checked_at")
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    @classmethod
    def snapshots_for(cls, portfolio: dict[str, Any]) -> list[dict[str, Any]]:
        created_at = portfolio.get("created_at")
        snapshots = []
        for path in cls.SNAPSHOT_DIRECTORY.glob("portfolio_health_*.json"):
            try:
                snapshot = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                isinstance(snapshot, dict)
                and snapshot.get("portfolio_created_at") == created_at
                and cls.checked_at(snapshot) is not None
            ):
                snapshots.append(snapshot)
        return sorted(snapshots, key=lambda item: cls.checked_at(item) or datetime.min.replace(tzinfo=timezone.utc))

    @classmethod
    def portfolio_return(cls, snapshot: dict[str, Any]) -> float | None:
        total_weight = 0.0
        weighted_return = 0.0
        for position in snapshot.get("positions") or []:
            weight = cls.number(position.get("weight"))
            price_change = cls.number(position.get("price_change"))
            if weight is None or price_change is None or weight <= 0:
                continue
            total_weight += weight
            weighted_return += weight * price_change
        return weighted_return / total_weight if total_weight > 0 else None

    @classmethod
    def history(cls, portfolio: dict[str, Any]) -> list[dict[str, Any]]:
        snapshots = cls.snapshots_for(portfolio)
        baseline_price = None
        points = []
        for snapshot in snapshots:
            benchmark = snapshot.get("benchmark") or {}
            price = cls.number(benchmark.get("price"))
            if baseline_price is None and price is not None and price > 0:
                baseline_price = price
            portfolio_return = cls.portfolio_return(snapshot)
            if portfolio_return is None or baseline_price is None or price is None or price <= 0:
                continue
            checked_at = cls.checked_at(snapshot)
            points.append(
                {
                    "checked_at": checked_at.isoformat() if checked_at else None,
                    "portfolio_return": portfolio_return,
                    "sp500_return": (price / baseline_price) - 1.0,
                }
            )
        return points

    @classmethod
    def performance_comparison(cls, portfolio: dict[str, Any]) -> dict[str, Any]:
        history = cls.history(portfolio)
        if not history:
            return {
                "status": "NOT_ENOUGH_DATA",
                "reason": "No dated portfolio and S&P 500 price observations are available yet.",
                "windows": list(cls.PERFORMANCE_WINDOWS),
                "history": [],
            }

        latest = history[-1]
        latest_at = datetime.fromisoformat(str(latest["checked_at"]).replace("Z", "+00:00"))
        month_start = latest_at - timedelta(days=30)
        eligible_baselines = [
            point for point in history
            if datetime.fromisoformat(str(point["checked_at"]).replace("Z", "+00:00")) <= month_start
        ]
        if not eligible_baselines:
            return {
                "status": "NOT_ENOUGH_DATA",
                "reason": "A one-month comparison needs a dated portfolio health check at least 30 days before the latest check.",
                "windows": list(cls.PERFORMANCE_WINDOWS),
                "history": history,
            }

        baseline = eligible_baselines[-1]
        portfolio_return = (1.0 + latest["portfolio_return"]) / (1.0 + baseline["portfolio_return"]) - 1.0
        sp500_return = (1.0 + latest["sp500_return"]) / (1.0 + baseline["sp500_return"]) - 1.0
        return {
            "status": "COMPLETE",
            "reason": "One-month performance is calculated from dated paper-portfolio health checks, not from valuation estimates.",
            "windows": list(cls.PERFORMANCE_WINDOWS),
            "one_month": {
                "portfolio_return": portfolio_return,
                "sp500_return": sp500_return,
                "relative_return": portfolio_return - sp500_return,
                "from": baseline["checked_at"],
                "to": latest["checked_at"],
            },
            "history": history,
        }

    @classmethod
    def disclosure(cls, portfolio: dict[str, Any] | None) -> dict[str, Any]:
        portfolio = portfolio if isinstance(portfolio, dict) else {}
        forecast_years = portfolio.get("valuation_horizon_years")

        return {
            "version": cls.VERSION,
            "benchmark": {
                "name": cls.BENCHMARK_NAME,
                "ticker": cls.BENCHMARK_TICKER,
                "purpose": (
                    "The S&P 500 is the reference index for assessing the "
                    "prototype's future paper-portfolio performance."
                ),
            },
            "performance_comparison": cls.performance_comparison(portfolio),
            "valuation_time_horizon": {
                "forecast_years": forecast_years,
                "label": (
                    f"{int(forecast_years)}-year DCF forecast period"
                    if isinstance(forecast_years, (int, float))
                    else "DCF forecast period varies or is unavailable"
                ),
                "disclosure": (
                    "The valuation gap is model-implied over the stated DCF forecast "
                    "period. It is not a guaranteed return by that date."
                ),
            },
        }
