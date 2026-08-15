"""Fail-closed boundary for legacy yfinance ``fast_info`` current-price reads."""

from __future__ import annotations

from dataclasses import dataclass
import math
import numbers
from typing import Any, Callable

from core.data_sources.provider_access import (
    ProviderAccessCoordinator,
    ProviderAccessError,
    ProviderAccessPolicy,
    ProviderAttemptMetadata,
)
from core.data_sources.yahoo_history_access import validate_yahoo_symbol


_UNAVAILABLE = object()


class YahooFastInfoAccessError(RuntimeError):
    """A stable failure that contains no SDK, request or response detail."""

    def __init__(self, message: str, *, reason_code: str, metadata: ProviderAttemptMetadata) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.metadata = metadata


@dataclass(frozen=True)
class YahooFastInfoObservation:
    """One validated scalar price and the truth about what it is not.

    Only the scalar and the access counters are retained.  The ``fast_info``
    payload itself is never stored or exposed, so no unreviewed provider field
    can reach a caller through this observation.
    """

    last_price: float
    access: ProviderAttemptMetadata
    provider: str = "YAHOO_FINANCE_VIA_YFINANCE"
    authenticated: bool = False
    point_in_time: bool = False
    survivorship_safe: bool = False
    tradeable_quote: bool = False
    admissible_as_replay_evidence: bool = False


def validate_fast_info_last_price(value: Any) -> float:
    """Return a positive finite float, or fail closed on anything else."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError("Yahoo fast_info last price is invalid")
    try:
        resolved = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Yahoo fast_info last price is invalid") from None
    if not math.isfinite(resolved) or resolved <= 0:
        raise ValueError("Yahoo fast_info last price is invalid")
    return resolved


def _last_price_of(payload: Any) -> Any:
    """Read ``last_price`` from either fast_info access shape, then validate.

    ``yfinance`` exposes ``fast_info`` as an object with a ``last_price``
    property that also supports mapping-style ``get``.  Attribute access is
    tried first so a plain mapping never has an unrelated ``get`` invoked, and
    any unexpected shape simply yields no value and fails validation.
    """
    value = getattr(payload, "last_price", None)
    if value is None:
        getter = getattr(payload, "get", None)
        if callable(getter):
            value = getter("last_price")
    try:
        return validate_fast_info_last_price(value)
    except ValueError:
        # A missing or unusable per-symbol price is not a provider failure.
        # Raising inside call_once would spend shared circuit credit and allow
        # routine universe screening to disable every Yahoo reader.
        return _UNAVAILABLE


class YahooFastInfoClient:
    """Coordinate and validate one current-price read without elevating it."""

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

    def last_price(self, symbol: Any) -> YahooFastInfoObservation:
        resolved_symbol = validate_yahoo_symbol(symbol)
        try:
            result = self.coordinator.call_once(
                lambda: _last_price_of(self.ticker_factory(resolved_symbol).fast_info)
            )
        except ProviderAccessError as error:
            raise YahooFastInfoAccessError(
                "Yahoo current-price read failed.",
                reason_code=error.reason_code,
                metadata=error.metadata,
            ) from None
        if result.value is _UNAVAILABLE:
            raise YahooFastInfoAccessError(
                "Yahoo current-price read failed.",
                reason_code="PRICE_UNAVAILABLE",
                metadata=result.metadata,
            )
        return YahooFastInfoObservation(last_price=result.value, access=result.metadata)
