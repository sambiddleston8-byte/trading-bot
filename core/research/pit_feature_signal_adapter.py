"""PIT-safe consumer and fixed technical signal adapter for admitted features."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any, Mapping, Sequence

from core.features.pit_feature_contract import DEFINITION_SHA256, FAMILY, PITFeatureRecord
from core.guardrailed_backtest import (
    ACTION_ENTER_LONG,
    ACTION_EXIT_LONG,
    ACTION_HOLD,
    MarketBar,
)


SYMBOLS = ("AAPL", "MSFT", "SPY")
POLICY_VERSION = "admitted-pit-technical-signal-v1"
CONFIRMED_POLICY_VERSION = "admitted-pit-momentum-confirmed-signal-v2"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _time(value: str | datetime, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error
    if not isinstance(parsed, datetime) or parsed.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def deterministic_signal_parameters() -> dict[str, Any]:
    return {
        "entry_rule": "sma_20 > sma_50 AND momentum_20 > 0",
        "exit_rule": "sma_20 <= sma_50 OR momentum_20 <= 0",
        "atr_position_sizing": "admitted atr_14 with engine 2x ATR stop and 1% equity risk cap",
        "parameter_search_allowed": False,
    }


def momentum_confirmed_signal_parameters() -> dict[str, Any]:
    return {
        "entry_rule": (
            "current and prior session sma_20 > sma_50 AND "
            "current momentum_20 > prior momentum_20 > 0"
        ),
        "exit_rule": "sma_20 <= sma_50 OR momentum_20 <= 0",
        "confirmation_sessions": 2,
        "atr_position_sizing": (
            "admitted atr_14 with engine 2x ATR stop and 1% equity risk cap"
        ),
        "parameter_search_allowed": False,
    }


class PITFeatureConsumer:
    """Verify and expose one admitted matrix under an explicit as-of boundary."""

    def __init__(
        self,
        matrix: Mapping[str, Any],
        *,
        expected_matrix_sha256: str,
        suppressed_decision_ats: Sequence[str | datetime] = (),
    ) -> None:
        material = dict(matrix)
        embedded_hash = material.pop("matrix_sha256", None)
        derived_hash = hashlib.sha256(_canonical(material)).hexdigest()
        if embedded_hash != derived_hash or embedded_hash != expected_matrix_sha256:
            raise ValueError("feature matrix differs from its admitted SHA-256")
        if matrix.get("admitted") is not True or matrix.get("untouched_test_included") is not False:
            raise ValueError("consumer accepts admitted non-TEST matrices only")
        if (
            matrix.get("feature_family") != FAMILY
            or matrix.get("feature_definition_sha256") != DEFINITION_SHA256
        ):
            raise ValueError("feature matrix family is unsupported")
        role = matrix.get("partition_role")
        if role not in {"TRAIN", "VALIDATION"}:
            raise ValueError("feature matrix role is unsupported")
        rows = tuple(PITFeatureRecord(**row) for row in matrix.get("rows", ()))
        if not rows or any(row.partition_role != role for row in rows):
            raise ValueError("feature rows do not match the matrix role")
        index = {(row.entity_id, _time(row.effective_at, "effective_at")): row for row in rows}
        if len(index) != len(rows):
            raise ValueError("feature matrix has duplicate symbol/session rows")
        sessions = sorted({_time(row.effective_at, "effective_at") for row in rows})
        if set(index) != {(symbol, session) for symbol in SYMBOLS for session in sessions}:
            raise ValueError("feature matrix is not cross-sectionally aligned")
        self.role = role
        self.matrix_sha256 = embedded_hash
        self._index = index
        self._first_effective_at = sessions[0]
        self._last_effective_at = sessions[-1]
        self._suppressed = frozenset(
            _time(value, "suppressed_decision_at") for value in suppressed_decision_ats
        )
        if not self._suppressed <= set(sessions):
            raise ValueError("suppressed decision is outside the admitted matrix")

    def consume(
        self,
        symbol: str,
        *,
        effective_at: datetime,
        decision_at: datetime,
    ) -> PITFeatureRecord | None:
        return self._consume(
            symbol,
            effective_at=effective_at,
            decision_at=decision_at,
            unavailable_is_none=False,
        )

    def consume_if_available(
        self,
        symbol: str,
        *,
        effective_at: datetime,
        decision_at: datetime,
    ) -> PITFeatureRecord | None:
        """Return no row when this admitted vintage was unavailable as of decision."""
        return self._consume(
            symbol,
            effective_at=effective_at,
            decision_at=decision_at,
            unavailable_is_none=True,
        )

    def _consume(
        self,
        symbol: str,
        *,
        effective_at: datetime,
        decision_at: datetime,
        unavailable_is_none: bool,
    ) -> PITFeatureRecord | None:
        resolved_symbol = str(symbol).strip().upper()
        effective = _time(effective_at, "effective_at")
        decision = _time(decision_at, "decision_at")
        if effective > decision:
            raise ValueError("feature effective_at exceeds decision_at")
        if effective in self._suppressed:
            return None
        record = self._index.get((resolved_symbol, effective))
        if record is None:
            if effective < self._first_effective_at:
                return None
            raise ValueError("admitted feature row is missing at decision time")
        if _time(record.available_at, "available_at") > decision:
            if unavailable_is_none:
                return None
            raise ValueError("feature available_at exceeds decision_at")
        if record.feature_definition_sha256 != DEFINITION_SHA256:
            raise ValueError("feature row definition differs from the fixed contract")
        if any(
            _time(row["available_at"], "input available_at") > decision
            for row in record.provenance["input_rows"]
        ):
            raise ValueError("feature input availability exceeds decision_at")
        return record


class DeterministicSignalAdapter:
    """Fixed SMA-cross, momentum-filter and admitted-ATR strategy adapter."""

    version = POLICY_VERSION

    def __init__(
        self,
        consumer: PITFeatureConsumer,
        *,
        liquidation_signal_at: str | datetime,
    ) -> None:
        self.consumer = consumer
        self.liquidation_signal_at = _time(
            liquidation_signal_at, "liquidation_signal_at"
        )

    @staticmethod
    def parameters() -> dict[str, Any]:
        return deterministic_signal_parameters()

    @staticmethod
    def _validate_parameters(parameters: Mapping[str, Any]) -> None:
        if dict(parameters) != deterministic_signal_parameters():
            raise ValueError("strategy parameters differ from the fixed signal policy")

    def _record(
        self,
        symbol: str,
        history: Sequence[MarketBar],
        parameters: Mapping[str, Any],
    ) -> PITFeatureRecord | None:
        self._validate_parameters(parameters)
        if not history:
            raise ValueError("strategy history is empty")
        current = history[-1]
        if current.symbol != symbol:
            raise ValueError("strategy history symbol differs from the request")
        return self.consumer.consume(
            symbol,
            effective_at=current.close_at,
            decision_at=current.available_at,
        )

    def decide(
        self,
        symbol: str,
        history_through_signal_close: Sequence[MarketBar],
        parameters: Mapping[str, Any],
    ) -> str:
        self._validate_parameters(parameters)
        if not history_through_signal_close:
            raise ValueError("strategy history is empty")
        current = history_through_signal_close[-1]
        if current.symbol != symbol:
            raise ValueError("strategy history symbol differs from the request")
        if current.close_at >= self.liquidation_signal_at:
            return ACTION_EXIT_LONG
        record = self._record(symbol, history_through_signal_close, parameters)
        if record is None:
            return ACTION_HOLD
        sma_20 = Decimal(record.values["sma_20"])
        sma_50 = Decimal(record.values["sma_50"])
        momentum_20 = Decimal(record.values["momentum_20"])
        if sma_20 > sma_50 and momentum_20 > 0:
            return ACTION_ENTER_LONG
        return ACTION_EXIT_LONG

    def atr_for_signal(
        self,
        symbol: str,
        history_through_signal_close: Sequence[MarketBar],
        parameters: Mapping[str, Any],
    ) -> Decimal:
        record = self._record(symbol, history_through_signal_close, parameters)
        if record is None:
            raise ValueError("admitted ATR is unavailable for the entry signal")
        atr = Decimal(record.values["atr_14"])
        if not atr.is_finite() or atr <= 0:
            raise ValueError("admitted ATR must be finite and positive")
        return atr


class MomentumConfirmedSignalAdapter(DeterministicSignalAdapter):
    """Require a rising two-session PIT trend before entering long."""

    version = CONFIRMED_POLICY_VERSION

    @staticmethod
    def parameters() -> dict[str, Any]:
        return momentum_confirmed_signal_parameters()

    @staticmethod
    def _validate_parameters(parameters: Mapping[str, Any]) -> None:
        if dict(parameters) != momentum_confirmed_signal_parameters():
            raise ValueError(
                "strategy parameters differ from the fixed confirmed signal policy"
            )

    def decide(
        self,
        symbol: str,
        history_through_signal_close: Sequence[MarketBar],
        parameters: Mapping[str, Any],
    ) -> str:
        self._validate_parameters(parameters)
        if not history_through_signal_close:
            raise ValueError("strategy history is empty")
        current_bar = history_through_signal_close[-1]
        if current_bar.symbol != symbol:
            raise ValueError("strategy history symbol differs from the request")
        if current_bar.close_at >= self.liquidation_signal_at:
            return ACTION_EXIT_LONG
        current_record = self._record(
            symbol, history_through_signal_close, parameters
        )
        if current_record is None:
            return ACTION_HOLD

        current_sma_20 = Decimal(current_record.values["sma_20"])
        current_sma_50 = Decimal(current_record.values["sma_50"])
        current_momentum = Decimal(current_record.values["momentum_20"])
        if current_sma_20 <= current_sma_50 or current_momentum <= 0:
            return ACTION_EXIT_LONG
        confirmation_sessions = int(parameters["confirmation_sessions"])
        if len(history_through_signal_close) < confirmation_sessions:
            return ACTION_HOLD

        prior_bar = history_through_signal_close[-confirmation_sessions]
        prior_record = self.consumer.consume_if_available(
            symbol,
            effective_at=prior_bar.close_at,
            decision_at=prior_bar.available_at,
        )
        if prior_record is None:
            return ACTION_HOLD
        prior_sma_20 = Decimal(prior_record.values["sma_20"])
        prior_sma_50 = Decimal(prior_record.values["sma_50"])
        prior_momentum = Decimal(prior_record.values["momentum_20"])
        if (
            prior_sma_20 > prior_sma_50
            and current_momentum > prior_momentum > 0
        ):
            return ACTION_ENTER_LONG
        return ACTION_HOLD
