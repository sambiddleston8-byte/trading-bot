from __future__ import annotations

"""Portfolio-level correlation and liquidity review for paper monitoring.

This is a portfolio risk diagnostic, not a trading signal. It makes overlap
visible so diversification is not judged only by the count of companies or
their broad sector labels.
"""

from typing import Any, Callable

from core.data_sources.yahoo_history_access import YahooHistoryClient


class PortfolioMarketExposureService:
    MIN_RETURN_OBSERVATIONS = 60
    HIGH_CORRELATION = 0.80
    LOW_LIQUIDITY_DOLLAR_VOLUME = 2_000_000.0

    @staticmethod
    def number(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def history(ticker: str, *, history_client: Any = None):
        try:
            client = history_client or YahooHistoryClient()

            return client.history(str(ticker).upper(), period="1y", auto_adjust=False).frame
        except Exception:
            return None

    @staticmethod
    def column(history: Any, name: str):
        if history is None or getattr(history, "empty", True) or name not in history:
            return None
        values = history[name]
        if hasattr(values, "columns"):
            if values.empty:
                return None
            values = values.iloc[:, 0]
        try:
            values = values.dropna().astype(float)
        except (AttributeError, TypeError, ValueError):
            return None
        return values if len(values) else None

    @classmethod
    def review(
        cls,
        portfolio: dict[str, Any],
        *,
        history_lookup: Callable[[str], Any] | None = None,
        history_client: Any = None,
    ) -> dict[str, Any]:
        """Measure observable liquidity and price-history overlap.

        Missing market history yields a visible LIMITED result rather than a
        fabricated neutral correlation. Results are advisory controls; the
        master research and hard portfolio constraints remain separate.
        """
        try:
            import pandas as pd
        except ImportError:
            return {
                "status": "UNAVAILABLE",
                "reason": "The market-exposure calculation requires pandas.",
                "risk_alerts": [],
            }

        holdings = [
            holding for holding in portfolio.get("holdings") or []
            if isinstance(holding, dict) and holding.get("ticker")
        ]
        history_lookup = history_lookup or (
            lambda ticker: cls.history(ticker, history_client=history_client)
        )
        prices: dict[str, Any] = {}
        liquidity: list[dict[str, Any]] = []
        industry_weights: dict[str, float] = {}
        sector_weights: dict[str, float] = {}

        for holding in holdings:
            ticker = str(holding.get("ticker") or "").upper()
            weight = cls.number(holding.get("weight")) or 0.0
            sector = str(holding.get("sector") or "Unknown")
            industry = str(holding.get("industry") or "Unknown")
            sector_weights[sector] = round(sector_weights.get(sector, 0.0) + weight, 6)
            industry_weights[industry] = round(industry_weights.get(industry, 0.0) + weight, 6)
            history = history_lookup(ticker)
            close = cls.column(history, "Close")
            volume = cls.column(history, "Volume")
            if close is not None:
                prices[ticker] = close
            dollar_volume = None
            if close is not None and volume is not None:
                common = min(len(close), len(volume))
                if common:
                    dollar_volume = float((close.tail(common) * volume.tail(common)).tail(20).mean())
            liquidity.append(
                {
                    "ticker": ticker,
                    "company": holding.get("name") or ticker,
                    "weight": weight,
                    "average_daily_dollar_volume_20d": (
                        round(dollar_volume, 2) if dollar_volume is not None else None
                    ),
                    "status": (
                        "LOW" if dollar_volume is not None and dollar_volume < cls.LOW_LIQUIDITY_DOLLAR_VOLUME
                        else "COMPLETE" if dollar_volume is not None
                        else "UNAVAILABLE"
                    ),
                }
            )

        pairwise: list[dict[str, Any]] = []
        observation_count = 0
        if len(prices) >= 2:
            frame = pd.DataFrame(prices).dropna(how="all")
            returns = frame.pct_change().dropna(how="all")
            observation_count = len(returns)
            if observation_count >= cls.MIN_RETURN_OBSERVATIONS:
                correlation = returns.corr(min_periods=cls.MIN_RETURN_OBSERVATIONS)
                tickers = sorted(prices)
                for index, first in enumerate(tickers):
                    for second in tickers[index + 1:]:
                        value = cls.number(correlation.loc[first, second])
                        if value is None:
                            continue
                        pairwise.append(
                            {
                                "first_ticker": first,
                                "second_ticker": second,
                                "correlation": round(value, 3),
                            }
                        )
        pairwise.sort(key=lambda item: abs(item["correlation"]), reverse=True)

        risk_alerts = []
        for pair in pairwise:
            if pair["correlation"] >= cls.HIGH_CORRELATION:
                risk_alerts.append(
                    f"{pair['first_ticker']} and {pair['second_ticker']} have high observed price-history correlation ({pair['correlation']:.2f})."
                )
        for item in liquidity:
            if item["status"] == "LOW":
                risk_alerts.append(
                    f"{item['ticker']} has low observed 20-day average dollar volume for the current position size."
                )

        weights = [cls.number(holding.get("weight")) or 0.0 for holding in holdings]
        herfindahl = sum(weight * weight for weight in weights)
        effective_positions = 1.0 / herfindahl if herfindahl > 0 else None
        status = "COMPLETE" if observation_count >= cls.MIN_RETURN_OBSERVATIONS else "LIMITED"
        reason = (
            "Correlation uses overlapping daily returns from roughly one year of observed price history."
            if status == "COMPLETE"
            else "Insufficient overlapping market history for a reliable correlation calculation."
        )
        return {
            "status": status,
            "reason": reason,
            "return_observation_count": observation_count,
            "covered_holdings": len(prices),
            "total_holdings": len(holdings),
            "effective_position_count": round(effective_positions, 2) if effective_positions is not None else None,
            "sector_weights": sector_weights,
            "industry_weights": industry_weights,
            "highest_correlated_pairs": pairwise[:10],
            "liquidity": liquidity,
            "risk_alerts": risk_alerts,
            "method": "OBSERVED_RETURN_CORRELATION_AND_20_DAY_DOLLAR_VOLUME",
        }
