"""Fail-closed boundary for legacy yfinance historical-price reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
import re
from typing import Any, Callable

import pandas as pd

from core.data_sources.provider_access import (
    ProviderAccessCoordinator,
    ProviderAccessError,
    ProviderAccessPolicy,
    ProviderAttemptMetadata,
)


_SYMBOL = re.compile(r"[A-Za-z0-9.^=_-]{1,32}\Z")
_PERIODS = frozenset({"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"})


class YahooHistoryAccessError(RuntimeError):
    """A stable failure that contains no SDK, request or response detail."""

    def __init__(self, message: str, *, reason_code: str, metadata: ProviderAttemptMetadata) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.metadata = metadata


@dataclass(frozen=True)
class YahooHistoryObservation:
    frame: pd.DataFrame
    access: ProviderAttemptMetadata
    provider: str = "YAHOO_FINANCE_VIA_YFINANCE"
    point_in_time: bool = False
    survivorship_safe: bool = False
    admissible_as_replay_evidence: bool = False


def _symbol(value: Any) -> str:
    resolved = str(value).strip()
    if not _SYMBOL.fullmatch(resolved):
        raise ValueError("Yahoo symbol has an unsupported format")
    return resolved


def _date_bound(value: Any, name: str) -> Any:
    if value is None or isinstance(value, (date, datetime, pd.Timestamp)):
        return value
    if not isinstance(value, str):
        raise TypeError(f"Yahoo {name} must be a date-like value")
    resolved = value.strip()
    if not resolved or len(resolved) > 40 or any(ord(character) < 32 for character in resolved):
        raise ValueError(f"Yahoo {name} is invalid")
    try:
        parsed = pd.Timestamp(resolved)
    except (TypeError, ValueError):
        raise ValueError(f"Yahoo {name} is invalid") from None
    if pd.isna(parsed):
        raise ValueError(f"Yahoo {name} is invalid")
    return value


def validate_yahoo_history_frame(value: Any) -> pd.DataFrame:
    if not isinstance(value, pd.DataFrame):
        raise ValueError("Yahoo history response is invalid")
    frame = value.copy(deep=True)
    if frame.empty:
        return frame
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("Yahoo history response is invalid")
    if not frame.index.is_monotonic_increasing or not frame.index.is_unique:
        raise ValueError("Yahoo history response is invalid")
    if "Close" not in frame.columns:
        raise ValueError("Yahoo history response is invalid")
    try:
        closes = pd.to_numeric(frame["Close"], errors="raise")
    except (TypeError, ValueError):
        raise ValueError("Yahoo history response is invalid") from None
    if closes.isna().any() or any(not math.isfinite(float(value)) or float(value) <= 0 for value in closes):
        raise ValueError("Yahoo history response is invalid")
    return frame


class YahooHistoryClient:
    """Coordinate and validate legacy history reads without elevating evidence."""

    def __init__(
        self,
        *,
        ticker_factory: Callable[[str], Any] | None = None,
        coordinator: ProviderAccessCoordinator | None = None,
    ) -> None:
        if ticker_factory is None:
            import yfinance as yf

            ticker_factory = yf.Ticker
        if not callable(ticker_factory):
            raise TypeError("ticker_factory must be callable")
        self.ticker_factory = ticker_factory
        self.coordinator = coordinator or ProviderAccessCoordinator.for_provider(
            "YAHOO_FINANCE_VIA_YFINANCE",
            "Yahoo Finance via yfinance",
            policy=ProviderAccessPolicy(maximum_attempts=1),
        )

    def history(
        self,
        symbol: Any,
        *,
        period: str | None = None,
        start: Any = None,
        end: Any = None,
        auto_adjust: bool | None = None,
    ) -> YahooHistoryObservation:
        resolved_symbol = _symbol(symbol)
        if period is not None:
            if start is not None or end is not None:
                raise ValueError("Yahoo history accepts either period or date bounds")
            if not isinstance(period, str) or period not in _PERIODS:
                raise ValueError("Yahoo history period is unsupported")
        elif start is None:
            raise ValueError("Yahoo history requires a period or start date")
        start = _date_bound(start, "start")
        end = _date_bound(end, "end")
        if auto_adjust is not None and not isinstance(auto_adjust, bool):
            raise TypeError("auto_adjust must be a boolean or None")

        kwargs: dict[str, Any] = {}
        if period is not None:
            kwargs["period"] = period
        else:
            kwargs.update({"start": start, "end": end})
        if auto_adjust is not None:
            kwargs["auto_adjust"] = auto_adjust

        try:
            result = self.coordinator.call_once(
                lambda: validate_yahoo_history_frame(
                    self.ticker_factory(resolved_symbol).history(**kwargs)
                )
            )
        except ProviderAccessError as error:
            raise YahooHistoryAccessError(
                "Yahoo history read failed.",
                reason_code=error.reason_code,
                metadata=error.metadata,
            ) from None
        return YahooHistoryObservation(frame=result.value, access=result.metadata)
