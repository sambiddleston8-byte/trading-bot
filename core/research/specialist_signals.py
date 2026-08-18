"""Normalized specialist outputs and the fixed executive aggregation contract."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext
import hashlib
import json
from typing import Any, Mapping

import pandas as pd

from core.features.pit_feature_contract import DECIMAL_CONTEXT


MIN_SCORE = Decimal("-1")
MAX_SCORE = Decimal("1")
VALID_SPECIALIST_STATUSES = frozenset(
    {"ACTIVE", "NEUTRAL", "ABSTAIN", "STALE", "INVALID"}
)
VALID_RISK_STATUSES = frozenset({"VALID", "STALE", "INVALID"})
VALID_ACTIONS = frozenset({"ENTER_LONG", "HOLD", "REDUCE", "EXIT", "CASH"})
WEIGHT_QUANTUM = Decimal("0.0001")


def _time(value: str | datetime, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error
    if not isinstance(parsed, datetime) or parsed.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _score(value: Any, name: str = "score") -> Decimal:
    try:
        resolved = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{name} must be decimal-compatible") from error
    if not resolved.is_finite() or not MIN_SCORE <= resolved <= MAX_SCORE:
        raise ValueError(f"{name} must be finite and within [-1, 1]")
    return resolved


def _unit_interval(value: Any, name: str) -> Decimal:
    try:
        resolved = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{name} must be decimal-compatible") from error
    if not resolved.is_finite() or not Decimal("0") <= resolved <= Decimal("1"):
        raise ValueError(f"{name} must be finite and within [0, 1]")
    return resolved


def _sha256(value: str, name: str) -> str:
    resolved = str(value).strip().lower()
    if len(resolved) != 64 or any(
        character not in "0123456789abcdef" for character in resolved
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return resolved


def _symbol(value: str) -> str:
    resolved = str(value).strip().upper()
    if not resolved or len(resolved) > 64:
        raise ValueError("symbol must be a non-empty normalized identifier")
    return resolved


def _decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


@dataclass(frozen=True)
class SpecialistSignal:
    """One isolated sub-bot opinion at an explicit historical decision time."""

    specialist_id: str
    specialist_version: str
    symbol: str
    decision_at: str
    score: Decimal
    evidence_count: int
    evidence_sha256: str
    reason: str
    horizon: str = "ONE_SESSION"
    label_horizon_bars: int = 1
    confidence: Decimal = Decimal("1")
    coverage: Decimal = Decimal("1")
    status: str = "ACTIVE"
    maximum_input_available_at: str | None = None
    model_version: str = "DETERMINISTIC"
    feature_version: str = "DETERMINISTIC"
    reason_codes: tuple[str, ...] = ()
    tick_vector_parity: bool = True
    evidence_hash_continuity: bool = True

    def __post_init__(self) -> None:
        if not self.specialist_id or not self.specialist_version:
            raise ValueError("specialist identity is required")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        decision = _time(self.decision_at, "decision_at")
        object.__setattr__(self, "decision_at", decision.isoformat())
        object.__setattr__(self, "score", _score(self.score))
        if not isinstance(self.evidence_count, int) or self.evidence_count < 0:
            raise ValueError("evidence_count must be a non-negative integer")
        object.__setattr__(
            self, "evidence_sha256", _sha256(self.evidence_sha256, "evidence_sha256")
        )
        if not self.reason:
            raise ValueError("specialist reason is required")
        if not self.horizon:
            raise ValueError("specialist horizon is required")
        if type(self.label_horizon_bars) is not int or self.label_horizon_bars < 1:
            raise ValueError("label_horizon_bars must be a positive integer")
        object.__setattr__(
            self, "confidence", _unit_interval(self.confidence, "confidence")
        )
        object.__setattr__(self, "coverage", _unit_interval(self.coverage, "coverage"))
        if self.status not in VALID_SPECIALIST_STATUSES:
            raise ValueError("specialist status is unsupported")
        if self.status in {"NEUTRAL", "ABSTAIN", "STALE", "INVALID"} and self.score != 0:
            raise ValueError("non-active specialist status requires a zero score")
        available = _time(
            self.maximum_input_available_at or decision,
            "maximum_input_available_at",
        )
        if available > decision:
            raise ValueError("specialist input is unavailable at decision_at")
        object.__setattr__(self, "maximum_input_available_at", available.isoformat())
        if not self.model_version or not self.feature_version:
            raise ValueError("specialist model and feature versions are required")
        if (
            type(self.tick_vector_parity) is not bool
            or type(self.evidence_hash_continuity) is not bool
        ):
            raise ValueError("specialist health observables must be boolean")
        reasons = self.reason_codes or (self.reason,)
        reasons = tuple(str(reason).strip() for reason in reasons)
        if any(not reason for reason in reasons):
            raise ValueError("specialist reason codes cannot be empty")
        object.__setattr__(self, "reason_codes", reasons)

    @property
    def directional_score(self) -> Decimal:
        return self.score

    def as_dict(self) -> dict[str, Any]:
        return {
            "specialist_id": self.specialist_id,
            "specialist_version": self.specialist_version,
            "symbol": self.symbol,
            "decision_at": _time(self.decision_at, "decision_at").isoformat(),
            "directional_score": _decimal(self.score),
            "confidence": _decimal(self.confidence),
            "coverage": _decimal(self.coverage),
            "status": self.status,
            "horizon": self.horizon,
            "label_horizon_bars": self.label_horizon_bars,
            "maximum_input_available_at": self.maximum_input_available_at,
            "evidence_count": self.evidence_count,
            "evidence_sha256": self.evidence_sha256,
            "reason": self.reason,
            "reason_codes": list(self.reason_codes),
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "tick_vector_parity": self.tick_vector_parity,
            "evidence_hash_continuity": self.evidence_hash_continuity,
        }


@dataclass(frozen=True)
class RiskEnvelope:
    """PIT-bound constraints supplied separately from every alpha signal."""

    version: str
    decision_at: str
    status: str
    regime: str
    new_entries_allowed: bool
    forced_exit: bool
    gross_exposure_cap: Decimal
    symbol_exposure_cap: Decimal
    position_size_multiplier: Decimal
    maximum_input_available_at: str
    evidence_sha256: str
    reason_codes: tuple[str, ...]
    envelope_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.version or not self.regime:
            raise ValueError("risk version and regime are required")
        decision = _time(self.decision_at, "decision_at")
        available = _time(
            self.maximum_input_available_at, "maximum_input_available_at"
        )
        if available > decision:
            raise ValueError("risk input is unavailable at decision_at")
        object.__setattr__(self, "decision_at", decision.isoformat())
        object.__setattr__(self, "maximum_input_available_at", available.isoformat())
        if self.status not in VALID_RISK_STATUSES:
            raise ValueError("risk status is unsupported")
        if type(self.new_entries_allowed) is not bool or type(self.forced_exit) is not bool:
            raise ValueError("risk entry and forced-exit flags must be boolean")
        if self.status != "VALID" and (self.new_entries_allowed or self.forced_exit):
            raise ValueError("stale or invalid risk cannot originate an instruction")
        if self.forced_exit and self.new_entries_allowed:
            raise ValueError("forced-exit risk cannot allow new entries")
        object.__setattr__(
            self,
            "gross_exposure_cap",
            _unit_interval(self.gross_exposure_cap, "gross_exposure_cap"),
        )
        object.__setattr__(
            self,
            "symbol_exposure_cap",
            _unit_interval(self.symbol_exposure_cap, "symbol_exposure_cap"),
        )
        object.__setattr__(
            self,
            "position_size_multiplier",
            _unit_interval(
                self.position_size_multiplier, "position_size_multiplier"
            ),
        )
        if self.symbol_exposure_cap > self.gross_exposure_cap:
            raise ValueError("risk symbol cap cannot exceed its gross cap")
        object.__setattr__(
            self, "evidence_sha256", _sha256(self.evidence_sha256, "evidence_sha256")
        )
        reasons = tuple(str(reason).strip() for reason in self.reason_codes)
        if not reasons or any(not reason for reason in reasons):
            raise ValueError("risk reason codes are required")
        object.__setattr__(self, "reason_codes", reasons)
        material = self._material()
        object.__setattr__(
            self,
            "envelope_sha256",
            hashlib.sha256(
                json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        )

    def _material(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "decision_at": self.decision_at,
            "status": self.status,
            "regime": self.regime,
            "new_entries_allowed": self.new_entries_allowed,
            "forced_exit": self.forced_exit,
            "gross_exposure_cap": _decimal(self.gross_exposure_cap),
            "symbol_exposure_cap": _decimal(self.symbol_exposure_cap),
            "position_size_multiplier": _decimal(self.position_size_multiplier),
            "maximum_input_available_at": self.maximum_input_available_at,
            "evidence_sha256": self.evidence_sha256,
            "reason_codes": list(self.reason_codes),
        }

    def as_dict(self) -> dict[str, Any]:
        return {**self._material(), "envelope_sha256": self.envelope_sha256}


@dataclass(frozen=True)
class StandingStopInstruction:
    reference_price: Decimal
    trigger_rule: str
    order_type: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        price = Decimal(str(self.reference_price))
        if not price.is_finite() or price <= 0:
            raise ValueError("standing-stop reference price must be positive")
        object.__setattr__(self, "reference_price", price)
        if not self.trigger_rule or self.order_type != "STOP_MARKET":
            raise ValueError("standing-stop trigger and STOP_MARKET order type are required")
        prefix = "LAST_PRICE_LTE_"
        if not self.trigger_rule.startswith(prefix):
            raise ValueError("standing-stop trigger rule is unsupported")
        try:
            trigger = Decimal(self.trigger_rule[len(prefix) :])
        except Exception as error:
            raise ValueError("standing-stop trigger price must be decimal-compatible") from error
        if not trigger.is_finite() or trigger <= 0 or trigger >= price:
            raise ValueError(
                "standing-stop trigger price must be positive and below reference price"
            )
        object.__setattr__(
            self, "evidence_sha256", _sha256(self.evidence_sha256, "evidence_sha256")
        )

    @property
    def trigger_price(self) -> Decimal:
        return Decimal(self.trigger_rule.removeprefix("LAST_PRICE_LTE_"))

    def as_dict(self) -> dict[str, str]:
        return {
            "reference_price": _decimal(self.reference_price),
            "trigger_rule": self.trigger_rule,
            "order_type": self.order_type,
            "evidence_sha256": self.evidence_sha256,
        }


@dataclass(frozen=True)
class SymbolIntent:
    symbol: str
    action: str
    current_weight: Decimal
    target_weight: Decimal
    conviction: Decimal
    participation: Decimal
    consensus: Decimal | None
    disagreement: Decimal | None
    risk_multiplier: Decimal
    specialist_evidence_sha256: tuple[str, ...]
    reason_codes: tuple[str, ...]
    standing_stop: StandingStopInstruction | None = None
    specialist_health: tuple[tuple[str, bool], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        if self.action not in VALID_ACTIONS:
            raise ValueError("symbol intent action is unsupported")
        current = _unit_interval(self.current_weight, "current_weight")
        target = _unit_interval(self.target_weight, "target_weight")
        object.__setattr__(self, "current_weight", current)
        object.__setattr__(self, "target_weight", target)
        object.__setattr__(self, "conviction", _score(self.conviction, "conviction"))
        object.__setattr__(
            self, "participation", _unit_interval(self.participation, "participation")
        )
        if (self.consensus is None) != (self.disagreement is None):
            raise ValueError("consensus and disagreement must both be present or absent")
        if self.consensus is not None:
            object.__setattr__(self, "consensus", _score(self.consensus, "consensus"))
            object.__setattr__(
                self,
                "disagreement",
                _unit_interval(self.disagreement, "disagreement"),
            )
        object.__setattr__(
            self,
            "risk_multiplier",
            _unit_interval(self.risk_multiplier, "risk_multiplier"),
        )
        expected = {
            "ENTER_LONG": target > current,
            "HOLD": target == current,
            "REDUCE": Decimal("0") < target < current,
            "EXIT": current > 0 and target == 0,
            "CASH": current == 0 and target == 0,
        }[self.action]
        if not expected:
            raise ValueError("symbol action is inconsistent with current/target weights")
        evidence = tuple(
            _sha256(value, "specialist_evidence_sha256")
            for value in self.specialist_evidence_sha256
        )
        object.__setattr__(self, "specialist_evidence_sha256", evidence)
        reasons = tuple(str(reason).strip() for reason in self.reason_codes)
        if not reasons or any(not reason for reason in reasons):
            raise ValueError("symbol intent reason codes are required")
        object.__setattr__(self, "reason_codes", reasons)
        if self.action == "ENTER_LONG" and self.standing_stop is None:
            raise ValueError("new long exposure requires a standing stop")
        health = tuple(sorted(self.specialist_health))
        if len({name for name, _ in health}) != len(health) or any(
            not name or type(value) is not bool for name, value in health
        ):
            raise ValueError("specialist health must be uniquely named and boolean")
        object.__setattr__(self, "specialist_health", health)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "current_weight": _decimal(self.current_weight),
            "target_weight": _decimal(self.target_weight),
            "conviction": _decimal(self.conviction),
            "participation": _decimal(self.participation),
            "consensus": None if self.consensus is None else _decimal(self.consensus),
            "disagreement": (
                None if self.disagreement is None else _decimal(self.disagreement)
            ),
            "risk_multiplier": _decimal(self.risk_multiplier),
            "specialist_evidence_sha256": list(self.specialist_evidence_sha256),
            "reason_codes": list(self.reason_codes),
            "standing_stop": (
                None if self.standing_stop is None else self.standing_stop.as_dict()
            ),
            "specialist_health": [list(value) for value in self.specialist_health],
        }


@dataclass(frozen=True)
class ExecutivePortfolioIntent:
    version: str
    decision_at: str
    risk_envelope_sha256: str
    gross_exposure_cap: Decimal
    symbol_intents: tuple[SymbolIntent, ...]
    reason_codes: tuple[str, ...]
    intent_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("executive intent version is required")
        decision = _time(self.decision_at, "decision_at")
        object.__setattr__(self, "decision_at", decision.isoformat())
        object.__setattr__(
            self,
            "risk_envelope_sha256",
            _sha256(self.risk_envelope_sha256, "risk_envelope_sha256"),
        )
        cap = _unit_interval(self.gross_exposure_cap, "gross_exposure_cap")
        object.__setattr__(self, "gross_exposure_cap", cap)
        intents = tuple(sorted(self.symbol_intents, key=lambda value: value.symbol))
        if len({intent.symbol for intent in intents}) != len(intents):
            raise ValueError("executive intent contains duplicate symbols")
        target_total = sum(
            (intent.target_weight for intent in intents), Decimal("0")
        )
        if target_total > cap and any(
            intent.target_weight > intent.current_weight for intent in intents
        ):
            raise ValueError("risk-increasing executive targets exceed the gross cap")
        object.__setattr__(self, "symbol_intents", intents)
        reasons = tuple(str(reason).strip() for reason in self.reason_codes)
        if not reasons or any(not reason for reason in reasons):
            raise ValueError("executive intent reason codes are required")
        object.__setattr__(self, "reason_codes", reasons)
        material = {
            "version": self.version,
            "decision_at": self.decision_at,
            "risk_envelope_sha256": self.risk_envelope_sha256,
            "gross_exposure_cap": _decimal(cap),
            "symbol_intents": [intent.as_dict() for intent in intents],
            "reason_codes": list(reasons),
        }
        object.__setattr__(
            self,
            "intent_sha256",
            hashlib.sha256(
                json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "decision_at": self.decision_at,
            "risk_envelope_sha256": self.risk_envelope_sha256,
            "gross_exposure_cap": _decimal(self.gross_exposure_cap),
            "symbol_intents": [intent.as_dict() for intent in self.symbol_intents],
            "reason_codes": list(self.reason_codes),
            "intent_sha256": self.intent_sha256,
        }


class LegacyResearchWeightedAggregatorBot:
    """Frozen three-vote TRAIN baseline retained only for parity comparisons."""

    VERSION = "fixed-three-specialist-weighted-aggregator-v1"
    REQUIRED_SPECIALISTS = ("TECHNICAL", "RISK_REGIME", "SEC_FORM4_INSIDER")
    SPECIALIST_VERSIONS = {
        "TECHNICAL": "pit-sma-momentum-breadth-specialist-v1",
        "RISK_REGIME": "pit-atr-percentile-risk-regime-specialist-v1",
        "SEC_FORM4_INSIDER": "sec-form4-cluster-role-intensity-v2",
    }
    WEIGHTS = {
        "TECHNICAL": Decimal("0.55"),
        "RISK_REGIME": Decimal("0.25"),
        "SEC_FORM4_INSIDER": Decimal("0.20"),
    }
    ENTRY_THRESHOLD = Decimal("0.70")
    RESEARCH_ONLY = True
    PROMOTABLE = False

    def __init__(self) -> None:
        if sum(self.WEIGHTS.values(), Decimal("0")) != Decimal("1"):
            raise ValueError("executive aggregator weights must sum to one")

    def aggregate(
        self, signals: Mapping[str, SpecialistSignal], *, decision_at: str | datetime
    ) -> SpecialistSignal:
        if set(signals) != set(self.REQUIRED_SPECIALISTS):
            raise ValueError("executive aggregator requires the exact specialist set")
        if any(
            signals[name].specialist_id != name
            or signals[name].specialist_version != self.SPECIALIST_VERSIONS[name]
            for name in self.REQUIRED_SPECIALISTS
        ):
            raise ValueError("executive aggregator specialist identity/version mismatch")
        decision = _time(decision_at, "decision_at")
        symbols = {signal.symbol for signal in signals.values()}
        decisions = {
            _time(signal.decision_at, "signal decision_at")
            for signal in signals.values()
        }
        if len(symbols) != 1 or decisions != {decision}:
            raise ValueError("specialist outputs are not symbol/time aligned")
        combined = sum(
            (self.WEIGHTS[name] * signals[name].score for name in self.REQUIRED_SPECIALISTS),
            Decimal("0"),
        )
        material = {
            name: signals[name].as_dict() for name in self.REQUIRED_SPECIALISTS
        }
        evidence_sha256 = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return SpecialistSignal(
            specialist_id="LEGACY_RESEARCH_THREE_VOTE_BASELINE",
            specialist_version=self.VERSION,
            symbol=next(iter(symbols)),
            decision_at=decision.isoformat(),
            score=combined,
            evidence_count=sum(signal.evidence_count for signal in signals.values()),
            evidence_sha256=evidence_sha256,
            reason="RESEARCH_ONLY_LEGACY_THREE_VOTE_RISK_AS_ALPHA",
        )

    def aggregate_frame(self, signals: pd.DataFrame) -> pd.DataFrame:
        """Ordered batch aggregation delegated to the authoritative tick rule."""
        required = {"symbol", "decision_at", *self.REQUIRED_SPECIALISTS}
        if set(signals.columns) != required:
            raise ValueError("executive signal frame has an unsupported schema")
        rows = []
        for row in signals.itertuples(index=False):
            specialist_signals = {
                name: getattr(row, name) for name in self.REQUIRED_SPECIALISTS
            }
            if any(
                not isinstance(signal, SpecialistSignal)
                for signal in specialist_signals.values()
            ):
                raise ValueError("executive batch requires SpecialistSignal values")
            aggregate = self.aggregate(
                specialist_signals, decision_at=row.decision_at
            )
            if aggregate.symbol != str(row.symbol).strip().upper():
                raise ValueError("executive batch symbol differs from its signals")
            rows.append(
                {
                    "symbol": aggregate.symbol,
                    "decision_at": aggregate.decision_at,
                    "score": _decimal(aggregate.score),
                }
            )
        return pd.DataFrame(rows, columns=("symbol", "decision_at", "score"))


class ExecutiveAggregatorBot:
    """The sole portfolio authority over independent alpha and separate risk."""

    VERSION = "ultimate-executive-portfolio-v1"
    REQUIRED_SPECIALISTS = ("TECHNICAL", "SEC_FORM4_INSIDER")
    SPECIALIST_VERSIONS = {
        "TECHNICAL": "pit-sma-momentum-breadth-specialist-v1",
        "SEC_FORM4_INSIDER": "sec-form4-cluster-role-intensity-v2",
    }
    WEIGHTS = {
        "TECHNICAL": Decimal("0.50"),
        "SEC_FORM4_INSIDER": Decimal("0.50"),
    }
    MINIMUM_SPECIALISTS = 2
    MINIMUM_PARTICIPATION = Decimal("0.50")
    ENTRY_THRESHOLD = Decimal("0.20")
    EXIT_THRESHOLD = Decimal("0.05")
    MAX_POSITION_WEIGHT = Decimal("0.10")
    CONVICTION_TO_GROSS = Decimal("1")

    def __init__(self) -> None:
        if sum(self.WEIGHTS.values(), Decimal("0")) != Decimal("1"):
            raise ValueError("executive alpha weights must sum to one")
        if self.EXIT_THRESHOLD >= self.ENTRY_THRESHOLD:
            raise ValueError("executive exit threshold must be below entry threshold")
        if self.CONVICTION_TO_GROSS not in {
            Decimal("1"),
            Decimal("2"),
            Decimal("4"),
            Decimal("8"),
        }:
            raise ValueError("executive conviction multiplier is not preregistered")

    @staticmethod
    def _action(current: Decimal, target: Decimal) -> str:
        if current == 0 and target == 0:
            return "CASH"
        if current > 0 and target == 0:
            return "EXIT"
        if target > current:
            return "ENTER_LONG"
        if target < current:
            return "REDUCE"
        return "HOLD"

    def _aggregate_symbol(
        self,
        symbol: str,
        signals: Mapping[str, SpecialistSignal],
        *,
        decision: datetime,
    ) -> tuple[
        Decimal | None,
        Decimal | None,
        Decimal,
        Decimal,
        tuple[str, ...],
        tuple[str, ...],
        tuple[tuple[str, bool], ...],
    ]:
        if set(signals) != set(self.REQUIRED_SPECIALISTS):
            return (
                None,
                None,
                Decimal("0"),
                Decimal("0"),
                (),
                ("REQUIRED_SPECIALIST_SET_INCOMPLETE", "NO_QUORUM"),
                tuple((name, False) for name in self.REQUIRED_SPECIALISTS),
            )
        for name in self.REQUIRED_SPECIALISTS:
            signal = signals[name]
            if (
                signal.specialist_id != name
                or signal.specialist_version != self.SPECIALIST_VERSIONS[name]
                or signal.symbol != symbol
                or _time(signal.decision_at, "signal decision_at") != decision
                or _time(
                    signal.maximum_input_available_at,
                    "maximum_input_available_at",
                )
                > decision
            ):
                return (
                    None,
                    None,
                    Decimal("0"),
                    Decimal("0"),
                    (),
                    ("SPECIALIST_IDENTITY_OR_ALIGNMENT_FAILURE", "NO_QUORUM"),
                    tuple((value, False) for value in self.REQUIRED_SPECIALISTS),
                )

        healthy = {
            name: (
                signals[name].status in {"ACTIVE", "NEUTRAL"}
                and signals[name].tick_vector_parity
                and signals[name].evidence_hash_continuity
            )
            for name in self.REQUIRED_SPECIALISTS
        }
        health = tuple((name, healthy[name]) for name in self.REQUIRED_SPECIALISTS)
        evidence = tuple(
            signals[name].evidence_sha256 for name in self.REQUIRED_SPECIALISTS
        )
        with localcontext(DECIMAL_CONTEXT):
            effective = {
                name: (
                    self.WEIGHTS[name]
                    * signals[name].confidence
                    * signals[name].coverage
                    if healthy[name]
                    else Decimal("0")
                )
                for name in self.REQUIRED_SPECIALISTS
            }
            denominator = sum(effective.values(), Decimal("0"))
            participation = denominator / sum(
                self.WEIGHTS.values(), Decimal("0")
            )
            eligible_count = sum(value > 0 for value in effective.values())
            if (
                denominator == 0
                or eligible_count < self.MINIMUM_SPECIALISTS
                or participation < self.MINIMUM_PARTICIPATION
            ):
                return (
                    None,
                    None,
                    participation,
                    Decimal("0"),
                    evidence,
                    ("NO_QUORUM",),
                    health,
                )
            consensus = sum(
                (
                    effective[name] * signals[name].directional_score
                    for name in self.REQUIRED_SPECIALISTS
                ),
                Decimal("0"),
            ) / denominator
            disagreement = sum(
                (
                    effective[name]
                    * abs(signals[name].directional_score - consensus)
                    for name in self.REQUIRED_SPECIALISTS
                ),
                Decimal("0"),
            ) / denominator
            conviction = consensus * participation * (
                Decimal("1") - disagreement
            )
        return (
            consensus,
            disagreement,
            participation,
            conviction,
            evidence,
            ("QUORUM_MET",),
            health,
        )

    def decide(
        self,
        signals_by_symbol: Mapping[str, Mapping[str, SpecialistSignal]],
        *,
        risk: RiskEnvelope,
        current_weights: Mapping[str, Decimal],
        eligible_symbols: tuple[str, ...],
        standing_stops: Mapping[str, StandingStopInstruction],
        decision_at: str | datetime,
    ) -> ExecutivePortfolioIntent:
        """Return one immutable portfolio intent; never return an order."""
        decision = _time(decision_at, "decision_at")
        if _time(risk.decision_at, "risk decision_at") != decision:
            raise ValueError("risk envelope is not aligned to decision_at")
        eligible = {_symbol(symbol) for symbol in eligible_symbols}
        current = {
            _symbol(symbol): _unit_interval(weight, "current_weight")
            for symbol, weight in current_weights.items()
        }
        with localcontext(DECIMAL_CONTEXT):
            current_compare = {
                symbol: weight.quantize(
                    WEIGHT_QUANTUM, rounding=ROUND_HALF_EVEN
                )
                for symbol, weight in current.items()
            }
        signal_symbols = {_symbol(symbol) for symbol in signals_by_symbol}
        dropped_signal_symbols = signal_symbols - eligible
        symbols = sorted(
            eligible | {symbol for symbol, weight in current.items() if weight > 0}
        )
        stops = {_symbol(symbol): stop for symbol, stop in standing_stops.items()}

        provisional: list[dict[str, Any]] = []
        for symbol in symbols:
            current_weight = current.get(symbol, Decimal("0"))
            if symbol not in eligible and risk.status != "VALID":
                provisional.append(
                    {
                        "symbol": symbol,
                        "current": current_weight,
                        "target": current_weight,
                        "conviction": Decimal("0"),
                        "participation": Decimal("0"),
                        "consensus": None,
                        "disagreement": None,
                        "evidence": (),
                        "reasons": (
                            "UNIVERSE_EXIT_DEFERRED",
                            "RISK_STALE",
                            "HOLD_CURRENT_WEIGHT",
                        ),
                        "health": (),
                    }
                )
                continue
            if symbol not in eligible:
                provisional.append(
                    {
                        "symbol": symbol,
                        "current": current_weight,
                        "target": Decimal("0"),
                        "conviction": Decimal("0"),
                        "participation": Decimal("0"),
                        "consensus": None,
                        "disagreement": None,
                        "evidence": (),
                        "reasons": ("UNIVERSE_EXIT",),
                        "health": (),
                    }
                )
                continue

            aggregate = self._aggregate_symbol(
                symbol,
                signals_by_symbol.get(symbol, {}),
                decision=decision,
            )
            (
                consensus,
                disagreement,
                participation,
                conviction,
                evidence,
                reasons,
                health,
            ) = aggregate
            if risk.status != "VALID":
                target = current_weight
                reasons = (*reasons, "RISK_STALE", "HOLD_CURRENT_WEIGHT")
            elif risk.forced_exit:
                target = Decimal("0")
                reasons = (*reasons, "RISK_FORCED_EXIT")
            elif consensus is None:
                target = current_weight
                reasons = (*reasons, "HOLD_CURRENT_WEIGHT")
            elif conviction < self.EXIT_THRESHOLD:
                target = Decimal("0")
                reasons = (*reasons, "BELOW_EXIT_THRESHOLD")
            else:
                alpha_weight = min(
                    max(
                        conviction * self.CONVICTION_TO_GROSS,
                        Decimal("0"),
                    ),
                    Decimal("1"),
                ) * self.MAX_POSITION_WEIGHT
                if conviction < self.ENTRY_THRESHOLD:
                    target = min(current_weight, alpha_weight)
                    reasons = (*reasons, "HYSTERESIS_HOLD_OR_REDUCE")
                else:
                    target = alpha_weight
                    reasons = (*reasons, "ENTRY_THRESHOLD_MET")
                if not risk.new_entries_allowed:
                    target = min(current_weight, target)
                    reasons = (*reasons, "NEW_ENTRIES_BLOCKED")
            if risk.status == "VALID":
                target = min(target, risk.symbol_exposure_cap)
            provisional.append(
                {
                    "symbol": symbol,
                    "current": current_weight,
                    "target": target,
                    "conviction": conviction,
                    "participation": participation,
                    "consensus": consensus,
                    "disagreement": disagreement,
                    "evidence": evidence,
                    "reasons": reasons,
                    "health": health,
                }
            )

        gross_scale = Decimal("1")
        if risk.status == "VALID":
            with localcontext(DECIMAL_CONTEXT):
                positive_total = sum(
                    (row["target"] for row in provisional), Decimal("0")
                )
                gross_scale = (
                    min(Decimal("1"), risk.gross_exposure_cap / positive_total)
                    if positive_total > 0
                    else Decimal("1")
                )
                for row in provisional:
                    row["unquantized_target"] = (
                        row["target"]
                        * gross_scale
                        * risk.position_size_multiplier
                    )
                    row["target"] = row["unquantized_target"].quantize(
                        WEIGHT_QUANTUM, rounding=ROUND_FLOOR
                    )

        intents = []
        cap_binding = risk.status == "VALID" and gross_scale < Decimal("1")
        for row in provisional:
            compare = current_compare.get(row["symbol"], Decimal("0"))
            if (
                row.get("unquantized_target") == row["current"]
                or (
                    not cap_binding
                    and compare > 0
                    and row["target"] > 0
                    and abs(row["target"] - compare) < WEIGHT_QUANTUM
                )
            ):
                row["target"] = row["current"]
                row["reasons"] = (*row["reasons"], "SUB_QUANTUM_NO_OP")
            action = self._action(row["current"], row["target"])
            stop = stops.get(row["symbol"])
            if action == "ENTER_LONG" and stop is None:
                row["target"] = row["current"]
                action = self._action(row["current"], row["target"])
                row["reasons"] = (*row["reasons"], "STOP_UNAVAILABLE")
            try:
                intent = SymbolIntent(
                    symbol=row["symbol"],
                    action=action,
                    current_weight=row["current"],
                    target_weight=row["target"],
                    conviction=row["conviction"],
                    participation=row["participation"],
                    consensus=row["consensus"],
                    disagreement=row["disagreement"],
                    risk_multiplier=(
                        risk.position_size_multiplier
                        if risk.status == "VALID"
                        else Decimal("1")
                    ),
                    specialist_evidence_sha256=row["evidence"],
                    reason_codes=row["reasons"],
                    standing_stop=stop,
                    specialist_health=row["health"],
                )
            except ValueError:
                fallback_target = (
                    row["target"]
                    if action in {"REDUCE", "EXIT", "CASH"}
                    else row["current"]
                )
                fallback_action = self._action(row["current"], fallback_target)
                intent = SymbolIntent(
                    symbol=row["symbol"],
                    action=fallback_action,
                    current_weight=row["current"],
                    target_weight=fallback_target,
                    conviction=Decimal("0"),
                    participation=Decimal("0"),
                    consensus=None,
                    disagreement=None,
                    risk_multiplier=Decimal("1"),
                    specialist_evidence_sha256=(),
                    reason_codes=("SYMBOL_INTENT_INVALID",),
                    standing_stop=stops.get(row["symbol"]),
                    specialist_health=(),
                )
            intents.append(intent)
        return ExecutivePortfolioIntent(
            version=self.VERSION,
            decision_at=decision.isoformat(),
            risk_envelope_sha256=risk.envelope_sha256,
            gross_exposure_cap=risk.gross_exposure_cap,
            symbol_intents=tuple(intents),
            reason_codes=tuple(
                [
                    (
                        "RISK_ENVELOPE_APPLIED"
                        if risk.status == "VALID"
                        else "RISK_ENVELOPE_FAIL_CLOSED"
                    )
                ]
                + (["INELIGIBLE_SIGNALS_DROPPED"] if dropped_signal_symbols else [])
            ),
        )


class FundamentalResearchExecutiveAggregatorBot(ExecutiveAggregatorBot):
    """Stage-4 three-alpha candidate; research-only until TRAIN evidence admits it."""

    VERSION = "ultimate-executive-portfolio-fundamental-research-v1"
    REQUIRED_SPECIALISTS = (
        "TECHNICAL",
        "SEC_FORM4_INSIDER",
        "FUNDAMENTAL_VALUATION",
    )
    SPECIALIST_VERSIONS = {
        **ExecutiveAggregatorBot.SPECIALIST_VERSIONS,
        "FUNDAMENTAL_VALUATION": "fundamental-valuation-dispersion-v1",
    }
    WEIGHTS = {
        # Finite-decimal representation of the preregistered equal scheme.
        "TECHNICAL": Decimal("0.34"),
        "SEC_FORM4_INSIDER": Decimal("0.33"),
        "FUNDAMENTAL_VALUATION": Decimal("0.33"),
    }


class CatalystResearchExecutiveAggregatorBot(ExecutiveAggregatorBot):
    """Stage-4 Catalyst candidate; isolated from unregistered research slices."""

    VERSION = "ultimate-executive-portfolio-catalyst-research-v1"
    REQUIRED_SPECIALISTS = (
        "TECHNICAL",
        "SEC_FORM4_INSIDER",
        "CATALYST_EVENT",
    )
    SPECIALIST_VERSIONS = {
        **ExecutiveAggregatorBot.SPECIALIST_VERSIONS,
        "CATALYST_EVENT": "catalyst-event-specialist-v1",
    }
    WEIGHTS = {
        "TECHNICAL": Decimal("0.34"),
        "SEC_FORM4_INSIDER": Decimal("0.33"),
        "CATALYST_EVENT": Decimal("0.33"),
    }


class PoliticalResearchExecutiveAggregatorBot(ExecutiveAggregatorBot):
    """Stage-4 Political Disclosure candidate; research-only and isolated."""

    VERSION = "ultimate-executive-portfolio-political-research-v1"
    REQUIRED_SPECIALISTS = (
        "TECHNICAL",
        "SEC_FORM4_INSIDER",
        "POLITICAL_DISCLOSURE",
    )
    SPECIALIST_VERSIONS = {
        **ExecutiveAggregatorBot.SPECIALIST_VERSIONS,
        "POLITICAL_DISCLOSURE": "political-disclosure-specialist-v1",
    }
    WEIGHTS = {
        "TECHNICAL": Decimal("0.34"),
        "SEC_FORM4_INSIDER": Decimal("0.33"),
        "POLITICAL_DISCLOSURE": Decimal("0.33"),
    }


class MacroResearchExecutiveAggregatorBot(ExecutiveAggregatorBot):
    """Stage-4 Macro/Cross-Asset candidate; alpha-only and research-only."""

    VERSION = "ultimate-executive-portfolio-macro-research-v1"
    REQUIRED_SPECIALISTS = (
        "TECHNICAL",
        "SEC_FORM4_INSIDER",
        "MACRO_CROSS_ASSET",
    )
    SPECIALIST_VERSIONS = {
        **ExecutiveAggregatorBot.SPECIALIST_VERSIONS,
        "MACRO_CROSS_ASSET": "macro-cross-asset-specialist-v1",
    }
    WEIGHTS = {
        "TECHNICAL": Decimal("0.34"),
        "SEC_FORM4_INSIDER": Decimal("0.33"),
        "MACRO_CROSS_ASSET": Decimal("0.33"),
    }
