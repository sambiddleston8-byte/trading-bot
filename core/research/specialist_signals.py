"""Normalized specialist outputs and the fixed executive aggregation contract."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any, Mapping

import pandas as pd


MIN_SCORE = Decimal("-1")
MAX_SCORE = Decimal("1")


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


def _decimal(value: Decimal) -> str:
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

    def __post_init__(self) -> None:
        if not self.specialist_id or not self.specialist_version:
            raise ValueError("specialist identity is required")
        if self.symbol not in {"AAPL", "MSFT", "SPY"}:
            raise ValueError("specialist symbol is outside the fixed campaign basket")
        _time(self.decision_at, "decision_at")
        object.__setattr__(self, "score", _score(self.score))
        if not isinstance(self.evidence_count, int) or self.evidence_count < 0:
            raise ValueError("evidence_count must be a non-negative integer")
        if (
            len(self.evidence_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.evidence_sha256)
        ):
            raise ValueError("evidence_sha256 must be a lowercase SHA-256")
        if not self.reason:
            raise ValueError("specialist reason is required")

    def as_dict(self) -> dict[str, Any]:
        return {
            "specialist_id": self.specialist_id,
            "specialist_version": self.specialist_version,
            "symbol": self.symbol,
            "decision_at": _time(self.decision_at, "decision_at").isoformat(),
            "score": _decimal(self.score),
            "evidence_count": self.evidence_count,
            "evidence_sha256": self.evidence_sha256,
            "reason": self.reason,
        }


class ExecutiveAggregatorBot:
    """Combine independent sub-bot scores without reading their raw inputs."""

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
            specialist_id="EXECUTIVE_AGGREGATOR",
            specialist_version=self.VERSION,
            symbol=next(iter(symbols)),
            decision_at=decision.isoformat(),
            score=combined,
            evidence_count=sum(signal.evidence_count for signal in signals.values()),
            evidence_sha256=evidence_sha256,
            reason="FIXED_WEIGHTED_SPECIALIST_AGGREGATION",
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
