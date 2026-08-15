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

# ``longBusinessSummary`` is legitimately a paragraph rather than a label, so a
# 200-character cap would drop every real value. It gets its own bound instead
# of relaxing the shared one; it is still bounded, still strictly rejected when
# it contains an ASCII control character, and still never unlimited.
_MAXIMUM_SUMMARY_LENGTH = 4_000

# Only the fields the migrated readers actually consume are accepted: the
# research, multi-factor and competitor analysers, the portfolio pipeline
# reading through ``MultiFactorEngine.get_info``, the Yahoo source fetch, the
# valuation engine and the ``FinancialDataEngine.get_company_info`` aggregator
# that feeds ``CompanyContext``. Everything else in the provider mapping -
# including fields yfinance may add later - is discarded at this boundary and
# can never reach a caller.
#
# One field the aggregator's consumers read is deliberately NOT admitted:
# ``companyOfficers``. It is a nested provider list of officer objects, and the
# only consumer (``bots/profile/analyser.py``) assigns it whole to a ``"CEO"``
# key without selecting an officer, a name or a title. No smaller deterministic
# sanitized scalar contract can be derived from that read, so the field is
# omitted rather than passed through raw or reduced by a guessed rule. The
# consumer's ``"CEO"`` key therefore remains present with no value; inventing a
# name here would be fabricated data.
TEXT_FIELDS: tuple[str, ...] = (
    "city",
    "country",
    "currency",
    "exchange",
    "industry",
    "longName",
    "quoteType",
    "recommendationKey",
    "sector",
    "shortName",
    # Descriptive profile text only. This boundary never dereferences, resolves
    # or requests it, and admitting it grants no such permission downstream.
    "website",
)

SUMMARY_FIELDS: tuple[str, ...] = ("longBusinessSummary",)

NUMERIC_FIELDS: tuple[str, ...] = (
    "beta",
    "currentPrice",
    "currentRatio",
    "debtToEquity",
    "dividendYield",
    "earningsGrowth",
    "earningsQuarterlyGrowth",
    "ebitda",
    "enterpriseValue",
    "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow",
    "floatShares",
    "forwardPE",
    "freeCashflow",
    "fullTimeEmployees",
    "grossMargins",
    "heldPercentInsiders",
    "heldPercentInstitutions",
    "marketCap",
    "netIncomeToCommon",
    "operatingCashflow",
    "operatingMargins",
    "pegRatio",
    # An unqualified late Yahoo profile scalar, exactly like ``currentPrice``
    # and ``regularMarketPrice``. It is NOT an official prior close, NOT
    # settlement evidence, NOT account-bound and NOT admissible as replay
    # evidence. Admitting it here grants it no authority whatsoever, and the
    # Phase 4 previous-close resolution remains unaffected by its presence.
    "previousClose",
    "priceToBook",
    "priceToSalesTrailing12Months",
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
    "totalRevenue",
    "trailingPE",
)

INFO_FIELD_ALLOWLIST: frozenset[str] = frozenset(
    TEXT_FIELDS + SUMMARY_FIELDS + NUMERIC_FIELDS
)


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
    without exception to the profile price fields ``currentPrice``,
    ``regularMarketPrice`` and ``previousClose``, which all remain descriptive
    profile scalars.  In particular ``previousClose`` is an unqualified late
    Yahoo scalar and is not the official prior close, not settlement evidence
    and not account-bound.  Text fields, including ``website``, are descriptive
    values only: nothing here is ever dereferenced or requested.
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


def _bounded_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError("Yahoo info text field is invalid")
    resolved = value.strip()
    if not resolved or len(resolved) > limit:
        raise ValueError("Yahoo info text field is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in resolved):
        raise ValueError("Yahoo info text field is invalid")
    return resolved


def validate_info_text(value: Any) -> str:
    """Return bounded text without ASCII control characters, or fail closed."""
    return _bounded_text(value, _MAXIMUM_TEXT_LENGTH)


def validate_info_summary(value: Any) -> str:
    """Return a bounded business summary without ASCII control characters.

    Same strictness as any other text field - a non-string, an empty string or
    any ASCII control character is rejected outright - with only the length bound
    widened, because a business summary is a paragraph rather than a label.
    An over-long value is dropped, never truncated into a shortened paragraph
    that the provider never sent.
    """
    return _bounded_text(value, _MAXIMUM_SUMMARY_LENGTH)


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
    + [(name, validate_info_summary) for name in SUMMARY_FIELDS]
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
