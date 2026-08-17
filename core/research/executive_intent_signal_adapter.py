"""TRAIN-only PIT adapter from independent Specialists to Executive intents."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any, Mapping, Sequence

from core.guardrailed_backtest import MarketBar
from core.research.ensemble_signal_adapter import PITRiskRegimeSpecialistBot
from core.research.pit_feature_signal_adapter import (
    PITFeatureConsumer,
    SYMBOLS,
    volatility_risk_off_signal_parameters,
)
from core.research.sec_form4_insider_specialist import (
    SECForm4InsiderSpecialistBot,
    SPECIALIST_VERSION as INSIDER_SPECIALIST_VERSION,
)
from core.research.specialist_signals import (
    ExecutiveAggregatorBot,
    ExecutivePortfolioIntent,
    RiskEnvelope,
    SpecialistSignal,
    StandingStopInstruction,
)


POLICY_VERSION = "train-only-executive-intent-adapter-v1"
TECHNICAL_VERSION = ExecutiveAggregatorBot.SPECIALIST_VERSIONS["TECHNICAL"]


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _time(value: str | datetime, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error
    if not isinstance(parsed, datetime) or parsed.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def executive_intent_signal_parameters() -> dict[str, Any]:
    risk = volatility_risk_off_signal_parameters()
    return {
        **risk,
        "architecture": "TECHNICAL + SEC_FORM4_INSIDER -> EXECUTIVE; RISK separate",
        "executive_version": ExecutiveAggregatorBot.VERSION,
        "insider_specialist_version": INSIDER_SPECIALIST_VERSION,
        "alpha_weights": {
            name: str(weight)
            for name, weight in ExecutiveAggregatorBot.WEIGHTS.items()
        },
        "entry_threshold": str(ExecutiveAggregatorBot.ENTRY_THRESHOLD),
        "exit_threshold": str(ExecutiveAggregatorBot.EXIT_THRESHOLD),
        "maximum_position_weight": str(
            ExecutiveAggregatorBot.MAX_POSITION_WEIGHT
        ),
        "conviction_to_gross": str(ExecutiveAggregatorBot.CONVICTION_TO_GROSS),
        "standing_stop": "current unadjusted close minus 2 * admitted ATR-14",
        "bounded_single_instrument_engine_bridge": True,
        "portfolio_wide_batching_complete": False,
        "research_only": True,
        "promotable": False,
        "parameter_search_allowed": False,
    }


class ExecutiveIntentSignalAdapter:
    """Create one complete Executive intent for the engine's bounded symbol run.

    This bridge deliberately remains TRAIN-only and non-promotable until the
    authoritative engine supports simultaneous portfolio-wide order batching.
    """

    version = POLICY_VERSION

    def __init__(
        self,
        consumer: PITFeatureConsumer,
        *,
        insider_specialist: SECForm4InsiderSpecialistBot,
        liquidation_signal_at: str | datetime,
    ) -> None:
        self.consumer = consumer
        self.insider_specialist = insider_specialist
        self.risk_specialist = PITRiskRegimeSpecialistBot()
        self.executive = ExecutiveAggregatorBot()
        self.liquidation_signal_at = _time(
            liquidation_signal_at, "liquidation_signal_at"
        )
        self._intent_count = 0
        self._entry_count = 0
        self._risk_off_count = 0
        self._no_quorum_count = 0

    @staticmethod
    def parameters() -> dict[str, Any]:
        return executive_intent_signal_parameters()

    @staticmethod
    def _validate_parameters(parameters: Mapping[str, Any]) -> None:
        if dict(parameters) != executive_intent_signal_parameters():
            raise ValueError("strategy parameters differ from the Executive policy")

    def diagnostics(self) -> dict[str, int]:
        return {
            "executive_intent_count": self._intent_count,
            "executive_entry_count": self._entry_count,
            "risk_off_intent_count": self._risk_off_count,
            "no_quorum_intent_count": self._no_quorum_count,
        }

    def decide(
        self,
        symbol: str,
        history_through_signal_close: Sequence[MarketBar],
        parameters: Mapping[str, Any],
    ) -> str:
        raise ValueError(
            "Executive adapter requires the engine's decide_portfolio interface"
        )

    def _technical_signal(
        self, symbol: str, current: MarketBar
    ) -> SpecialistSignal:
        records = {
            basket_symbol: self.consumer.consume_if_available(
                basket_symbol,
                effective_at=current.close_at,
                decision_at=current.available_at,
            )
            for basket_symbol in SYMBOLS
        }
        available_records = [record for record in records.values() if record is not None]
        evidence = [
            records[name].record_sha256
            for name in SYMBOLS
            if records[name] is not None
        ]
        if len(available_records) != len(SYMBOLS):
            return SpecialistSignal(
                specialist_id="TECHNICAL",
                specialist_version=TECHNICAL_VERSION,
                symbol=symbol,
                decision_at=current.available_at.isoformat(),
                score=Decimal("0"),
                confidence=Decimal("0"),
                coverage=Decimal("0"),
                status="ABSTAIN",
                maximum_input_available_at=current.available_at.isoformat(),
                evidence_count=len(available_records),
                evidence_sha256=_hash(evidence),
                reason="CROSS_SECTIONAL_FEATURES_UNAVAILABLE",
            )
        bullish = {
            basket_symbol: (
                Decimal(record.values["sma_20"])
                > Decimal(record.values["sma_50"])
                and Decimal(record.values["momentum_20"]) > 0
            )
            for basket_symbol, record in records.items()
            if record is not None
        }
        positive = bullish[symbol] and sum(bullish.values()) >= 2
        maximum_available = max(
            _time(record.available_at, "technical input available_at")
            for record in available_records
        )
        return SpecialistSignal(
            specialist_id="TECHNICAL",
            specialist_version=TECHNICAL_VERSION,
            symbol=symbol,
            decision_at=current.available_at.isoformat(),
            score=Decimal("1") if positive else Decimal("-1"),
            maximum_input_available_at=maximum_available.isoformat(),
            evidence_count=len(available_records),
            evidence_sha256=_hash(evidence),
            reason=(
                "BULLISH_BASKET_MAJORITY"
                if positive
                else "TECHNICAL_ENTRY_RULE_FAILED"
            ),
        )

    def _insider_signal(
        self, symbol: str, decision_at: datetime
    ) -> SpecialistSignal:
        signal = self.insider_specialist.score_tick(
            symbol, decision_at=decision_at
        )
        if signal.reason not in {
            "NO_INSIDER_COVERAGE_FOR_SYMBOL",
            "INSUFFICIENT_TRAILING_LOOKBACK",
        }:
            return signal
        return SpecialistSignal(
            specialist_id=signal.specialist_id,
            specialist_version=signal.specialist_version,
            symbol=signal.symbol,
            decision_at=signal.decision_at,
            score=Decimal("0"),
            confidence=Decimal("0"),
            coverage=Decimal("0"),
            status="ABSTAIN",
            maximum_input_available_at=signal.maximum_input_available_at,
            evidence_count=signal.evidence_count,
            evidence_sha256=signal.evidence_sha256,
            reason=signal.reason,
            model_version=signal.model_version,
            feature_version=signal.feature_version,
        )

    def _risk_envelope(
        self,
        symbol: str,
        history: Sequence[MarketBar],
        *,
        admitted_atr: Decimal | None,
        parameters: Mapping[str, Any],
        force_liquidation: bool,
    ) -> RiskEnvelope:
        current = history[-1]
        if force_liquidation:
            return RiskEnvelope(
                version="pit-volatility-risk-envelope-v1",
                decision_at=current.available_at.isoformat(),
                status="VALID",
                regime="EVALUATION_LIQUIDATION",
                new_entries_allowed=False,
                forced_exit=True,
                gross_exposure_cap=Decimal("0"),
                symbol_exposure_cap=Decimal("0"),
                position_size_multiplier=Decimal("0"),
                maximum_input_available_at=current.available_at.isoformat(),
                evidence_sha256=_hash(
                    ["EVALUATION_LIQUIDATION", current.available_at.isoformat()]
                ),
                reason_codes=("EVALUATION_LIQUIDATION",),
            )
        if admitted_atr is None:
            return RiskEnvelope(
                version="pit-volatility-risk-envelope-v1",
                decision_at=current.available_at.isoformat(),
                status="STALE",
                regime="UNKNOWN",
                new_entries_allowed=False,
                forced_exit=False,
                gross_exposure_cap=Decimal("0"),
                symbol_exposure_cap=Decimal("0"),
                position_size_multiplier=Decimal("0"),
                maximum_input_available_at=current.available_at.isoformat(),
                evidence_sha256=_hash([]),
                reason_codes=("RISK_FEATURE_UNAVAILABLE",),
            )
        risk_signal = self.risk_specialist.score_tick(
            symbol,
            history=history,
            admitted_atr=admitted_atr,
            parameters=parameters,
        )
        if risk_signal.reason == "INSUFFICIENT_HISTORY":
            return RiskEnvelope(
                version="pit-volatility-risk-envelope-v1",
                decision_at=current.available_at.isoformat(),
                status="STALE",
                regime="UNKNOWN",
                new_entries_allowed=False,
                forced_exit=False,
                gross_exposure_cap=Decimal("0"),
                symbol_exposure_cap=Decimal("0"),
                position_size_multiplier=Decimal("0"),
                maximum_input_available_at=risk_signal.maximum_input_available_at,
                evidence_sha256=risk_signal.evidence_sha256,
                reason_codes=("INSUFFICIENT_HISTORY",),
            )
        risk_on = risk_signal.reason == "RISK_ON"
        return RiskEnvelope(
            version="pit-volatility-risk-envelope-v1",
            decision_at=current.available_at.isoformat(),
            status="VALID",
            regime="NORMAL" if risk_on else "RISK_OFF",
            new_entries_allowed=risk_on,
            forced_exit=not risk_on,
            gross_exposure_cap=Decimal("0.1") if risk_on else Decimal("0"),
            symbol_exposure_cap=Decimal("0.1") if risk_on else Decimal("0"),
            position_size_multiplier=Decimal("1") if risk_on else Decimal("0"),
            maximum_input_available_at=risk_signal.maximum_input_available_at,
            evidence_sha256=risk_signal.evidence_sha256,
            reason_codes=(risk_signal.reason,),
        )

    def decide_portfolio(
        self,
        symbol: str,
        history_through_signal_close: Sequence[MarketBar],
        parameters: Mapping[str, Any],
        *,
        current_weight: Decimal,
        eligible: bool,
    ) -> ExecutivePortfolioIntent:
        self._validate_parameters(parameters)
        if not history_through_signal_close:
            raise ValueError("strategy history is empty")
        current = history_through_signal_close[-1]
        if current.symbol != symbol or symbol not in SYMBOLS:
            raise ValueError("strategy history differs from the campaign symbol")
        record = self.consumer.consume_if_available(
            symbol,
            effective_at=current.close_at,
            decision_at=current.available_at,
        )
        admitted_atr = (
            Decimal(record.values["atr_14"]) if record is not None else None
        )
        force_liquidation = current.close_at >= self.liquidation_signal_at
        risk = self._risk_envelope(
            symbol,
            history_through_signal_close,
            admitted_atr=admitted_atr,
            parameters=parameters,
            force_liquidation=force_liquidation,
        )
        signals = {
            symbol: {
                "TECHNICAL": self._technical_signal(symbol, current),
                "SEC_FORM4_INSIDER": self._insider_signal(
                    symbol, current.available_at
                ),
            }
        }
        stops: dict[str, StandingStopInstruction] = {}
        if admitted_atr is not None:
            stop_price = current.close - Decimal("2") * admitted_atr
            if stop_price > 0:
                stops[symbol] = StandingStopInstruction(
                    reference_price=current.close,
                    trigger_rule=f"LAST_PRICE_LTE_{_decimal_text(stop_price)}",
                    order_type="STOP_MARKET",
                    evidence_sha256=_hash(
                        [record.record_sha256, current.available_at.isoformat()]
                    ),
                )
        intent = self.executive.decide(
            signals,
            risk=risk,
            current_weights={symbol: Decimal(current_weight)},
            eligible_symbols=(symbol,) if eligible else (),
            standing_stops=stops,
            decision_at=current.available_at,
        )
        self._intent_count += 1
        symbol_intent = intent.symbol_intents[0]
        if symbol_intent.action == "ENTER_LONG":
            self._entry_count += 1
        if risk.regime == "RISK_OFF":
            self._risk_off_count += 1
        if "NO_QUORUM" in symbol_intent.reason_codes:
            self._no_quorum_count += 1
        return intent
