from __future__ import annotations

"""Isolated synthetic-only VectorBT research-engine pilot.

This adapter is deliberately NOT part of the authenticated replay route.  It
accepts only caller-declared synthetic bars under a `SyntheticPilotAttestation`
that is structurally distinct from `ReplayDataAttestation` and can never be
substituted for it.  It has no broker, network, credential or order-submission
capability, imports VectorBT lazily and touches exactly one VectorBT entry
point: `Portfolio.from_signals`.

Nothing produced here is admissible evidence.  Its benchmark and Sharpe outputs
are explicitly DIAGNOSTIC_ONLY: they do not satisfy `SharpeMetricReadinessGate`
or the benchmark distribution-evidence rules, and must never be routed into any
existing performance ledger.  Parity with `GuardrailedBacktestEngine` is
unproven and is asserted nowhere in this module.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import hashlib
import math
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "vectorbt-synthetic-pilot-adapter-v2"
ASSUMPTIONS_VERSION = "synthetic-pilot-next-open-longonly-assumptions-v2"

# The pilot is pinned to one verified dependency build.  The licence is
# recorded exactly as the installed distribution states it.  This records the
# evaluated build only and infers no commercial acceptability.
VECTORBT_PINNED_VERSION = "1.1.0"
VECTORBT_LICENCE = "Apache 2.0 with Commons Clause"

# Declared output precision.  Every float VectorBT returns is quantized to this
# exponent before it is compared, reported or hashed.
QUANTIZE_EXPONENT = Decimal("0.000000001")
QUANTIZE_DECIMAL_PLACES = 9

# Diagnostic Sharpe definition.  Fixed and explicit so the number can never be
# mistaken for the gated Sharpe metric.
DIAGNOSTIC_RISK_FREE_RATE = Decimal("0")
DIAGNOSTIC_ANNUALIZATION_FACTOR = 252
DIAGNOSTIC_RETURN_FREQUENCY = "1D"
# VectorBT's own default year_freq is "365 days".  The pilot pins 252 trading
# days explicitly so the recorded definition cannot drift with library settings.
DIAGNOSTIC_YEAR_FREQUENCY = "252 days"
DIAGNOSTIC_SAMPLE_DDOF = 1
DIAGNOSTIC_LABEL = "DIAGNOSTIC_ONLY"
DIAGNOSTIC_SHARPE_FORMULA = (
    "sqrt(252) * sample_mean(daily_return - 0) / sample_stddev(daily_return, ddof=1)"
)

ACTION_ENTER_LONG = "ENTER_LONG"
ACTION_EXIT_LONG = "EXIT_LONG"
PILOT_ACTIONS = (ACTION_ENTER_LONG, ACTION_EXIT_LONG)

BENCHMARK_DEFINITION = (
    "SYNTHETIC_SAME_WINDOW_BUY_AND_HOLD_FIRST_CLOSE_TO_LAST_CLOSE_NO_COSTS"
)
TURNOVER_DEFINITION = "TOTAL_FILLED_NOTIONAL_DIVIDED_BY_INITIAL_CASH"
WIN_RATE_DEFINITION = "PROFITABLE_CLOSED_TRADES_DIVIDED_BY_CLOSED_TRADES"
TOTAL_RETURN_DEFINITION = "ENDING_MARK_TO_MARK_VALUE_INCLUDING_OPEN_POSITIONS"
SIGNAL_EXECUTION_PRICE_POLICY = (
    "STRATEGY_SIGNAL_BOUND_BAR_CLOSE_THEN_EXACT_NEXT_BAR_OPEN"
)
HARD_STOP_EXIT_PRICE_POLICY = (
    "STOP_MARKET_FROM_ENTRY_FILL_AT_INTRABAR_STOP_OR_ADVERSE_CURRENT_OPEN_AFTER_GAP_WITH_SLIPPAGE"
)
HARD_STOP_ACTIVE_ON_ENTRY_BAR = False
VALUATION_PRICE_POLICY = "PRIOR_BAR_CLOSE"
DAILY_SESSION_SPACING_SECONDS = 86_400

# VectorBT surfaces these on the top-level module.  The pilot must never touch
# them; a deterministic test pins that this module's source references none.
FORBIDDEN_VECTORBT_ATTRIBUTES = (
    "AlpacaData",
    "BinanceData",
    "CCXTData",
    "Data",
    "DataUpdater",
    "GBMData",
    "SyntheticData",
    "YFData",
)

# Authority flags fixed false on every pilot result.
FIXED_FALSE = (
    "performance_claim_allowed",
    "paper_trade_promotion_allowed",
    "promotion_allowed",
    "track_record_claim",
    "learning_eligible",
    "admissible_as_replay_evidence",
    "broker_connection_allowed",
    "orders_submitted",
    "live_trading_enabled",
    "satisfies_sharpe_metric_readiness_gate",
    "satisfies_benchmark_evidence_rules",
    "parity_with_guardrailed_engine_proven",
)


def _canonical_json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required(value: Any, name: str, maximum: int = 200) -> str:
    resolved = str(value or "").strip()
    if not resolved or len(resolved) > maximum:
        raise ValueError(f"{name} is required and must not exceed {maximum} characters")
    return resolved


def _digest(value: Any, name: str) -> str:
    resolved = _required(value, name, 64).lower()
    if len(resolved) != 64 or any(item not in "0123456789abcdef" for item in resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _decimal(value: Any, name: str, *, positive: bool = False) -> Decimal:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite() or resolved < 0 or (positive and resolved <= 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'non-negative'}")
    return resolved


def _time(value: Any, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def quantize(value: Any, name: str) -> Decimal:
    """Quantize one VectorBT float to the declared pilot precision."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{name} must be a real number produced by the pilot")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite():
        raise ValueError(f"{name} must be finite")
    return resolved.quantize(QUANTIZE_EXPONENT, rounding=ROUND_HALF_EVEN)


def decimal_text(value: Decimal) -> str:
    """Render a quantized Decimal as a stable canonical string."""
    return format(value, "f")


def optional_decimal_text(value: Decimal | None) -> str | None:
    """Render an optional metric without inventing a numeric substitute."""
    return None if value is None else decimal_text(value)


@dataclass(frozen=True)
class SyntheticPilotAttestation:
    """Declared identity of synthetic pilot data.

    This type is intentionally incompatible with `ReplayDataAttestation`.  It
    pins no authenticated evidence roles, carries no validation receipt and
    grants no authority.  It exists so a pilot run is reproducible, not so a
    pilot run can be believed.
    """

    data_version: str
    content_sha256: str
    generator_description: str
    synthetic_data_declared: bool
    point_in_time_verified: bool
    timestamps_verified: bool
    no_future_data_verified: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_version", _required(self.data_version, "data_version"))
        object.__setattr__(
            self, "content_sha256", _digest(self.content_sha256, "content_sha256")
        )
        object.__setattr__(
            self,
            "generator_description",
            _required(self.generator_description, "generator_description", 300),
        )
        for name in (
            "synthetic_data_declared",
            "point_in_time_verified",
            "timestamps_verified",
            "no_future_data_verified",
        ):
            if getattr(self, name) is not True:
                raise ValueError(f"{name} must be explicitly True for a pilot run")

    def identity(self) -> dict[str, Any]:
        return {
            "data_version": self.data_version,
            "content_sha256": self.content_sha256,
            "generator_description": self.generator_description,
            "synthetic_data_declared": True,
            "point_in_time_verified": True,
            "timestamps_verified": True,
            "no_future_data_verified": True,
            "authenticated_replay_evidence": False,
        }

    def identity_sha256(self) -> str:
        return _sha256(self.identity())


@dataclass(frozen=True)
class SyntheticPilotBar:
    """One synthetic daily session.

    Deliberately a separate type from `MarketBar` so pilot data can never be
    passed to `GuardrailedBacktestEngine`, and engine bars can never silently
    become pilot input.
    """

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
        symbol = _required(self.symbol, "symbol", 32).upper()
        object.__setattr__(self, "symbol", symbol)
        for name in ("open_at", "close_at", "available_at"):
            object.__setattr__(self, name, _time(getattr(self, name), name))
        if not self.open_at < self.close_at:
            raise ValueError("bar must open strictly before it closes")
        if self.available_at < self.close_at:
            raise ValueError("bar cannot become available before its close")
        for name in ("open", "high", "low", "close"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name, positive=True))
        object.__setattr__(self, "volume", _decimal(self.volume, "volume"))
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC values are inconsistent")

    def material(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "open_at": self.open_at.isoformat(),
            "close_at": self.close_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "open": decimal_text(self.open),
            "high": decimal_text(self.high),
            "low": decimal_text(self.low),
            "close": decimal_text(self.close),
            "volume": decimal_text(self.volume),
        }


@dataclass(frozen=True)
class SyntheticPilotSignal:
    """A decision bound to one exact bar close, carrying its availability."""

    symbol: str
    action: str
    bar_close_at: datetime
    available_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _required(self.symbol, "symbol", 32).upper())
        action = _required(self.action, "action", 40).upper()
        if action not in PILOT_ACTIONS:
            raise ValueError("pilot action must be ENTER_LONG or EXIT_LONG")
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "bar_close_at", _time(self.bar_close_at, "bar_close_at"))
        object.__setattr__(self, "available_at", _time(self.available_at, "available_at"))

    def material(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "bar_close_at": self.bar_close_at.isoformat(),
            "available_at": self.available_at.isoformat(),
        }


@dataclass(frozen=True)
class VectorBTPilotAssumptions:
    """Every execution assumption the pilot pins into VectorBT."""

    initial_cash: Decimal
    fees_bps_per_side: Decimal
    slippage_bps_per_side: Decimal
    maximum_position_fraction: Decimal
    hard_stop_loss_fraction: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "initial_cash", _decimal(self.initial_cash, "initial_cash", positive=True)
        )
        object.__setattr__(
            self, "fees_bps_per_side", _decimal(self.fees_bps_per_side, "fees_bps_per_side")
        )
        object.__setattr__(
            self,
            "slippage_bps_per_side",
            _decimal(self.slippage_bps_per_side, "slippage_bps_per_side", positive=True),
        )
        object.__setattr__(
            self,
            "maximum_position_fraction",
            _decimal(self.maximum_position_fraction, "maximum_position_fraction", positive=True),
        )
        object.__setattr__(
            self,
            "hard_stop_loss_fraction",
            _decimal(self.hard_stop_loss_fraction, "hard_stop_loss_fraction", positive=True),
        )
        if self.slippage_bps_per_side < Decimal("10"):
            raise ValueError("pilot adverse slippage cannot be below 10 bps (0.10%)")
        if self.maximum_position_fraction > Decimal("1"):
            raise ValueError("maximum position fraction cannot exceed 1; the pilot has no leverage")
        if self.hard_stop_loss_fraction >= Decimal("1"):
            raise ValueError("hard stop-loss fraction must be below 1")

    def material(self) -> dict[str, Any]:
        return {
            "assumptions_version": ASSUMPTIONS_VERSION,
            "initial_cash": decimal_text(self.initial_cash),
            "fees_bps_per_side": decimal_text(self.fees_bps_per_side),
            "slippage_bps_per_side": decimal_text(self.slippage_bps_per_side),
            "maximum_position_fraction": decimal_text(self.maximum_position_fraction),
            "hard_stop_loss_fraction": decimal_text(self.hard_stop_loss_fraction),
            "direction": "longonly",
            "accumulate": False,
            "cash_sharing": False,
            "leverage_allowed": False,
            "signal_execution_price_policy": SIGNAL_EXECUTION_PRICE_POLICY,
            "signal_same_bar_execution_allowed": False,
            "hard_stop_exit_price_policy": HARD_STOP_EXIT_PRICE_POLICY,
            "hard_stop_active_on_entry_bar": HARD_STOP_ACTIVE_ON_ENTRY_BAR,
            "partial_fills_allowed": False,
            "rejected_order_policy": "FAIL_RUN",
            "valuation_price_policy": VALUATION_PRICE_POLICY,
            "return_frequency": DIAGNOSTIC_RETURN_FREQUENCY,
        }

    def identity_sha256(self) -> str:
        return _sha256(self.material())


@dataclass(frozen=True)
class VectorBTPilotResult:
    """Quantized, explicitly non-admissible pilot output."""

    schema_version: str
    policy_version: str
    symbol: str
    session_count: int
    evaluation_start: datetime
    evaluation_end: datetime
    data_version: str
    data_content_sha256: str
    attestation_sha256: str
    assumptions_version: str
    assumptions_sha256: str
    assumptions: tuple[tuple[str, Any], ...]
    input_sha256: str
    strategy_version: str
    vectorbt_version: str
    vectorbt_licence: str
    quantize_decimal_places: int
    total_return: Decimal
    benchmark_total_return: Decimal
    excess_return_vs_benchmark: Decimal
    sharpe_ratio: Decimal | None
    maximum_drawdown: Decimal
    win_rate: Decimal | None
    turnover: Decimal
    trade_count: int
    open_trade_count: int
    order_count: int
    benchmark_label: str = DIAGNOSTIC_LABEL
    sharpe_label: str = DIAGNOSTIC_LABEL
    benchmark_definition: str = BENCHMARK_DEFINITION
    turnover_definition: str = TURNOVER_DEFINITION
    win_rate_definition: str = WIN_RATE_DEFINITION
    total_return_definition: str = TOTAL_RETURN_DEFINITION
    sharpe_risk_free_rate: Decimal = DIAGNOSTIC_RISK_FREE_RATE
    sharpe_annualization_factor: int = DIAGNOSTIC_ANNUALIZATION_FACTOR
    sharpe_return_frequency: str = DIAGNOSTIC_RETURN_FREQUENCY
    sharpe_year_frequency: str = DIAGNOSTIC_YEAR_FREQUENCY
    sharpe_sample_ddof: int = DIAGNOSTIC_SAMPLE_DDOF
    sharpe_formula: str = DIAGNOSTIC_SHARPE_FORMULA
    simulation_only: bool = True
    synthetic_data_only: bool = True
    # VectorBT recycles sale proceeds within the same bar.  The pilot models no
    # settlement delay, so it is structurally more permissive than
    # GuardrailedBacktestEngine, which holds proceeds unsettled for a session.
    cash_settlement_modelled: bool = False

    def __post_init__(self) -> None:
        required_constants = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "assumptions_version": ASSUMPTIONS_VERSION,
            "vectorbt_version": VECTORBT_PINNED_VERSION,
            "vectorbt_licence": VECTORBT_LICENCE,
            "quantize_decimal_places": QUANTIZE_DECIMAL_PLACES,
            "benchmark_label": DIAGNOSTIC_LABEL,
            "sharpe_label": DIAGNOSTIC_LABEL,
            "benchmark_definition": BENCHMARK_DEFINITION,
            "turnover_definition": TURNOVER_DEFINITION,
            "win_rate_definition": WIN_RATE_DEFINITION,
            "total_return_definition": TOTAL_RETURN_DEFINITION,
            "sharpe_risk_free_rate": DIAGNOSTIC_RISK_FREE_RATE,
            "sharpe_annualization_factor": DIAGNOSTIC_ANNUALIZATION_FACTOR,
            "sharpe_return_frequency": DIAGNOSTIC_RETURN_FREQUENCY,
            "sharpe_year_frequency": DIAGNOSTIC_YEAR_FREQUENCY,
            "sharpe_sample_ddof": DIAGNOSTIC_SAMPLE_DDOF,
            "sharpe_formula": DIAGNOSTIC_SHARPE_FORMULA,
            "simulation_only": True,
            "synthetic_data_only": True,
            "cash_settlement_modelled": False,
        }
        for name, expected in required_constants.items():
            if getattr(self, name) != expected:
                raise ValueError(f"{name} must remain fixed at {expected!r}")
        for name in (
            "data_content_sha256",
            "attestation_sha256",
            "assumptions_sha256",
            "input_sha256",
        ):
            _digest(getattr(self, name), name)
        if type(self.assumptions) is not tuple or any(
            type(item) is not tuple or len(item) != 2 for item in self.assumptions
        ):
            raise ValueError("assumptions must be an immutable tuple of key/value pairs")
        assumptions_material = dict(self.assumptions)
        if len(assumptions_material) != len(self.assumptions) or self.assumptions != tuple(
            sorted(assumptions_material.items())
        ):
            raise ValueError("assumptions must contain unique keys in canonical order")
        if _sha256(assumptions_material) != self.assumptions_sha256:
            raise ValueError("assumptions do not match assumptions_sha256")
        for name in ("symbol", "data_version", "strategy_version"):
            _required(getattr(self, name), name)
        if self.session_count < 2:
            raise ValueError("session_count must be at least two")
        if min(self.trade_count, self.open_trade_count, self.order_count) < 0:
            raise ValueError("trade and order counts must be non-negative")
        if _time(self.evaluation_start, "evaluation_start") >= _time(
            self.evaluation_end, "evaluation_end"
        ):
            raise ValueError("evaluation_start must precede evaluation_end")

    def material(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "symbol": self.symbol,
            "session_count": self.session_count,
            "evaluation_start": self.evaluation_start.isoformat(),
            "evaluation_end": self.evaluation_end.isoformat(),
            "data_version": self.data_version,
            "data_content_sha256": self.data_content_sha256,
            "attestation_sha256": self.attestation_sha256,
            "assumptions_version": self.assumptions_version,
            "assumptions_sha256": self.assumptions_sha256,
            "assumptions": dict(self.assumptions),
            "input_sha256": self.input_sha256,
            "strategy_version": self.strategy_version,
            "vectorbt_version": self.vectorbt_version,
            "vectorbt_licence": self.vectorbt_licence,
            "quantize_decimal_places": self.quantize_decimal_places,
            "total_return": decimal_text(self.total_return),
            "benchmark_total_return": decimal_text(self.benchmark_total_return),
            "excess_return_vs_benchmark": decimal_text(self.excess_return_vs_benchmark),
            "sharpe_ratio": optional_decimal_text(self.sharpe_ratio),
            "maximum_drawdown": decimal_text(self.maximum_drawdown),
            "win_rate": optional_decimal_text(self.win_rate),
            "turnover": decimal_text(self.turnover),
            "trade_count": self.trade_count,
            "open_trade_count": self.open_trade_count,
            "order_count": self.order_count,
            "benchmark_label": self.benchmark_label,
            "sharpe_label": self.sharpe_label,
            "benchmark_definition": self.benchmark_definition,
            "turnover_definition": self.turnover_definition,
            "win_rate_definition": self.win_rate_definition,
            "total_return_definition": self.total_return_definition,
            "sharpe_risk_free_rate": decimal_text(self.sharpe_risk_free_rate),
            "sharpe_annualization_factor": self.sharpe_annualization_factor,
            "sharpe_return_frequency": self.sharpe_return_frequency,
            "sharpe_year_frequency": self.sharpe_year_frequency,
            "sharpe_sample_ddof": self.sharpe_sample_ddof,
            "sharpe_formula": self.sharpe_formula,
            "simulation_only": self.simulation_only,
            "synthetic_data_only": self.synthetic_data_only,
            "cash_settlement_modelled": self.cash_settlement_modelled,
            "cash_settlement_note": (
                "No settlement delay is modelled. Sale proceeds are immediately "
                "reusable, which is more permissive than GuardrailedBacktestEngine."
            ),
            "diagnostic_metrics_do_not_satisfy_existing_gates": (
                "Benchmark and Sharpe are DIAGNOSTIC_ONLY. They do not satisfy "
                "SharpeMetricReadinessGate or benchmark distribution-evidence rules "
                "and must never enter an existing performance ledger."
            ),
            **{field: False for field in FIXED_FALSE},
        }

    def result_sha256(self) -> str:
        return _sha256(self.material())


def _load_from_signals() -> Any:
    """Import VectorBT lazily and expose exactly one entry point.

    Importing VectorBT transitively imports `requests`, `urllib3`, `socket` and
    `schedule` and binds broker/data classes onto the top-level module.  The
    pilot cannot prevent those imports, so it (a) defers them until a run is
    actually requested and (b) never resolves any attribute other than
    `Portfolio.from_signals`.
    """
    import vectorbt

    installed = str(getattr(vectorbt, "__version__", "")).strip()
    if installed != VECTORBT_PINNED_VERSION:
        raise ValueError(
            f"pilot is pinned to VectorBT {VECTORBT_PINNED_VERSION}; found {installed!r}"
        )
    return vectorbt.Portfolio.from_signals


def synthetic_bar_content_sha256(bars: Iterable[SyntheticPilotBar]) -> str:
    """Canonical content digest of an exact synthetic bar sequence.

    `SyntheticPilotAttestation.content_sha256` must equal this value for the
    bars actually supplied, so a declared data version cannot be attached to
    different content.
    """
    rows = []
    for item in bars:
        if type(item) is not SyntheticPilotBar:
            raise ValueError("the pilot accepts only SyntheticPilotBar rows")
        rows.append(item.material())
    return _sha256(rows)


def _validate_bars(bars: Sequence[SyntheticPilotBar]) -> tuple[SyntheticPilotBar, ...]:
    if len(bars) < 2:
        raise ValueError("the pilot requires at least two synthetic sessions")
    symbols = {bar.symbol for bar in bars}
    if len(symbols) != 1:
        raise ValueError("the pilot supports exactly one instrument per run")
    ordered = tuple(bars)
    for left, right in zip(ordered, ordered[1:]):
        if left.open_at >= right.open_at:
            raise ValueError("synthetic bars must be supplied in strict chronological order")
        if left.close_at >= right.open_at:
            raise ValueError("synthetic bars overlap")
        if left.available_at >= right.open_at:
            raise ValueError("a bar was not available strictly before the next session open")
    if len({(bar.open_at, bar.close_at) for bar in ordered}) != len(ordered):
        raise ValueError("synthetic sessions must be unique")
    spacings = {
        (right.open_at - left.open_at).total_seconds() for left, right in zip(ordered, ordered[1:])
    }
    if len(spacings) != 1:
        raise ValueError(
            "synthetic sessions must be evenly spaced; a skipped or ambiguous session "
            "cannot be given a daily frequency"
        )
    if spacings != {DAILY_SESSION_SPACING_SECONDS}:
        raise ValueError(
            "synthetic sessions must be spaced exactly one day apart because the "
            "pilot records a 1D return frequency and 252-day Sharpe annualization"
        )
    return ordered


def _resolve_signals(
    bars: Sequence[SyntheticPilotBar],
    signals: Sequence[SyntheticPilotSignal],
) -> dict[int, SyntheticPilotSignal]:
    """Map each signal to exactly the next bar open; reject everything else."""
    symbol = bars[0].symbol
    by_close = {bar.close_at: index for index, bar in enumerate(bars)}
    resolved: dict[int, SyntheticPilotSignal] = {}
    seen: set[tuple[str, datetime]] = set()
    previous_close: datetime | None = None
    for signal in signals:
        if signal.symbol != symbol:
            raise ValueError("signal instrument does not match the pilot bars")
        key = (signal.symbol, signal.bar_close_at)
        if key in seen:
            raise ValueError("duplicate signals bound to one bar close")
        seen.add(key)
        if previous_close is not None and signal.bar_close_at <= previous_close:
            raise ValueError(
                "signals must be supplied in strict chronological bar-close order"
            )
        previous_close = signal.bar_close_at
        index = by_close.get(signal.bar_close_at)
        if index is None:
            raise ValueError("signal is not bound to an exact synthetic bar close")
        if signal.available_at < bars[index].close_at:
            raise ValueError(
                "signal claims availability before its bound bar close; this is future data"
            )
        if signal.available_at < bars[index].available_at:
            raise ValueError(
                "signal claims use before its bound bar became available; this is future data"
            )
        if index + 1 >= len(bars):
            raise ValueError(
                "signal bound to the final bar close has no next bar open to execute at"
            )
        if signal.available_at >= bars[index + 1].open_at:
            raise ValueError(
                "signal was not available strictly before the next bar open and cannot execute"
            )
        # Execution index is the exact next bar.  Same-bar execution is
        # structurally impossible: the signal index is never used as a fill
        # index anywhere in this adapter.
        resolved[index + 1] = signal
    return resolved


class VectorBTPilotAdapter:
    """Run one synthetic, long-only, single-instrument VectorBT pilot."""

    schema_version = SCHEMA_VERSION
    policy_version = POLICY_VERSION

    def __init__(self, *, assumptions: VectorBTPilotAssumptions) -> None:
        if type(assumptions) is not VectorBTPilotAssumptions:
            raise ValueError("pilot assumptions must be a VectorBTPilotAssumptions")
        self.assumptions = assumptions

    def run(
        self,
        *,
        bars: Iterable[SyntheticPilotBar],
        signals: Iterable[SyntheticPilotSignal],
        attestation: SyntheticPilotAttestation,
        strategy_version: str,
    ) -> VectorBTPilotResult:
        # A ReplayDataAttestation (or any subclass shim) is refused outright.
        if type(attestation) is not SyntheticPilotAttestation:
            raise ValueError(
                "the pilot accepts only a SyntheticPilotAttestation; authenticated "
                "replay attestations are not admissible pilot input"
            )
        resolved_bars = _validate_bars([self._require_bar(item) for item in bars])
        content_sha256 = synthetic_bar_content_sha256(resolved_bars)
        if content_sha256 != attestation.content_sha256:
            raise ValueError(
                "attestation content_sha256 does not match the supplied synthetic bars"
            )
        resolved_signals = _resolve_signals(
            resolved_bars, [self._require_signal(item) for item in signals]
        )
        version = _required(strategy_version, "strategy_version", 200)

        input_material = {
            "policy_version": POLICY_VERSION,
            "attestation": attestation.identity(),
            "assumptions": self.assumptions.material(),
            "strategy_version": version,
            "bars": [bar.material() for bar in resolved_bars],
            "signals": [
                {"execution_bar_index": index, **signal.material()}
                for index, signal in sorted(resolved_signals.items())
            ],
        }
        input_sha256 = _sha256(input_material)

        frame = self._simulate(resolved_bars, resolved_signals)
        return self._metrics(
            frame,
            bars=resolved_bars,
            attestation=attestation,
            strategy_version=version,
            input_sha256=input_sha256,
        )

    @staticmethod
    def _require_bar(value: Any) -> SyntheticPilotBar:
        if type(value) is not SyntheticPilotBar:
            raise ValueError("the pilot accepts only SyntheticPilotBar rows")
        return value

    @staticmethod
    def _require_signal(value: Any) -> SyntheticPilotSignal:
        if type(value) is not SyntheticPilotSignal:
            raise ValueError("the pilot accepts only SyntheticPilotSignal rows")
        return value

    def _simulate(
        self,
        bars: Sequence[SyntheticPilotBar],
        signals: Mapping[int, SyntheticPilotSignal],
    ) -> Any:
        import pandas as pd

        from_signals = _load_from_signals()
        index = pd.DatetimeIndex([bar.open_at for bar in bars], name="open_at")
        symbol = bars[0].symbol
        close = pd.Series([float(bar.close) for bar in bars], index=index, name=symbol)
        open_ = pd.Series([float(bar.open) for bar in bars], index=index, name=symbol)
        high = pd.Series([float(bar.high) for bar in bars], index=index, name=symbol)
        low = pd.Series([float(bar.low) for bar in bars], index=index, name=symbol)
        entries = pd.Series(False, index=index, name=symbol)
        exits = pd.Series(False, index=index, name=symbol)
        for position, signal in signals.items():
            if signal.action == ACTION_ENTER_LONG:
                entries.iloc[position] = True
            else:
                exits.iloc[position] = True

        assumptions = self.assumptions
        bps = Decimal("10000")
        return from_signals(
            close=close,
            entries=entries,
            exits=exits,
            # Execution happens at the current bar's open.  Signals were already
            # shifted to the exact next bar, so no same-bar fill is reachable.
            price=open_,
            # Valuation uses the prior bar close, which is known at signal time.
            # This is the leak VectorBT's default (current close) would create.
            val_price=close.shift(1),
            ffill_val_price=True,
            update_value=False,
            open=open_,
            high=high,
            low=low,
            size=float(assumptions.maximum_position_fraction),
            size_type="percent",
            direction="longonly",
            accumulate=False,
            fees=float(assumptions.fees_bps_per_side / bps),
            fixed_fees=0.0,
            slippage=float(assumptions.slippage_bps_per_side / bps),
            init_cash=float(assumptions.initial_cash),
            cash_sharing=False,
            group_by=False,
            call_seq="default",
            lock_cash=True,
            allow_partial=False,
            reject_prob=0.0,
            raise_reject=True,
            log=False,
            use_stops=True,
            sl_stop=float(assumptions.hard_stop_loss_fraction),
            sl_trail=False,
            tp_stop=None,
            stop_entry_price="fillprice",
            # A close-price stop can breach intrabar, recover, and then be
            # filled optimistically at the close.  StopMarket instead fills at
            # the stop price (plus adverse slippage), or at the current open
            # after an adverse gap.  VectorBT processes only one signal per
            # asset/bar, so this stop becomes active on the first bar after an
            # entry; that limitation is recorded explicitly in assumptions.
            stop_exit_price="stopmarket",
            freq=DIAGNOSTIC_RETURN_FREQUENCY,
            seed=0,
            attach_call_seq=False,
        )

    def _metrics(
        self,
        portfolio: Any,
        *,
        bars: Sequence[SyntheticPilotBar],
        attestation: SyntheticPilotAttestation,
        strategy_version: str,
        input_sha256: str,
    ) -> VectorBTPilotResult:
        trades = portfolio.trades
        closed_trades = trades.closed
        orders = portfolio.orders
        trade_count = int(closed_trades.count())
        open_trade_count = int(trades.open.count())
        order_count = int(orders.count())

        total_return = quantize(portfolio.total_return(), "total_return")
        # Max drawdown is reported by VectorBT as a non-positive number; the
        # pilot reports its magnitude.
        maximum_drawdown = quantize(
            abs(float(portfolio.max_drawdown())), "maximum_drawdown"
        )
        # Every argument of the recorded Sharpe definition is passed explicitly.
        # Relying on VectorBT's defaults would silently annualize by 365 days.
        sharpe_ratio = self._quantized_or_none(
            portfolio.sharpe_ratio(
                freq=DIAGNOSTIC_RETURN_FREQUENCY,
                year_freq=DIAGNOSTIC_YEAR_FREQUENCY,
                risk_free=float(DIAGNOSTIC_RISK_FREE_RATE),
                ddof=DIAGNOSTIC_SAMPLE_DDOF,
            ),
            "sharpe_ratio",
        )
        win_rate = self._quantized_or_none(
            closed_trades.win_rate() if trade_count else float("nan"), "win_rate"
        )

        # Same-window, cost-free buy-and-hold of the same synthetic instrument.
        # Computed here rather than taken from VectorBT so its definition is
        # explicit and cannot drift.
        benchmark_total_return = quantize(
            bars[-1].close / bars[0].close - Decimal("1"), "benchmark_total_return"
        )
        excess = quantize(
            total_return - benchmark_total_return, "excess_return_vs_benchmark"
        )

        turnover = quantize(
            self._filled_notional(orders) / self.assumptions.initial_cash, "turnover"
        )

        return VectorBTPilotResult(
            schema_version=SCHEMA_VERSION,
            policy_version=POLICY_VERSION,
            symbol=bars[0].symbol,
            session_count=len(bars),
            evaluation_start=bars[0].open_at,
            evaluation_end=bars[-1].close_at,
            data_version=attestation.data_version,
            data_content_sha256=attestation.content_sha256,
            attestation_sha256=attestation.identity_sha256(),
            assumptions_version=ASSUMPTIONS_VERSION,
            assumptions_sha256=self.assumptions.identity_sha256(),
            assumptions=tuple(sorted(self.assumptions.material().items())),
            input_sha256=input_sha256,
            strategy_version=strategy_version,
            vectorbt_version=VECTORBT_PINNED_VERSION,
            vectorbt_licence=VECTORBT_LICENCE,
            quantize_decimal_places=QUANTIZE_DECIMAL_PLACES,
            total_return=total_return,
            benchmark_total_return=benchmark_total_return,
            excess_return_vs_benchmark=excess,
            sharpe_ratio=sharpe_ratio,
            maximum_drawdown=maximum_drawdown,
            win_rate=win_rate,
            turnover=turnover,
            trade_count=trade_count,
            open_trade_count=open_trade_count,
            order_count=order_count,
        )

    @staticmethod
    def _quantized_or_none(value: Any, name: str) -> Decimal | None:
        resolved = float(value)
        return None if not math.isfinite(resolved) else quantize(resolved, name)

    @staticmethod
    def _filled_notional(orders: Any) -> Decimal:
        records = orders.records
        total = Decimal("0")
        for size, price in zip(records["size"], records["price"]):
            total += Decimal(str(float(size))) * Decimal(str(float(price)))
        return total
