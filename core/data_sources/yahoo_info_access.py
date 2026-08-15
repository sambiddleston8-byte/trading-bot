"""Fail-closed boundary for legacy yfinance ``Ticker.info`` profile reads."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import numbers
from types import MappingProxyType
from typing import Any, Callable, Mapping

from core.data_sources.provider_access import (
    ProviderAccessCoordinator,
    ProviderAccessError,
    ProviderAccessPolicy,
    ProviderAttemptMetadata,
)
from core.data_sources.yahoo_history_access import validate_yahoo_symbol


_MAXIMUM_TEXT_LENGTH = 200

# Only the fields the migrated readers actually consume are accepted: the
# research, multi-factor and competitor analysers, the portfolio pipeline
# reading through ``MultiFactorEngine.get_info``, the Yahoo source fetch and
# the valuation engine. Everything else in the provider mapping - including
# fields yfinance may add later - is discarded at this boundary and can never
# reach a caller.
#
# ``FinancialDataEngine.get_company_info`` is deliberately not migrated yet and
# still reads ``Ticker.info`` directly. It feeds the broad ``CompanyContext``,
# whose analysers read profile fields well outside this allowlist, so it needs
# its own separately inventoried batch. No field is admitted here for those
# deferred consumers.
TEXT_FIELDS: tuple[str, ...] = (
    "longName",
    "sector",
    "industry",
)

NUMERIC_FIELDS: tuple[str, ...] = (
    "beta",
    "currentPrice",
    "currentRatio",
    "debtToEquity",
    "dividendYield",
    "earningsGrowth",
    "earningsQuarterlyGrowth",
    "forwardPE",
    "freeCashflow",
    "marketCap",
    "operatingCashflow",
    "operatingMargins",
    "pegRatio",
    "priceToBook",
    "profitMargins",
    "quickRatio",
    # An unqualified late provider number, exactly like ``currentPrice``: it is
    # neither a tradeable quote nor an official prior close, and admitting it
    # here adds no authority to either field.
    "regularMarketPrice",
    "returnOnAssets",
    "returnOnEquity",
    "returnOnInvestedCapital",
    "revenueGrowth",
    "sharesOutstanding",
    "totalCash",
    "totalDebt",
    "trailingPE",
)

INFO_FIELD_ALLOWLIST: frozenset[str] = frozenset(TEXT_FIELDS + NUMERIC_FIELDS)


class YahooInfoAccessError(RuntimeError):
    """A stable failure that contains no SDK, request or response detail."""

    def __init__(self, message: str, *, reason_code: str, metadata: ProviderAttemptMetadata) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.metadata = metadata


@dataclass(frozen=True)
class YahooInfoObservation:
    """Validated allowlisted scalars and the truth about what they are not.

    Only rebuilt scalar values are retained: the provider mapping itself is
    dropped inside the boundary, so no unreviewed, nested or credential-shaped
    provider field can reach a caller through this observation.  The values are
    unqualified late provider numbers.  They are not official settlement
    prices, prior closes, tradeable quotes, account-bound values or replay
    evidence, and their presence proves no temporal completeness.  That applies
    without exception to the profile price fields ``currentPrice`` and
    ``regularMarketPrice``, which remain descriptive profile scalars.
    """

    fields: Mapping[str, Any]
    access: ProviderAttemptMetadata
    provider: str = field(default="YAHOO_FINANCE_VIA_YFINANCE", init=False)
    authenticated: bool = field(default=False, init=False)
    point_in_time: bool = field(default=False, init=False)
    survivorship_safe: bool = field(default=False, init=False)
    tradeable_quote: bool = field(default=False, init=False)
    official_settlement: bool = field(default=False, init=False)
    account_bound: bool = field(default=False, init=False)
    temporally_complete: bool = field(default=False, init=False)
    admissible_as_replay_evidence: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        # Enforce the same copy-and-validate boundary for direct construction,
        # so a mutable caller mapping cannot change an existing observation.
        selected = select_info_fields(self.fields)
        object.__setattr__(self, "fields", selected)


def validate_info_text(value: Any) -> str:
    """Return a bounded control-character-free string, or fail closed."""
    if not isinstance(value, str):
        raise ValueError("Yahoo info text field is invalid")
    resolved = value.strip()
    if not resolved or len(resolved) > _MAXIMUM_TEXT_LENGTH:
        raise ValueError("Yahoo info text field is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in resolved):
        raise ValueError("Yahoo info text field is invalid")
    return resolved


def validate_info_number(value: Any) -> int | float:
    """Return a finite number without losing integer precision.

    Booleans are rejected outright rather than counted as ``0``/``1``, and no
    string is ever coerced: a field the provider sent in an unexpected shape is
    treated as absent instead of being repaired into a plausible number.
    """
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError("Yahoo info numeric field is invalid")
    if isinstance(value, numbers.Integral):
        return int(value)
    try:
        resolved = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Yahoo info numeric field is invalid") from None
    if not math.isfinite(resolved):
        raise ValueError("Yahoo info numeric field is invalid")
    return resolved


_FIELD_VALIDATORS: tuple[tuple[str, Callable[[Any], Any]], ...] = tuple(
    [(name, validate_info_text) for name in TEXT_FIELDS]
    + [(name, validate_info_number) for name in NUMERIC_FIELDS]
)


def select_info_fields(payload: Any) -> Mapping[str, Any]:
    """Rebuild allowlisted scalars from one valid provider mapping.

    A non-mapping payload is a provider-protocol failure and must spend shared
    circuit credit. A valid mapping may legitimately be empty or partial; its
    missing or malformed optional fields are omitted without fabricated values
    and do not count as provider failures.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("Yahoo info response is invalid")
    selected: dict[str, Any] = {}
    for name, validate in _FIELD_VALIDATORS:
        try:
            value = payload[name]
        except KeyError:
            continue
        try:
            selected[name] = validate(value)
        except ValueError:
            continue
    return MappingProxyType(selected)


class YahooInfoClient:
    """Coordinate and validate one profile read without elevating it."""

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

    def info(self, symbol: Any) -> YahooInfoObservation:
        resolved_symbol = validate_yahoo_symbol(symbol)
        try:
            result = self.coordinator.call_once(
                # The payload is reduced to validated scalars inside the call,
                # so the provider mapping never leaves this boundary.
                lambda: select_info_fields(self.ticker_factory(resolved_symbol).info)
            )
        except ProviderAccessError as error:
            raise YahooInfoAccessError(
                "Yahoo company-profile read failed.",
                reason_code=error.reason_code,
                metadata=error.metadata,
            ) from None
        return YahooInfoObservation(fields=result.value, access=result.metadata)
