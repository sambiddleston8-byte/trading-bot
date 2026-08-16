from __future__ import annotations

"""Causal, long-only historical execution and validation primitives.

The engine is deliberately data- and strategy-neutral.  It will not run unless
an authenticated-evidence adapter supplies a point-in-time, survivorship-safe
data receipt and terminal outcomes for every instrument that becomes terminal.
It has no broker, network, credential, or order-submission capability.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Context, Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext
import copy
import hashlib
import inspect
import itertools
import json
import math
from pathlib import Path
import random
from statistics import median
from typing import Any, Iterable, Mapping, Protocol, Sequence


ZERO = Decimal("0")
ONE = Decimal("1")
BPS = Decimal("10000")
ATR_DECIMAL_CONTEXT = Context(
    prec=34, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999
)
ACTION_HOLD = "HOLD"
ACTION_ENTER_LONG = "ENTER_LONG"
ACTION_EXIT_LONG = "EXIT_LONG"
TERMINAL_TYPES = {"DELISTED", "BANKRUPT", "ACQUIRED", "MERGED"}
AUTHENTICATED_REPLAY_ROLES = {
    "CORPORATE_ACTIONS",
    "DELISTING_OUTCOMES",
    "MARKET_CALENDARS_AND_HALTS",
    "RAW_DAILY_SESSION_BARS",
    "TOTAL_RETURN_PRICES",
    "UNIVERSE_MEMBERSHIP",
}
ENGINE_POLICY_VERSION = "causal-single-instrument-guardrailed-backtest-v3"
_ATTESTATION_FACTORY_TOKEN = object()
_RESEARCH_EXEMPTION_FACTORY_TOKEN = object()


def _decimal(value: Any, name: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'non-negative'}")
    return result


def _signed_decimal(value: Any, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not result.is_finite() or result < -ONE:
        raise ValueError(f"{name} must be finite and cannot be below -100%")
    return result


def _time(value: Any, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    def default(item: Any) -> Any:
        if isinstance(item, Decimal):
            return format(item, "f")
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat()
        raise TypeError(f"unsupported canonical value: {type(item)!r}")

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=default,
        allow_nan=False,
    )


def canonical_strategy_parameters(parameters: Mapping[str, Any]) -> str:
    """Return the exact bounded representation used to identify a strategy run."""
    if not isinstance(parameters, Mapping):
        raise ValueError("strategy parameters must be a mapping")

    count = 0

    def validate(value: Any, depth: int = 0) -> None:
        nonlocal count
        count += 1
        if count > 1000 or depth > 10:
            raise ValueError("strategy parameters exceed the bounded structure")
        if isinstance(value, Mapping):
            if len(value) > 100 or any(
                not isinstance(key, str) or not key or len(key) > 200
                for key in value
            ):
                raise ValueError("strategy parameter keys are invalid or excessive")
            for child in value.values():
                validate(child, depth + 1)
        elif isinstance(value, (list, tuple)):
            if len(value) > 100:
                raise ValueError("strategy parameter sequence is excessive")
            for child in value:
                validate(child, depth + 1)
        elif isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError("strategy Decimal parameters must be finite")
        elif isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("strategy datetime parameters must be timezone-aware")
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("strategy float parameters must be finite")
        elif isinstance(value, str):
            if len(value) > 10000:
                raise ValueError("strategy text parameter is excessive")
        elif value is not None and not isinstance(value, (bool, int)):
            raise ValueError(f"unsupported strategy parameter value: {type(value)!r}")

    validate(parameters)
    payload = _canonical_json(parameters)
    if len(payload.encode("utf-8")) > 100_000:
        raise ValueError("canonical strategy parameters exceed 100000 bytes")
    return payload


def strategy_parameter_hash(parameters: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_strategy_parameters(parameters).encode("utf-8")).hexdigest()


def _strategy_implementation_identity(strategy: Any) -> tuple[str, str]:
    strategy_type = strategy.__class__
    entrypoint = f"{strategy_type.__module__}:{strategy_type.__qualname__}"
    source_name = inspect.getsourcefile(strategy_type)
    if not source_name:
        raise ValueError("strategy implementation must have an inspectable source file")
    source = Path(source_name).resolve()
    if not source.is_file() or source.is_symlink():
        raise ValueError("strategy implementation source must be a regular non-symlink file")
    return entrypoint, hashlib.sha256(source.read_bytes()).hexdigest()


@dataclass(frozen=True, init=False)
class ReplayDataAttestation:
    source_id: str
    source_content_sha256: str
    validation_receipt_sha256: str
    derivation_policy_version: str
    evidence_role_hashes: tuple[tuple[str, str], ...]

    def __init__(
        self,
        *,
        source_id: str,
        source_content_sha256: str,
        validation_receipt_sha256: str,
        derivation_policy_version: str,
        evidence_role_hashes: tuple[tuple[str, str], ...],
        _factory_token: object | None = None,
    ) -> None:
        if _factory_token is not _ATTESTATION_FACTORY_TOKEN:
            raise ValueError(
                "ReplayDataAttestation must be derived from authenticated replay artifacts"
            )
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "source_content_sha256", source_content_sha256)
        object.__setattr__(self, "validation_receipt_sha256", validation_receipt_sha256)
        object.__setattr__(self, "derivation_policy_version", derivation_policy_version)
        object.__setattr__(self, "evidence_role_hashes", evidence_role_hashes)
        self.validate()

    @classmethod
    def _from_authenticated_artifacts(
        cls,
        *,
        source_id: str,
        source_content_sha256: str,
        validation_receipt_sha256: str,
        derivation_policy_version: str,
        evidence_role_hashes: tuple[tuple[str, str], ...],
    ) -> ReplayDataAttestation:
        return cls(
            source_id=source_id,
            source_content_sha256=source_content_sha256,
            validation_receipt_sha256=validation_receipt_sha256,
            derivation_policy_version=derivation_policy_version,
            evidence_role_hashes=evidence_role_hashes,
            _factory_token=_ATTESTATION_FACTORY_TOKEN,
        )

    def validate(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id is required")
        for name in ("source_content_sha256", "validation_receipt_sha256"):
            digest = getattr(self, name).lower()
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError(f"{name} must be a SHA-256 digest")
        if not self.derivation_policy_version.strip():
            raise ValueError("derivation_policy_version is required")
        if (
            tuple(sorted(self.evidence_role_hashes)) != self.evidence_role_hashes
            or len({role for role, _ in self.evidence_role_hashes})
            != len(self.evidence_role_hashes)
            or {role for role, _ in self.evidence_role_hashes}
            != AUTHENTICATED_REPLAY_ROLES
        ):
            raise ValueError("authenticated evidence must pin every required replay role once")
        for _, digest in self.evidence_role_hashes:
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest.lower()
            ):
                raise ValueError("every authenticated replay role requires a SHA-256 pin")


@dataclass(frozen=True, init=False)
class ResearchExemptionDataAttestation:
    """Explicit human research assumption, never authenticated source evidence.

    This narrowly scoped type lets the mechanical engine exercise an explicitly
    exempt research dataset without forging ``ReplayDataAttestation``.  Its
    factory is intentionally private to the engine module; callers must use the
    public classmethod, which fixes every authority-bearing flag false.
    """

    source_id: str
    source_content_sha256: str
    validation_receipt_sha256: str
    derivation_policy_version: str
    evidence_role_hashes: tuple[tuple[str, str], ...]
    exemption_id: str
    exemption_record_sha256: str
    authenticated_replay_evidence: bool
    provider_evidence: bool
    performance_claim_allowed: bool
    broker_connection_allowed: bool
    orders_submitted: bool
    live_trading_enabled: bool

    def __init__(
        self,
        *,
        source_id: str,
        source_content_sha256: str,
        validation_receipt_sha256: str,
        derivation_policy_version: str,
        evidence_role_hashes: tuple[tuple[str, str], ...],
        exemption_id: str,
        exemption_record_sha256: str,
        _factory_token: object | None = None,
    ) -> None:
        if _factory_token is not _RESEARCH_EXEMPTION_FACTORY_TOKEN:
            raise ValueError(
                "ResearchExemptionDataAttestation must be issued by its explicit factory"
            )
        values = {
            "source_id": source_id,
            "source_content_sha256": source_content_sha256,
            "validation_receipt_sha256": validation_receipt_sha256,
            "derivation_policy_version": derivation_policy_version,
            "evidence_role_hashes": evidence_role_hashes,
            "exemption_id": exemption_id,
            "exemption_record_sha256": exemption_record_sha256,
            "authenticated_replay_evidence": False,
            "provider_evidence": False,
            "performance_claim_allowed": False,
            "broker_connection_allowed": False,
            "orders_submitted": False,
            "live_trading_enabled": False,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)
        self.validate()

    @classmethod
    def _from_explicit_research_exemption(
        cls,
        *,
        source_id: str,
        source_content_sha256: str,
        validation_receipt_sha256: str,
        derivation_policy_version: str,
        evidence_role_hashes: tuple[tuple[str, str], ...],
        exemption_id: str,
        exemption_record_sha256: str,
    ) -> ResearchExemptionDataAttestation:
        return cls(
            source_id=source_id,
            source_content_sha256=source_content_sha256,
            validation_receipt_sha256=validation_receipt_sha256,
            derivation_policy_version=derivation_policy_version,
            evidence_role_hashes=evidence_role_hashes,
            exemption_id=exemption_id,
            exemption_record_sha256=exemption_record_sha256,
            _factory_token=_RESEARCH_EXEMPTION_FACTORY_TOKEN,
        )

    def validate(self) -> None:
        if not self.source_id.startswith("RESEARCH_EXEMPTION:"):
            raise ValueError("research-exempt source_id must be explicitly labelled")
        for name in (
            "source_content_sha256",
            "validation_receipt_sha256",
            "exemption_record_sha256",
        ):
            digest = str(getattr(self, name)).lower()
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError(f"{name} must be a SHA-256 digest")
        if not self.derivation_policy_version.strip() or not self.exemption_id.strip():
            raise ValueError("research exemption identity is incomplete")
        if not self.evidence_role_hashes or tuple(sorted(self.evidence_role_hashes)) != self.evidence_role_hashes:
            raise ValueError("research-exempt role hashes must be nonempty and sorted")
        for role, digest in self.evidence_role_hashes:
            if not role.startswith("ASSUMED_") or len(digest) != 64 or any(
                c not in "0123456789abcdef" for c in digest.lower()
            ):
                raise ValueError("research-exempt roles must be explicit assumptions with SHA-256 pins")
        for name in (
            "authenticated_replay_evidence",
            "provider_evidence",
            "performance_claim_allowed",
            "broker_connection_allowed",
            "orders_submitted",
            "live_trading_enabled",
        ):
            if getattr(self, name) is not False:
                raise ValueError("research exemption cannot grant authority")


@dataclass(frozen=True)
class MarketBar:
    symbol: str
    open_at: datetime
    close_at: datetime
    available_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise ValueError("bar symbol is required")
        object.__setattr__(self, "symbol", symbol)
        for field_name in ("open_at", "close_at", "available_at"):
            object.__setattr__(self, field_name, _time(getattr(self, field_name), field_name))
        if not self.open_at < self.close_at or self.available_at < self.close_at:
            raise ValueError("bar cannot become available before its close")
        for field_name in ("open", "high", "low", "close"):
            object.__setattr__(
                self,
                field_name,
                _decimal(getattr(self, field_name), field_name, positive=True),
            )
        object.__setattr__(self, "volume", _decimal(self.volume, "volume"))
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC values are inconsistent")


@dataclass(frozen=True)
class UniverseEvent:
    symbol: str
    action: str
    effective_at: datetime
    available_at: datetime
    source_locator: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        if self.action not in {"ADD", "REMOVE"}:
            raise ValueError("universe action must be ADD or REMOVE")
        object.__setattr__(self, "effective_at", _time(self.effective_at, "effective_at"))
        object.__setattr__(self, "available_at", _time(self.available_at, "available_at"))
        if self.available_at > self.effective_at:
            raise ValueError("universe event must be public no later than it becomes effective")
        if not self.symbol or not self.source_locator.strip():
            raise ValueError("universe event identity and source are required")


@dataclass(frozen=True)
class TerminalOutcome:
    symbol: str
    terminal_type: str
    effective_at: datetime
    available_at: datetime
    recovery_per_share: Decimal
    cash_settled_at: datetime
    source_locator: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        if self.terminal_type not in TERMINAL_TYPES:
            raise ValueError("terminal_type is unsupported")
        object.__setattr__(self, "effective_at", _time(self.effective_at, "effective_at"))
        object.__setattr__(self, "available_at", _time(self.available_at, "available_at"))
        object.__setattr__(self, "cash_settled_at", _time(self.cash_settled_at, "cash_settled_at"))
        object.__setattr__(
            self,
            "recovery_per_share",
            _decimal(self.recovery_per_share, "recovery_per_share"),
        )
        if self.available_at > self.effective_at:
            raise ValueError("terminal outcome must be public no later than it becomes effective")
        if self.cash_settled_at < self.effective_at:
            raise ValueError("terminal cash cannot settle before the outcome is effective")
        if not self.symbol or not self.source_locator.strip():
            raise ValueError("terminal outcome identity and source are required")


@dataclass(frozen=True)
class CorporateAction:
    symbol: str
    action_type: str
    effective_at: datetime
    available_at: datetime
    source_locator: str
    split_ratio: Decimal = ONE
    cash_per_share: Decimal = ZERO
    cash_paid_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        if self.action_type not in {"SPLIT", "CASH_DIVIDEND"}:
            raise ValueError("unsupported corporate action")
        object.__setattr__(self, "effective_at", _time(self.effective_at, "effective_at"))
        object.__setattr__(self, "available_at", _time(self.available_at, "available_at"))
        object.__setattr__(
            self, "split_ratio", _decimal(self.split_ratio, "split_ratio", positive=True)
        )
        object.__setattr__(
            self, "cash_per_share", _decimal(self.cash_per_share, "cash_per_share")
        )
        if self.available_at > self.effective_at:
            raise ValueError("corporate action must be known no later than its effective time")
        if self.action_type == "SPLIT":
            if self.split_ratio == ONE or self.cash_per_share != ZERO or self.cash_paid_at is not None:
                raise ValueError("split requires only a non-unit split_ratio")
        else:
            if self.split_ratio != ONE or self.cash_per_share <= ZERO or self.cash_paid_at is None:
                raise ValueError("cash dividend requires cash_per_share and cash_paid_at")
            paid = _time(self.cash_paid_at, "cash_paid_at")
            if paid < self.effective_at:
                raise ValueError("cash dividend cannot be paid before its effective time")
            object.__setattr__(self, "cash_paid_at", paid)
        if not self.symbol or not self.source_locator.strip():
            raise ValueError("corporate action identity and source are required")


@dataclass(frozen=True)
class ExchangeFeeTier:
    prior_monthly_notional_below: Decimal | None
    variable_bps: Decimal
    minimum_fee: Decimal = ZERO

    def __post_init__(self) -> None:
        if self.prior_monthly_notional_below is not None:
            object.__setattr__(
                self,
                "prior_monthly_notional_below",
                _decimal(
                    self.prior_monthly_notional_below,
                    "prior_monthly_notional_below",
                    positive=True,
                ),
            )
        object.__setattr__(self, "variable_bps", _decimal(self.variable_bps, "variable_bps"))
        object.__setattr__(self, "minimum_fee", _decimal(self.minimum_fee, "minimum_fee"))


@dataclass(frozen=True)
class ExchangeFeeSchedule:
    schedule_id: str
    tiers: tuple[ExchangeFeeTier, ...]

    def __post_init__(self) -> None:
        if not self.schedule_id.strip() or not self.tiers:
            raise ValueError("an identified non-empty fee schedule is required")
        thresholds = [
            tier.prior_monthly_notional_below
            for tier in self.tiers
            if tier.prior_monthly_notional_below is not None
        ]
        if thresholds != sorted(thresholds) or self.tiers[-1].prior_monthly_notional_below is not None:
            raise ValueError("fee tiers must be ascending and end with an open tier")

    def fee(self, notional: Decimal, prior_monthly_notional: Decimal) -> Decimal:
        for tier in self.tiers:
            if (
                tier.prior_monthly_notional_below is None
                or prior_monthly_notional < tier.prior_monthly_notional_below
            ):
                return max(tier.minimum_fee, notional * tier.variable_bps / BPS)
        raise AssertionError("open fee tier is required")


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: Decimal
    max_equity_risk_per_trade: Decimal = Decimal("0.01")
    maximum_aggregate_open_risk: Decimal = Decimal("0.06")
    maximum_position_fraction: Decimal = Decimal("1")
    atr_window: int = 14
    atr_stop_multiple: Decimal = Decimal("2")
    baseline_slippage_bps: Decimal = Decimal("10")
    bid_ask_half_spread_bps: Decimal = Decimal("5")
    latency_adverse_bps: Decimal = Decimal("0")
    liquidity_impact_bps_at_max_participation: Decimal = Decimal("10")
    stop_pierce_fill_fraction: Decimal = Decimal("0.5")
    lagged_liquidity_lookback: int = 20
    maximum_lagged_volume_participation: Decimal = Decimal("0.02")
    allow_fractional_shares: bool = False
    cash_settlement_sessions: int = 1
    maximum_order_age_minutes: Decimal = Decimal("10080")
    execution_scenario: str = "BASE"

    def __post_init__(self) -> None:
        for name, positive in (
            ("initial_cash", True),
            ("max_equity_risk_per_trade", True),
            ("maximum_aggregate_open_risk", True),
            ("maximum_position_fraction", True),
            ("atr_stop_multiple", True),
            ("baseline_slippage_bps", True),
            ("bid_ask_half_spread_bps", True),
            ("latency_adverse_bps", False),
            ("liquidity_impact_bps_at_max_participation", True),
            ("maximum_lagged_volume_participation", True),
            ("stop_pierce_fill_fraction", False),
            ("maximum_order_age_minutes", True),
        ):
            object.__setattr__(self, name, _decimal(getattr(self, name), name, positive=positive))
        if self.max_equity_risk_per_trade > Decimal("0.01"):
            raise ValueError("max equity risk per trade cannot exceed 1%")
        if not self.max_equity_risk_per_trade <= self.maximum_aggregate_open_risk <= Decimal("0.10"):
            raise ValueError("aggregate open risk must be between per-trade risk and 10%")
        if self.maximum_position_fraction > ONE:
            raise ValueError("maximum position fraction cannot exceed 100% of equity")
        if self.baseline_slippage_bps < Decimal("10"):
            raise ValueError("baseline slippage cannot be below 0.10%")
        if not 2 <= self.atr_window or not 2 <= self.lagged_liquidity_lookback:
            raise ValueError("ATR and liquidity windows must be rolling windows of at least two bars")
        if not ZERO < self.maximum_lagged_volume_participation <= Decimal("0.10"):
            raise ValueError("lagged volume participation must be between 0 and 10%")
        if not ZERO <= self.stop_pierce_fill_fraction <= ONE:
            raise ValueError("stop pierce fill fraction must be between 0 and 1")
        if self.cash_settlement_sessions < 1:
            raise ValueError("cash settlement must take at least one market session")
        if self.execution_scenario not in {"BASE", "PESSIMISTIC"}:
            raise ValueError("execution_scenario must be BASE or PESSIMISTIC")
        if self.execution_scenario == "BASE" and self.stop_pierce_fill_fraction < Decimal("0.5"):
            raise ValueError("BASE stop-pierce fill must charge at least half the observed pierce")
        if self.execution_scenario == "PESSIMISTIC" and (
            self.stop_pierce_fill_fraction != ONE
            or self.baseline_slippage_bps < Decimal("20")
            or self.bid_ask_half_spread_bps < Decimal("10")
            or self.liquidity_impact_bps_at_max_participation < Decimal("20")
        ):
            raise ValueError("PESSIMISTIC execution requires doubled costs and full stop pierce")
        if self.allow_fractional_shares:
            raise ValueError("fractional shares are not supported by this conservative engine")


class CausalStrategy(Protocol):
    version: str

    def decide(
        self,
        symbol: str,
        history_through_signal_close: tuple[MarketBar, ...],
        parameters: Mapping[str, Any],
    ) -> str: ...


@dataclass
class _Position:
    quantity: Decimal
    average_entry_price: Decimal
    entry_total_cost: Decimal
    original_entry_total_cost: Decimal
    realized_exit_proceeds: Decimal
    stop_price: Decimal
    opened_at: datetime


@dataclass(frozen=True)
class ExecutionRecord:
    symbol: str
    action: str
    reason: str
    signal_at: datetime
    executed_at: datetime
    reference_price: Decimal
    execution_price: Decimal
    requested_quantity: Decimal
    filled_quantity: Decimal
    fee: Decimal
    status: str
    lagged_liquidity_notional: Decimal
    bid_ask_half_spread_bps: Decimal
    baseline_slippage_bps: Decimal
    latency_adverse_bps: Decimal
    liquidity_impact_bps: Decimal
    total_adverse_execution_bps: Decimal


@dataclass(frozen=True)
class CompletedTrade:
    symbol: str
    opened_at: datetime
    closed_at: datetime
    entry_total_cost: Decimal
    exit_net_proceeds: Decimal
    return_rate: Decimal
    exit_reason: str


@dataclass(frozen=True)
class SizingDecisionTrace:
    """Exact pre-fill limits used for one simulated order decision."""

    symbol: str
    action: str
    reason: str
    signal_at: datetime
    evaluated_at: datetime
    portfolio_equity_before: Decimal
    settled_cash_before: Decimal
    unsettled_cash_before: Decimal
    position_quantity_before: Decimal
    open_risk_before: Decimal
    risk_per_share: Decimal | None
    risk_budget: Decimal | None
    risk_quantity_limit: Decimal | None
    liquidity_notional: Decimal
    liquidity_quantity_limit: Decimal
    cash_quantity_limit: Decimal | None
    requested_quantity: Decimal
    filled_quantity: Decimal
    limiting_constraints: tuple[str, ...]
    stop_price_after: Decimal | None


@dataclass(frozen=True)
class PortfolioStateTrace:
    """Deterministic cash, position and mark state after a simulation event."""

    sequence: int
    as_of_at: datetime
    event_type: str
    symbol: str
    settled_cash: Decimal
    unsettled_cash: Decimal
    equity: Decimal
    position_quantity: Decimal
    average_entry_price: Decimal | None
    position_cost_basis: Decimal
    stop_price: Decimal | None
    mark_price: Decimal | None


@dataclass(frozen=True)
class BacktestResult:
    strategy_version: str
    parameter_hash: str
    source_id: str
    validation_receipt_sha256: str
    fee_schedule_id: str
    execution_scenario: str
    starting_equity: Decimal
    ending_equity: Decimal
    total_return: Decimal
    maximum_drawdown: Decimal
    executions: tuple[ExecutionRecord, ...]
    completed_trades: tuple[CompletedTrade, ...]
    equity_curve: tuple[tuple[datetime, Decimal], ...]
    sizing_decisions: tuple[SizingDecisionTrace, ...] = ()
    portfolio_states: tuple[PortfolioStateTrace, ...] = ()
    evaluation_start: datetime | None = None
    evaluation_end: datetime | None = None
    source_content_sha256: str = ""
    evidence_role_hashes: tuple[tuple[str, str], ...] = ()
    engine_policy_version: str = ENGINE_POLICY_VERSION
    engine_config_sha256: str = ""
    engine_config_canonical_json: str = ""
    strategy_entrypoint: str = ""
    strategy_source_sha256: str = ""
    no_lookahead_contract_enforced: bool = True
    mechanical_simulation_only: bool = True
    performance_claim_allowed: bool = False
    paper_trade_promotion_allowed: bool = False
    broker_connection_allowed: bool = False
    orders_submitted: bool = False
    live_trading_enabled: bool = False


def _atr(history: Sequence[MarketBar], window: int) -> Decimal | None:
    if len(history) < window + 1:
        return None
    with localcontext(ATR_DECIMAL_CONTEXT):
        true_ranges: list[Decimal] = []
        for prior, current in zip(history[-window - 1 : -1], history[-window:]):
            true_ranges.append(
                max(
                    current.high - current.low,
                    abs(current.high - prior.close),
                    abs(current.low - prior.close),
                )
            )
        return sum(true_ranges, ZERO) / Decimal(len(true_ranges))


def _lagged_liquidity(history: Sequence[MarketBar], lookback: int) -> Decimal | None:
    if len(history) < lookback:
        return None
    return median([bar.close * bar.volume for bar in history[-lookback:]])


def _adverse_price(reference: Decimal, side: str, slippage_bps: Decimal) -> Decimal:
    direction = ONE if side == "BUY" else -ONE
    result = reference * (ONE + direction * slippage_bps / BPS)
    if result <= ZERO:
        raise ValueError("adverse execution price is non-positive")
    return result


def _execution_cost_bps(
    config: BacktestConfig,
    *,
    reference_price: Decimal,
    filled_quantity: Decimal,
    lagged_liquidity_notional: Decimal,
) -> tuple[Decimal, Decimal]:
    participation = (
        filled_quantity * reference_price / lagged_liquidity_notional
        if lagged_liquidity_notional > ZERO
        else ZERO
    )
    normalized = min(ONE, participation / config.maximum_lagged_volume_participation)
    impact = config.liquidity_impact_bps_at_max_participation * normalized * normalized
    total = (
        config.bid_ask_half_spread_bps
        + config.baseline_slippage_bps
        + config.latency_adverse_bps
        + impact
    )
    return impact, total


def canonical_engine_configuration(
    config: BacktestConfig,
    fee_schedule: ExchangeFeeSchedule,
) -> str:
    return _canonical_json({
        "engine_policy_version": ENGINE_POLICY_VERSION,
        "config": config.__dict__,
        "fee_schedule": {
            "schedule_id": fee_schedule.schedule_id,
            "tiers": [tier.__dict__ for tier in fee_schedule.tiers],
        },
    })


def _drawdown(curve: Sequence[Decimal]) -> Decimal:
    peak = curve[0]
    worst = ZERO
    for value in curve:
        peak = max(peak, value)
        if peak > ZERO:
            worst = max(worst, ONE - value / peak)
    return worst


def _release_at_after_sessions(
    rows: Sequence[MarketBar], moment: datetime, sessions: int
) -> datetime:
    later_opens = sorted({bar.open_at for bar in rows if bar.open_at > moment})
    if len(later_opens) < sessions:
        raise ValueError("insufficient future market sessions to settle sale proceeds")
    return later_opens[sessions - 1]


class GuardrailedBacktestEngine:
    """Run causal daily-bar simulations with no external effects."""

    def __init__(
        self,
        *,
        config: BacktestConfig,
        fee_schedule: ExchangeFeeSchedule,
        data_attestation: ReplayDataAttestation | ResearchExemptionDataAttestation,
    ) -> None:
        if type(data_attestation) not in {
            ReplayDataAttestation,
            ResearchExemptionDataAttestation,
        }:
            raise ValueError("data_attestation has an unsupported authority type")
        data_attestation.validate()
        self.config = config
        self.fee_schedule = fee_schedule
        self.data_attestation = data_attestation

    def run(
        self,
        *,
        bars: Iterable[MarketBar],
        universe_events: Iterable[UniverseEvent],
        terminal_outcomes: Iterable[TerminalOutcome],
        corporate_actions: Iterable[CorporateAction],
        prices_are_unadjusted: bool,
        strategy: CausalStrategy,
        parameters: Mapping[str, Any],
        evaluation_start: datetime,
        evaluation_end: datetime,
    ) -> BacktestResult:
        start = _time(evaluation_start, "evaluation_start")
        end = _time(evaluation_end, "evaluation_end")
        if start >= end:
            raise ValueError("evaluation window is invalid")
        if not str(getattr(strategy, "version", "")).strip():
            raise ValueError("strategy version is required")
        try:
            strategy_instance = copy.deepcopy(strategy)
        except Exception as error:
            raise ValueError("strategy must be independently reproducible for every run") from error
        strategy_entrypoint, strategy_source_sha256 = _strategy_implementation_identity(
            strategy_instance
        )
        if not prices_are_unadjusted:
            raise ValueError(
                "back-adjusted prices embed future factors; supply raw point-in-time prices"
            )

        rows = sorted(tuple(bars), key=lambda item: (item.open_at, item.symbol))
        if not rows:
            raise ValueError("market bars are required")
        schedule_validator = getattr(
            strategy_instance, "validate_market_schedule", None
        )
        if schedule_validator is not None:
            if not callable(schedule_validator):
                raise ValueError("strategy market-schedule validator is invalid")
            schedule_validator(tuple(rows), self.data_attestation)
        by_symbol: dict[str, list[MarketBar]] = {}
        for row in rows:
            by_symbol.setdefault(row.symbol, []).append(row)
        if len(by_symbol) != 1:
            raise ValueError(
                "this bounded engine supports one instrument per run; "
                "portfolio-wide simultaneous order batching is not yet implemented"
            )
        for symbol, values in by_symbol.items():
            if any(left.close_at >= right.open_at for left, right in zip(values, values[1:])):
                raise ValueError(f"{symbol} bars overlap or are out of order")
            if any(left.available_at >= right.open_at for left, right in zip(values, values[1:])):
                raise ValueError(f"{symbol} bar was not available before the next session open")

        events = sorted(tuple(universe_events), key=lambda item: (item.effective_at, item.available_at, item.symbol))
        outcomes = sorted(tuple(terminal_outcomes), key=lambda item: (item.effective_at, item.symbol))
        actions = sorted(
            tuple(corporate_actions), key=lambda item: (item.effective_at, item.symbol, item.action_type)
        )
        if {row.symbol for row in rows} - {event.symbol for event in events if event.action == "ADD"}:
            raise ValueError("every instrument requires point-in-time universe entry evidence")
        if len({item.symbol for item in outcomes}) != len(outcomes):
            raise ValueError("each instrument may have at most one terminal outcome")
        if len({(item.symbol, item.effective_at, item.action_type) for item in actions}) != len(actions):
            raise ValueError("corporate actions must be unique")

        cash = self.config.initial_cash
        positions: dict[str, _Position] = {}
        histories: dict[str, list[MarketBar]] = {symbol: [] for symbol in by_symbol}
        pending: dict[str, tuple[str, str, datetime, Decimal | None]] = {}
        executions: list[ExecutionRecord] = []
        completed: list[CompletedTrade] = []
        equity_curve: list[tuple[datetime, Decimal]] = []
        sizing_decisions: list[SizingDecisionTrace] = []
        portfolio_states: list[PortfolioStateTrace] = []
        monthly_notional: dict[tuple[int, int], Decimal] = {}
        unsettled_cash: list[tuple[datetime, Decimal]] = []
        last_marks: dict[str, Decimal] = {}
        terminal_by_symbol = {item.symbol: item for item in outcomes}
        terminated: set[str] = set()
        applied_actions: set[tuple[str, datetime, str]] = set()
        dividend_entitlements: dict[tuple[str, datetime, str], Decimal] = {}
        paid_dividends: set[tuple[str, datetime, str]] = set()

        for symbol, values in by_symbol.items():
            if values[-1].close_at >= end:
                continue
            has_terminal = any(
                item.symbol == symbol and item.effective_at <= end for item in outcomes
            )
            has_removal = any(
                item.symbol == symbol and item.action == "REMOVE" and item.effective_at <= end
                for item in events
            )
            if not has_terminal and not has_removal:
                raise ValueError(
                    f"{symbol} history ends without an explicit removal or terminal outcome"
                )

        def eligible(symbol: str, moment: datetime) -> bool:
            known = [
                item
                for item in events
                if item.symbol == symbol
                and item.effective_at <= moment
                and item.available_at <= moment
            ]
            return bool(known) and known[-1].action == "ADD" and symbol not in terminated

        def equity() -> Decimal:
            return cash + sum(amount for _, amount in unsettled_cash) + sum(
                position.quantity * last_marks.get(symbol, position.average_entry_price)
                for symbol, position in positions.items()
            )

        def unsettled_total() -> Decimal:
            return sum((amount for _, amount in unsettled_cash), ZERO)

        def snapshot(event_type: str, moment: datetime, symbol: str) -> None:
            position = positions.get(symbol)
            portfolio_states.append(
                PortfolioStateTrace(
                    sequence=len(portfolio_states) + 1,
                    as_of_at=moment,
                    event_type=event_type,
                    symbol=symbol,
                    settled_cash=cash,
                    unsettled_cash=unsettled_total(),
                    equity=equity(),
                    position_quantity=position.quantity if position else ZERO,
                    average_entry_price=position.average_entry_price if position else None,
                    position_cost_basis=position.entry_total_cost if position else ZERO,
                    stop_price=position.stop_price if position else None,
                    mark_price=last_marks.get(symbol),
                )
            )

        def fee_for(notional: Decimal, moment: datetime) -> Decimal:
            key = (moment.year, moment.month)
            return self.fee_schedule.fee(notional, monthly_notional.get(key, ZERO))

        def record_notional(notional: Decimal, moment: datetime) -> None:
            key = (moment.year, moment.month)
            monthly_notional[key] = monthly_notional.get(key, ZERO) + notional

        def affordable_quantity(price: Decimal, maximum: Decimal, moment: datetime) -> Decimal:
            quantity = maximum.to_integral_value(rounding=ROUND_FLOOR)
            while quantity > ZERO:
                notional = quantity * price
                if notional + fee_for(notional, moment) <= cash:
                    return quantity
                quantity -= ONE
            return ZERO

        def record_unfilled_exit(
            *, symbol: str, requested: Decimal, reference: Decimal,
            moment: datetime, signal_at: datetime, reason: str, liquidity: Decimal,
            constraint: str,
        ) -> None:
            position = positions[symbol]
            impact_bps, total_cost_bps = _execution_cost_bps(
                self.config,
                reference_price=reference,
                filled_quantity=ZERO,
                lagged_liquidity_notional=liquidity,
            )
            executions.append(
                ExecutionRecord(
                    symbol, "SELL", reason, signal_at, moment, reference,
                    _adverse_price(reference, "SELL", total_cost_bps),
                    requested, ZERO, ZERO, "REJECTED", liquidity,
                    self.config.bid_ask_half_spread_bps,
                    self.config.baseline_slippage_bps,
                    self.config.latency_adverse_bps,
                    impact_bps, total_cost_bps,
                )
            )
            sizing_decisions.append(
                SizingDecisionTrace(
                    symbol=symbol, action="SELL", reason=reason,
                    signal_at=signal_at, evaluated_at=moment,
                    portfolio_equity_before=equity(), settled_cash_before=cash,
                    unsettled_cash_before=unsettled_total(),
                    position_quantity_before=position.quantity,
                    open_risk_before=max(
                        ZERO, position.average_entry_price - position.stop_price
                    ) * position.quantity,
                    risk_per_share=None, risk_budget=None,
                    risk_quantity_limit=None, liquidity_notional=liquidity,
                    liquidity_quantity_limit=ZERO, cash_quantity_limit=None,
                    requested_quantity=requested, filled_quantity=ZERO,
                    limiting_constraints=(constraint,),
                    stop_price_after=position.stop_price,
                )
            )

        def record_unfilled_entry(
            *, symbol: str, reference: Decimal, moment: datetime,
            signal_at: datetime, reason: str, liquidity: Decimal,
            portfolio_equity: Decimal, settled_before: Decimal,
            unsettled_before: Decimal, open_risk: Decimal,
            risk_per_share: Decimal, risk_budget: Decimal | None,
            constraint: str,
        ) -> None:
            impact_bps, total_cost_bps = _execution_cost_bps(
                self.config, reference_price=reference,
                filled_quantity=ZERO, lagged_liquidity_notional=liquidity,
            )
            executions.append(
                ExecutionRecord(
                    symbol, "BUY", reason, signal_at, moment, reference,
                    _adverse_price(reference, "BUY", total_cost_bps),
                    ZERO, ZERO, ZERO, "REJECTED", liquidity,
                    self.config.bid_ask_half_spread_bps,
                    self.config.baseline_slippage_bps,
                    self.config.latency_adverse_bps,
                    impact_bps, total_cost_bps,
                )
            )
            sizing_decisions.append(
                SizingDecisionTrace(
                    symbol=symbol, action="BUY", reason=reason,
                    signal_at=signal_at, evaluated_at=moment,
                    portfolio_equity_before=portfolio_equity,
                    settled_cash_before=settled_before,
                    unsettled_cash_before=unsettled_before,
                    position_quantity_before=ZERO, open_risk_before=open_risk,
                    risk_per_share=risk_per_share, risk_budget=risk_budget,
                    risk_quantity_limit=ZERO, liquidity_notional=liquidity,
                    liquidity_quantity_limit=ZERO, cash_quantity_limit=ZERO,
                    requested_quantity=ZERO, filled_quantity=ZERO,
                    limiting_constraints=(constraint,), stop_price_after=None,
                )
            )

        def close_quantity(
            *,
            symbol: str,
            requested_quantity: Decimal,
            maximum_fill_quantity: Decimal,
            reference: Decimal,
            moment: datetime,
            signal_at: datetime,
            reason: str,
            liquidity: Decimal,
        ) -> Decimal:
            nonlocal cash
            position = positions[symbol]
            requested = min(requested_quantity, position.quantity).to_integral_value(
                rounding=ROUND_FLOOR
            )
            filled = min(requested, maximum_fill_quantity).to_integral_value(
                rounding=ROUND_FLOOR
            )
            equity_before = equity()
            settled_before = cash
            unsettled_before = unsettled_total()
            open_risk_before = sum(
                max(ZERO, held.average_entry_price - held.stop_price) * held.quantity
                for held in positions.values()
            )
            impact_bps, total_cost_bps = _execution_cost_bps(
                self.config,
                reference_price=reference,
                filled_quantity=filled,
                lagged_liquidity_notional=liquidity,
            )
            price = _adverse_price(reference, "SELL", total_cost_bps)
            notional = filled * price
            fee = fee_for(notional, moment) if filled > ZERO else ZERO
            proceeds = notional - fee
            released_at = _release_at_after_sessions(rows, moment, self.config.cash_settlement_sessions)
            unsettled_cash.append((released_at, proceeds))
            record_notional(notional, moment)
            fraction = filled / position.quantity
            allocated_cost = position.entry_total_cost * fraction
            remaining_cost = position.entry_total_cost - allocated_cost
            total_exit_proceeds = position.realized_exit_proceeds + proceeds
            executions.append(
                ExecutionRecord(
                    symbol, "SELL", reason, signal_at, moment, reference, price,
                    requested, filled, fee,
                    "FILLED" if filled == requested else "PARTIALLY_FILLED",
                    liquidity,
                    self.config.bid_ask_half_spread_bps,
                    self.config.baseline_slippage_bps,
                    self.config.latency_adverse_bps,
                    impact_bps,
                    total_cost_bps,
                )
            )
            if filled == position.quantity:
                completed.append(
                    CompletedTrade(
                        symbol,
                        position.opened_at,
                        moment,
                        position.original_entry_total_cost,
                        total_exit_proceeds,
                        total_exit_proceeds / position.original_entry_total_cost - ONE,
                        reason,
                    )
                )
                del positions[symbol]
            else:
                positions[symbol] = _Position(
                    position.quantity - filled,
                    position.average_entry_price,
                    remaining_cost,
                    position.original_entry_total_cost,
                    total_exit_proceeds,
                    position.stop_price,
                    position.opened_at,
                )
            sizing_decisions.append(
                SizingDecisionTrace(
                    symbol=symbol,
                    action="SELL",
                    reason=reason,
                    signal_at=signal_at,
                    evaluated_at=moment,
                    portfolio_equity_before=equity_before,
                    settled_cash_before=settled_before,
                    unsettled_cash_before=unsettled_before,
                    position_quantity_before=position.quantity,
                    open_risk_before=open_risk_before,
                    risk_per_share=None,
                    risk_budget=None,
                    risk_quantity_limit=None,
                    liquidity_notional=liquidity,
                    liquidity_quantity_limit=maximum_fill_quantity,
                    cash_quantity_limit=None,
                    requested_quantity=requested,
                    filled_quantity=filled,
                    limiting_constraints=(
                        ("LIQUIDITY_CAP",) if filled < requested else ("POSITION_QUANTITY",)
                    ),
                    stop_price_after=(
                        positions[symbol].stop_price if symbol in positions else None
                    ),
                )
            )
            snapshot("POST_SIMULATED_SELL", moment, symbol)
            return filled

        # Each bar is processed at its open and then its close.  Only the
        # history ending at the current close is ever passed to the strategy.
        for bar in rows:
            symbol = bar.symbol
            history = histories[symbol]
            last_marks[symbol] = bar.open

            newly_settled = [amount for released_at, amount in unsettled_cash if released_at <= bar.open_at]
            if newly_settled:
                cash += sum(newly_settled, ZERO)
                unsettled_cash[:] = [
                    item for item in unsettled_cash if item[0] > bar.open_at
                ]
                snapshot("CASH_SETTLEMENT", bar.open_at, symbol)

            for action in actions:
                key = (action.symbol, action.effective_at, action.action_type)
                if action.symbol != symbol:
                    continue
                if (
                    action.action_type == "SPLIT"
                    and key not in applied_actions
                    and action.effective_at <= bar.open_at
                ):
                    applied_actions.add(key)
                    held = positions.get(symbol)
                    if held is not None:
                        quantity = held.quantity * action.split_ratio
                        if quantity != quantity.to_integral_value():
                            raise ValueError("split creates unsupported fractional simulated shares")
                        positions[symbol] = _Position(
                            quantity,
                            held.average_entry_price / action.split_ratio,
                            held.entry_total_cost,
                            held.original_entry_total_cost,
                            held.realized_exit_proceeds,
                            held.stop_price / action.split_ratio,
                            held.opened_at,
                        )
                        snapshot("STOCK_SPLIT", bar.open_at, symbol)
                    history[:] = [
                        replace(
                            prior,
                            open=prior.open / action.split_ratio,
                            high=prior.high / action.split_ratio,
                            low=prior.low / action.split_ratio,
                            close=prior.close / action.split_ratio,
                            volume=prior.volume * action.split_ratio,
                        )
                        for prior in history
                    ]
                    if symbol in pending and pending[symbol][3] is not None:
                        pending_action, pending_reason, pending_at, pending_atr = pending[symbol]
                        pending[symbol] = (
                            pending_action,
                            pending_reason,
                            pending_at,
                            pending_atr / action.split_ratio,
                        )
                elif action.action_type == "CASH_DIVIDEND":
                    if key not in applied_actions and action.effective_at <= bar.open_at:
                        applied_actions.add(key)
                        held = positions.get(symbol)
                        dividend_entitlements[key] = held.quantity if held else ZERO
                    if (
                        key in dividend_entitlements
                        and key not in paid_dividends
                        and action.cash_paid_at is not None
                        and action.cash_paid_at <= bar.open_at
                    ):
                        cash += dividend_entitlements[key] * action.cash_per_share
                        paid_dividends.add(key)
                        snapshot("CASH_DIVIDEND", bar.open_at, symbol)

            outcome = terminal_by_symbol.get(symbol)
            if outcome and outcome.effective_at <= bar.open_at and symbol not in terminated:
                processed_at = bar.open_at
                terminated.add(symbol)
                pending.pop(symbol, None)
                if symbol in positions:
                    portfolio_equity_before = equity()
                    settled_before = cash
                    unsettled_before = unsettled_total()
                    open_risk_before = sum(
                        max(ZERO, held.average_entry_price - held.stop_price) * held.quantity
                        for held in positions.values()
                    )
                    position = positions.pop(symbol)
                    proceeds = position.quantity * outcome.recovery_per_share
                    unsettled_cash.append((outcome.cash_settled_at, proceeds))
                    completed.append(
                        CompletedTrade(
                            symbol,
                            position.opened_at,
                            outcome.effective_at,
                            position.original_entry_total_cost,
                            position.realized_exit_proceeds + proceeds,
                            (position.realized_exit_proceeds + proceeds)
                            / position.original_entry_total_cost
                            - ONE,
                            outcome.terminal_type,
                        )
                    )
                    executions.append(
                        ExecutionRecord(
                            symbol, "TERMINAL_SETTLEMENT", outcome.terminal_type,
                            outcome.available_at, processed_at,
                            outcome.recovery_per_share, outcome.recovery_per_share,
                            position.quantity, position.quantity, ZERO, "FILLED", ZERO,
                            ZERO, ZERO, ZERO, ZERO, ZERO,
                        )
                    )
                    sizing_decisions.append(
                        SizingDecisionTrace(
                            symbol=symbol, action="TERMINAL_SETTLEMENT",
                            reason=outcome.terminal_type,
                            signal_at=outcome.available_at,
                            evaluated_at=processed_at,
                            portfolio_equity_before=portfolio_equity_before,
                            settled_cash_before=settled_before,
                            unsettled_cash_before=unsettled_before,
                            position_quantity_before=position.quantity,
                            open_risk_before=open_risk_before,
                            risk_per_share=None, risk_budget=None,
                            risk_quantity_limit=None, liquidity_notional=ZERO,
                            liquidity_quantity_limit=ZERO, cash_quantity_limit=None,
                            requested_quantity=position.quantity,
                            filled_quantity=position.quantity,
                            limiting_constraints=("MANDATORY_TERMINAL_OUTCOME",),
                            stop_price_after=None,
                        )
                    )
                    snapshot("TERMINAL_SETTLEMENT", processed_at, symbol)

            if bar.open_at >= end and symbol in positions:
                pending[symbol] = ("EXIT_LONG", "EVALUATION_END", end, None)
            if bar.open_at >= end:
                pending.pop(symbol, None) if symbol not in positions else None

            order = pending.pop(symbol, None)
            lagged = _lagged_liquidity(history, self.config.lagged_liquidity_lookback)
            liquidity = lagged or ZERO
            capacity_notional = liquidity * self.config.maximum_lagged_volume_participation
            if order and (bar.open_at < end or order[0] == "EXIT_LONG"):
                action, reason, signal_at, stored_atr = order
                order_age_seconds = Decimal(
                    str((bar.open_at - signal_at).total_seconds())
                )
                if order_age_seconds > self.config.maximum_order_age_minutes * Decimal("60"):
                    raise ValueError(
                        "pending order exceeded the configured maximum age; "
                        "daily bars cannot safely infer the missing intraday cancellation"
                    )
                if action == "ENTER_LONG" and symbol not in positions:
                    atr = stored_atr
                    portfolio_equity = equity()
                    settled_before = cash
                    unsettled_before = unsettled_total()
                    open_risk = sum(
                        max(ZERO, held.average_entry_price - held.stop_price) * held.quantity
                        for held in positions.values()
                    )
                    risk_per_share = (
                        atr * self.config.atr_stop_multiple if atr is not None else ZERO
                    )
                    if not eligible(symbol, bar.open_at):
                        record_unfilled_entry(
                            symbol=symbol, reference=bar.open, moment=bar.open_at,
                            signal_at=signal_at, reason=reason, liquidity=liquidity,
                            portfolio_equity=portfolio_equity,
                            settled_before=settled_before,
                            unsettled_before=unsettled_before, open_risk=open_risk,
                            risk_per_share=risk_per_share, risk_budget=None,
                            constraint="UNIVERSE_INELIGIBLE_AT_EXECUTION",
                        )
                    elif risk_per_share <= ZERO:
                        record_unfilled_entry(
                            symbol=symbol, reference=bar.open, moment=bar.open_at,
                            signal_at=signal_at, reason=reason, liquidity=liquidity,
                            portfolio_equity=portfolio_equity,
                            settled_before=settled_before,
                            unsettled_before=unsettled_before, open_risk=open_risk,
                            risk_per_share=risk_per_share, risk_budget=ZERO,
                            constraint="NO_POSITIVE_ATR_RISK_DISTANCE",
                        )
                    else:
                        risk_budget = min(
                            portfolio_equity * self.config.max_equity_risk_per_trade,
                            max(
                                ZERO,
                                portfolio_equity * self.config.maximum_aggregate_open_risk
                                - open_risk,
                            ),
                        )
                        risk_quantity_limit = (
                            risk_budget / risk_per_share
                        ).to_integral_value(rounding=ROUND_FLOOR)
                        maximum_cost_bps = (
                            self.config.bid_ask_half_spread_bps
                            + self.config.baseline_slippage_bps
                            + self.config.latency_adverse_bps
                            + self.config.liquidity_impact_bps_at_max_participation
                        )
                        maximum_price = _adverse_price(bar.open, "BUY", maximum_cost_bps)
                        maximum_position_notional = (
                            portfolio_equity * self.config.maximum_position_fraction
                        )
                        position_quantity_limit = (
                            maximum_position_notional / maximum_price
                        ).to_integral_value(rounding=ROUND_FLOOR)
                        requested = min(risk_quantity_limit, position_quantity_limit)
                        capacity = (capacity_notional / maximum_price).to_integral_value(rounding=ROUND_FLOOR)
                        cash_capacity = affordable_quantity(maximum_price, requested, bar.open_at)
                        filled = min(requested, capacity, cash_capacity)
                        impact_bps, total_cost_bps = _execution_cost_bps(
                            self.config,
                            reference_price=bar.open,
                            filled_quantity=filled,
                            lagged_liquidity_notional=liquidity,
                        )
                        fill_price = _adverse_price(bar.open, "BUY", total_cost_bps)
                        notional = filled * fill_price
                        fee = fee_for(notional, bar.open_at) if filled > ZERO else ZERO
                        if filled > ZERO:
                            cash -= notional + fee
                            record_notional(notional, bar.open_at)
                            positions[symbol] = _Position(
                                filled,
                                fill_price,
                                notional + fee,
                                notional + fee,
                                ZERO,
                                fill_price - risk_per_share,
                                bar.open_at,
                            )
                        constraints = []
                        if position_quantity_limit < risk_quantity_limit:
                            constraints.append("POSITION_FRACTION_CAP")
                        if capacity < requested:
                            constraints.append("LIQUIDITY_CAP")
                        if cash_capacity < requested:
                            constraints.append("CASH_AND_FEES")
                        if not constraints:
                            constraints.append("RISK_BUDGET")
                        sizing_decisions.append(
                            SizingDecisionTrace(
                                symbol=symbol,
                                action="BUY",
                                reason=reason,
                                signal_at=signal_at,
                                evaluated_at=bar.open_at,
                                portfolio_equity_before=portfolio_equity,
                                settled_cash_before=settled_before,
                                unsettled_cash_before=unsettled_before,
                                position_quantity_before=ZERO,
                                open_risk_before=open_risk,
                                risk_per_share=risk_per_share,
                                risk_budget=risk_budget,
                                risk_quantity_limit=risk_quantity_limit,
                                liquidity_notional=liquidity,
                                liquidity_quantity_limit=capacity,
                                cash_quantity_limit=cash_capacity,
                                requested_quantity=requested,
                                filled_quantity=filled,
                                limiting_constraints=tuple(constraints),
                                stop_price_after=(
                                    positions[symbol].stop_price if symbol in positions else None
                                ),
                            )
                        )
                        executions.append(
                            ExecutionRecord(
                                symbol, "BUY", reason, signal_at, bar.open_at,
                                bar.open, fill_price, requested, filled, fee,
                                "FILLED" if filled == requested and filled > ZERO else (
                                    "PARTIALLY_FILLED_CANCELED" if filled > ZERO else "REJECTED"
                                ),
                                liquidity,
                                self.config.bid_ask_half_spread_bps,
                                self.config.baseline_slippage_bps,
                                self.config.latency_adverse_bps,
                                impact_bps,
                                total_cost_bps,
                            )
                        )
                        snapshot("POST_SIMULATED_BUY", bar.open_at, symbol)
                elif action == "EXIT_LONG" and symbol in positions:
                    capacity = (capacity_notional / bar.open).to_integral_value(rounding=ROUND_FLOOR)
                    requested = positions[symbol].quantity
                    if capacity > ZERO:
                        filled = close_quantity(
                            symbol=symbol,
                            requested_quantity=requested,
                            maximum_fill_quantity=capacity,
                            reference=bar.open,
                            moment=bar.open_at,
                            signal_at=signal_at,
                            reason=reason,
                            liquidity=liquidity,
                        )
                    else:
                        record_unfilled_exit(
                            symbol=symbol, requested=requested, reference=bar.open,
                            moment=bar.open_at, signal_at=signal_at, reason=reason,
                            liquidity=liquidity, constraint="LIQUIDITY_CAP",
                        )
                        filled = ZERO
                    if filled < requested:
                        pending[symbol] = ("EXIT_LONG", reason, signal_at, None)

            if symbol in positions:
                position = positions[symbol]
                if bar.open <= position.stop_price:
                    stop_reference = bar.open
                elif bar.low <= position.stop_price:
                    stop_reference = position.stop_price - (
                        (position.stop_price - bar.low)
                        * self.config.stop_pierce_fill_fraction
                    )
                else:
                    stop_reference = None
                if stop_reference is not None:
                    capacity = (capacity_notional / stop_reference).to_integral_value(rounding=ROUND_FLOOR)
                    requested = position.quantity
                    if capacity > ZERO:
                        filled = close_quantity(
                            symbol=symbol,
                            requested_quantity=requested,
                            maximum_fill_quantity=capacity,
                            reference=stop_reference,
                            moment=(bar.open_at if bar.open <= position.stop_price else bar.close_at),
                            signal_at=bar.open_at,
                            reason="HARD_ATR_STOP",
                            liquidity=liquidity,
                        )
                        if filled < requested:
                            pending[symbol] = ("EXIT_LONG", "HARD_ATR_STOP", bar.open_at, None)
                    else:
                        stop_moment = (
                            bar.open_at if bar.open <= position.stop_price else bar.close_at
                        )
                        record_unfilled_exit(
                            symbol=symbol, requested=requested, reference=stop_reference,
                            moment=stop_moment, signal_at=bar.open_at,
                            reason="HARD_ATR_STOP", liquidity=liquidity,
                            constraint="LIQUIDITY_CAP",
                        )
                        pending[symbol] = (
                            "EXIT_LONG", "HARD_ATR_STOP", bar.open_at, None
                        )

            last_marks[symbol] = bar.close
            history.append(bar)
            if start <= bar.close_at < end and eligible(symbol, bar.available_at):
                action = strategy_instance.decide(symbol, tuple(history), parameters)
                if action not in {ACTION_HOLD, ACTION_ENTER_LONG, ACTION_EXIT_LONG}:
                    raise ValueError("strategy returned an unsupported action")
                if action == ACTION_ENTER_LONG and symbol not in positions and symbol not in pending:
                    feature_atr = getattr(strategy_instance, "atr_for_signal", None)
                    if feature_atr is not None and not callable(feature_atr):
                        raise ValueError("strategy ATR provider is invalid")
                    historical_atr = _atr(history, self.config.atr_window)
                    atr = (
                        _decimal(
                            feature_atr(symbol, tuple(history), parameters),
                            "strategy feature ATR",
                            positive=True,
                        )
                        if feature_atr is not None
                        else historical_atr
                    )
                    if feature_atr is not None:
                        if historical_atr is None:
                            raise ValueError(
                                "strategy feature ATR lacks causal bar history"
                            )
                        tolerance = max(
                            Decimal("1e-24"), abs(historical_atr) * Decimal("1e-24")
                        )
                        if abs(atr - historical_atr) > tolerance:
                            raise ValueError(
                                "strategy feature ATR differs from causal bar-derived ATR"
                            )
                    if atr is not None:
                        pending[symbol] = (action, "STRATEGY_SIGNAL", bar.available_at, atr)
                elif action == ACTION_EXIT_LONG and symbol in positions:
                    pending[symbol] = (action, "STRATEGY_SIGNAL", bar.available_at, None)
            elif start <= bar.close_at < end and symbol in positions and not eligible(symbol, bar.available_at):
                pending[symbol] = ("EXIT_LONG", "UNIVERSE_REMOVAL", bar.available_at, None)
            if start <= bar.close_at <= end:
                equity_curve.append((bar.close_at, equity()))
                snapshot("SESSION_CLOSE", bar.close_at, symbol)

        for outcome in outcomes:
            if outcome.symbol in positions and outcome.effective_at <= end:
                latest_state_at = (
                    portfolio_states[-1].as_of_at
                    if portfolio_states
                    else outcome.effective_at
                )
                processed_at = max(outcome.effective_at, end, latest_state_at)
                portfolio_equity_before = equity()
                settled_before = cash
                unsettled_before = unsettled_total()
                open_risk_before = sum(
                    max(ZERO, held.average_entry_price - held.stop_price) * held.quantity
                    for held in positions.values()
                )
                position = positions.pop(outcome.symbol)
                proceeds = position.quantity * outcome.recovery_per_share
                unsettled_cash.append((outcome.cash_settled_at, proceeds))
                completed.append(
                    CompletedTrade(
                        outcome.symbol, position.opened_at, outcome.effective_at,
                        position.original_entry_total_cost,
                        position.realized_exit_proceeds + proceeds,
                        (position.realized_exit_proceeds + proceeds)
                        / position.original_entry_total_cost - ONE,
                        outcome.terminal_type,
                    )
                )
                executions.append(
                    ExecutionRecord(
                        outcome.symbol, "TERMINAL_SETTLEMENT", outcome.terminal_type,
                        outcome.available_at, processed_at,
                        outcome.recovery_per_share, outcome.recovery_per_share,
                        position.quantity, position.quantity, ZERO, "FILLED", ZERO,
                        ZERO, ZERO, ZERO, ZERO, ZERO,
                    )
                )
                sizing_decisions.append(
                    SizingDecisionTrace(
                        symbol=outcome.symbol, action="TERMINAL_SETTLEMENT",
                        reason=outcome.terminal_type, signal_at=outcome.available_at,
                        evaluated_at=processed_at,
                        portfolio_equity_before=portfolio_equity_before,
                        settled_cash_before=settled_before,
                        unsettled_cash_before=unsettled_before,
                        position_quantity_before=position.quantity,
                        open_risk_before=open_risk_before,
                        risk_per_share=None, risk_budget=None,
                        risk_quantity_limit=None, liquidity_notional=ZERO,
                        liquidity_quantity_limit=ZERO, cash_quantity_limit=None,
                        requested_quantity=position.quantity,
                        filled_quantity=position.quantity,
                        limiting_constraints=("MANDATORY_TERMINAL_OUTCOME",),
                        stop_price_after=None,
                    )
                )
                snapshot("TERMINAL_SETTLEMENT", processed_at, outcome.symbol)
        completion_validator = getattr(
            strategy_instance, "validate_replay_completion", None
        )
        if completion_validator is not None:
            if not callable(completion_validator):
                raise ValueError("strategy replay-completion validator is invalid")
            completion_validator()
        if positions:
            raise ValueError("evaluation lacks a next-bar, liquidity-capped exit for open positions")
        if not equity_curve:
            raise ValueError("evaluation window contains no completed bars")

        ending = cash + sum(amount for _, amount in unsettled_cash)
        curve_values = [self.config.initial_cash, *[item[1] for item in equity_curve], ending]
        parameter_hash = strategy_parameter_hash(parameters)
        engine_config_canonical_json = canonical_engine_configuration(
            self.config, self.fee_schedule
        )
        engine_config_hash = hashlib.sha256(
            engine_config_canonical_json.encode("utf-8")
        ).hexdigest()
        return BacktestResult(
            str(strategy_instance.version),
            parameter_hash,
            self.data_attestation.source_id,
            self.data_attestation.validation_receipt_sha256,
            self.fee_schedule.schedule_id,
            self.config.execution_scenario,
            self.config.initial_cash,
            ending,
            ending / self.config.initial_cash - ONE,
            _drawdown(curve_values),
            tuple(executions),
            tuple(completed),
            tuple(equity_curve),
            tuple(sizing_decisions),
            tuple(portfolio_states),
            evaluation_start=start,
            evaluation_end=end,
            source_content_sha256=self.data_attestation.source_content_sha256,
            evidence_role_hashes=self.data_attestation.evidence_role_hashes,
            engine_policy_version=ENGINE_POLICY_VERSION,
            engine_config_sha256=engine_config_hash,
            engine_config_canonical_json=engine_config_canonical_json,
            strategy_entrypoint=strategy_entrypoint,
            strategy_source_sha256=strategy_source_sha256,
        )

    def run_base_and_pessimistic(self, **inputs: Any) -> Mapping[str, BacktestResult]:
        """Run the exact same sealed inputs under separate cost assumptions."""
        if self.config.execution_scenario != "BASE":
            raise ValueError("scenario comparison must start from a BASE engine")
        if self.fee_schedule.schedule_id.endswith("-BASE-COMMISSION"):
            raise ValueError(
                "authenticated comparison requires exact policy-derived scenario profiles"
            )
        materialized = dict(inputs)
        for name in ("bars", "universe_events", "terminal_outcomes", "corporate_actions"):
            materialized[name] = tuple(materialized.get(name, ()))
        base = self.run(**materialized)
        pessimistic_config = replace(
            self.config,
            execution_scenario="PESSIMISTIC",
            baseline_slippage_bps=max(
                Decimal("20"), self.config.baseline_slippage_bps * Decimal("2")
            ),
            bid_ask_half_spread_bps=max(
                Decimal("10"), self.config.bid_ask_half_spread_bps * Decimal("2")
            ),
            latency_adverse_bps=max(
                Decimal("2"), self.config.latency_adverse_bps * Decimal("2")
            ),
            liquidity_impact_bps_at_max_participation=max(
                Decimal("20"),
                self.config.liquidity_impact_bps_at_max_participation * Decimal("2"),
            ),
            maximum_lagged_volume_participation=(
                self.config.maximum_lagged_volume_participation / Decimal("2")
            ),
            maximum_order_age_minutes=self.config.maximum_order_age_minutes * Decimal("2"),
            stop_pierce_fill_fraction=ONE,
        )
        pessimistic_fee_schedule = ExchangeFeeSchedule(
            f"{self.fee_schedule.schedule_id}-PESSIMISTIC",
            tuple(
                ExchangeFeeTier(
                    tier.prior_monthly_notional_below,
                    tier.variable_bps * Decimal("2"),
                    tier.minimum_fee * Decimal("2"),
                )
                for tier in self.fee_schedule.tiers
            ),
        )
        pessimistic = GuardrailedBacktestEngine(
            config=pessimistic_config,
            fee_schedule=pessimistic_fee_schedule,
            data_attestation=self.data_attestation,
        ).run(**materialized)
        return {"BASE": base, "PESSIMISTIC": pessimistic}


@dataclass(frozen=True)
class ChronologicalSplit:
    in_sample_start: datetime
    in_sample_end: datetime
    out_of_sample_start: datetime
    out_of_sample_end: datetime
    execution_buffer_end: datetime
    settlement_buffer_end: datetime
    in_sample_fraction: Decimal = Decimal("0.60")
    out_of_sample_fraction: Decimal = Decimal("0.40")


def strict_sixty_forty_split(bars: Iterable[MarketBar]) -> ChronologicalSplit:
    closes = sorted({bar.close_at for bar in bars})
    if len(closes) < 12:
        raise ValueError(
            "at least ten measured sessions plus exit and settlement sessions are required"
        )
    measured = closes[:-2]
    cut = math.floor(len(measured) * 0.60)
    return ChronologicalSplit(
        measured[0],
        measured[cut - 1],
        measured[cut],
        measured[-1],
        closes[-2],
        closes[-1],
    )


@dataclass(frozen=True)
class MonteCarloResult:
    iterations: int
    seed: int
    drawdown_p50: Decimal
    drawdown_p95: Decimal
    worst_drawdown: Decimal


def monte_carlo_trade_order_risk(
    equity_fraction_returns: Sequence[Decimal], *, iterations: int = 2_000, seed: int = 0
) -> MonteCarloResult:
    values = tuple(_signed_decimal(item, "trade return") for item in equity_fraction_returns)
    if len(values) < 2 or iterations < 100:
        raise ValueError("Monte Carlo requires at least two trades and 100 iterations")
    generator = random.Random(seed)
    drawdowns: list[Decimal] = []
    for _ in range(iterations):
        order = [values[generator.randrange(len(values))] for _ in range(len(values))]
        equity = ONE
        curve = [equity]
        for value in order:
            equity *= ONE + value
            curve.append(equity)
        drawdowns.append(_drawdown(curve))
    drawdowns.sort()

    def percentile(fraction: Decimal) -> Decimal:
        index = min(len(drawdowns) - 1, math.ceil(float(fraction) * len(drawdowns)) - 1)
        return drawdowns[index]

    return MonteCarloResult(
        iterations,
        seed,
        percentile(Decimal("0.50")),
        percentile(Decimal("0.95")),
        drawdowns[-1],
    )


@dataclass(frozen=True)
class WalkForwardSelection:
    split: ChronologicalSplit
    selected_parameters: Mapping[str, Any]
    selected_parameter_hash: str
    in_sample_fold_scores: tuple[Decimal, ...]
    out_of_sample_result: BacktestResult
    monte_carlo: MonteCarloResult | None
    out_of_sample_opened_only_after_selection: bool = True


class WalkForwardOptimizer:
    """Select parameters inside the first 60%, then open the final 40% once."""

    def __init__(self, engine: GuardrailedBacktestEngine, validation_folds: int = 3) -> None:
        if validation_folds < 2:
            raise ValueError("walk-forward validation requires at least two folds")
        self.engine = engine
        self.validation_folds = validation_folds

    def validate(
        self,
        *,
        bars: Sequence[MarketBar],
        universe_events: Sequence[UniverseEvent],
        terminal_outcomes: Sequence[TerminalOutcome],
        corporate_actions: Sequence[CorporateAction],
        prices_are_unadjusted: bool,
        strategy: CausalStrategy,
        parameter_grid: Mapping[str, Sequence[Any]],
        monte_carlo_iterations: int = 2_000,
        monte_carlo_seed: int = 0,
    ) -> WalkForwardSelection:
        if not parameter_grid or any(not values for values in parameter_grid.values()):
            raise ValueError("a non-empty parameter grid is required")
        split = strict_sixty_forty_split(bars)
        in_sample_closes = sorted(
            {
                bar.close_at
                for bar in bars
                if split.in_sample_start <= bar.close_at <= split.in_sample_end
            }
        )
        # The final two in-sample sessions are never measured.  They exist only
        # to liquidate and settle the last fold without touching OOS data.
        fold_measurement_closes = in_sample_closes[:-2]
        all_closes = sorted({bar.close_at for bar in bars})
        initial_train = max(2, len(fold_measurement_closes) // 2)
        remaining = len(fold_measurement_closes) - initial_train
        if remaining < self.validation_folds:
            raise ValueError("insufficient in-sample sessions for walk-forward folds")
        fold_size = remaining // self.validation_folds
        if fold_size < 3:
            raise ValueError("each walk-forward fold needs signal, measurement, and exit sessions")
        keys = tuple(sorted(parameter_grid))
        candidates = [
            dict(zip(keys, combination))
            for combination in itertools.product(*(parameter_grid[key] for key in keys))
        ]
        scored: list[tuple[Decimal, Decimal, str, Mapping[str, Any], tuple[Decimal, ...]]] = []
        for candidate in candidates:
            fold_returns: list[Decimal] = []
            fold_drawdowns: list[Decimal] = []
            for fold in range(self.validation_folds):
                validation_start_index = initial_train + fold * fold_size
                start = fold_measurement_closes[validation_start_index]
                end_index = min(
                    validation_start_index + fold_size - 2,
                    len(fold_measurement_closes) - 2,
                )
                if end_index <= validation_start_index:
                    raise ValueError("insufficient in-sample sessions for non-overlapping folds")
                end = fold_measurement_closes[end_index]
                global_end_index = all_closes.index(end) + 2
                if global_end_index >= len(all_closes):
                    raise ValueError("walk-forward fold lacks exit and settlement sessions")
                permitted_through = all_closes[global_end_index]
                sealed_fold_bars = [
                    bar for bar in bars if bar.close_at <= permitted_through
                ]
                result = self.engine.run(
                    bars=sealed_fold_bars,
                    universe_events=universe_events,
                    terminal_outcomes=terminal_outcomes,
                    corporate_actions=corporate_actions,
                    prices_are_unadjusted=prices_are_unadjusted,
                    strategy=strategy,
                    parameters=candidate,
                    evaluation_start=start,
                    evaluation_end=end,
                )
                fold_returns.append(result.total_return)
                fold_drawdowns.append(result.maximum_drawdown)
            candidate_hash = hashlib.sha256(_canonical_json(candidate).encode()).hexdigest()
            scored.append(
                (
                    median(fold_returns),
                    -median(fold_drawdowns),
                    candidate_hash,
                    candidate,
                    tuple(fold_returns),
                )
            )
        _, _, selected_hash, selected, fold_scores = max(
            scored, key=lambda item: (item[0], item[1], -int(item[2], 16))
        )
        sealed_oos_bars = [
            bar for bar in bars if bar.close_at <= split.settlement_buffer_end
        ]
        oos = self.engine.run(
            bars=sealed_oos_bars,
            universe_events=universe_events,
            terminal_outcomes=terminal_outcomes,
            corporate_actions=corporate_actions,
            prices_are_unadjusted=prices_are_unadjusted,
            strategy=strategy,
            parameters=selected,
            evaluation_start=split.out_of_sample_start,
            evaluation_end=split.out_of_sample_end,
        )
        monte_carlo = None
        grouped_pnl: dict[tuple[str, datetime], Decimal] = {}
        for item in oos.completed_trades:
            key = (item.symbol, item.opened_at)
            grouped_pnl[key] = grouped_pnl.get(key, ZERO) + (
                item.exit_net_proceeds - item.entry_total_cost
            )
        returns = [pnl / oos.starting_equity for pnl in grouped_pnl.values()]
        if len(returns) >= 2:
            monte_carlo = monte_carlo_trade_order_risk(
                returns,
                iterations=monte_carlo_iterations,
                seed=monte_carlo_seed,
            )
        return WalkForwardSelection(
            split,
            selected,
            selected_hash,
            fold_scores,
            oos,
            monte_carlo,
        )
