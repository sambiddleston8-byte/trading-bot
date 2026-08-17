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
from core.research.fundamental_valuation_specialist import (
    FundamentalValuationSpecialistBot,
)
from core.research.specialist_signals import (
    ExecutiveAggregatorBot,
    FundamentalResearchExecutiveAggregatorBot,
    ExecutivePortfolioIntent,
    RiskEnvelope,
    SpecialistSignal,
    StandingStopInstruction,
)


POLICY_VERSION = "train-only-executive-portfolio-adapter-v2"
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
        "bounded_single_instrument_engine_bridge": False,
        "portfolio_wide_batching_complete": True,
        "research_only": True,
        "promotable": False,
        "parameter_search_allowed": False,
    }


def fundamental_research_intent_parameters() -> dict[str, Any]:
    result = executive_intent_signal_parameters()
    result.update(
        architecture=(
            "TECHNICAL + SEC_FORM4_INSIDER + FUNDAMENTAL_VALUATION -> "
            "ONE EXECUTIVE; RISK separate"
        ),
        executive_version=FundamentalResearchExecutiveAggregatorBot.VERSION,
        fundamental_specialist_version=(
            FundamentalResearchExecutiveAggregatorBot.SPECIALIST_VERSIONS[
                "FUNDAMENTAL_VALUATION"
            ]
        ),
        alpha_weights={
            name: str(weight)
            for name, weight in FundamentalResearchExecutiveAggregatorBot.WEIGHTS.items()
        },
        registration_status="RESEARCH_ONLY_PENDING_STABLE_REAL_TRAIN_ABLATION",
    )
    return result


class ExecutiveIntentSignalAdapter:
    """Create one complete TRAIN-only Executive intent for the whole portfolio."""

    version = POLICY_VERSION

    def __init__(
        self,
        consumer: PITFeatureConsumer,
        *,
        insider_specialist: SECForm4InsiderSpecialistBot,
        liquidation_signal_at: str | datetime,
        fundamental_specialist: FundamentalValuationSpecialistBot | None = None,
        executive: ExecutiveAggregatorBot | None = None,
    ) -> None:
        self.consumer = consumer
        self.insider_specialist = insider_specialist
        self.risk_specialist = PITRiskRegimeSpecialistBot()
        self.fundamental_specialist = fundamental_specialist
        self.executive = executive or ExecutiveAggregatorBot()
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

    def _validate_parameters(self, parameters: Mapping[str, Any]) -> None:
        expected = (
            fundamental_research_intent_parameters()
            if isinstance(self.executive, FundamentalResearchExecutiveAggregatorBot)
            else executive_intent_signal_parameters()
        )
        if dict(parameters) != expected:
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
        return self.decide_portfolio_batch(
            {symbol: tuple(history_through_signal_close)},
            parameters,
            current_weights={symbol: Decimal(current_weight)},
            eligible_symbols=(symbol,) if eligible else (),
        )

    def decide_portfolio_batch(
        self,
        histories_through_signal_close: Mapping[
            str, Sequence[MarketBar]
        ],
        parameters: Mapping[str, Any],
        *,
        current_weights: Mapping[str, Decimal],
        eligible_symbols: Sequence[str],
    ) -> ExecutivePortfolioIntent:
        self._validate_parameters(parameters)
        if not histories_through_signal_close:
            raise ValueError("strategy portfolio history is empty")
        histories = {
            symbol.strip().upper(): tuple(history)
            for symbol, history in histories_through_signal_close.items()
        }
        if any(
            symbol not in SYMBOLS
            or not history
            or any(bar.symbol != symbol for bar in history)
            for symbol, history in histories.items()
        ):
            raise ValueError("portfolio history differs from the campaign symbols")
        clocks = {
            (history[-1].close_at, history[-1].available_at)
            for history in histories.values()
        }
        if len(clocks) != 1:
            raise ValueError("portfolio histories are not decision-time aligned")
        current_by_symbol = {
            symbol: history[-1] for symbol, history in histories.items()
        }
        current = current_by_symbol[sorted(current_by_symbol)[0]]
        records = {
            symbol: self.consumer.consume_if_available(
                symbol,
                effective_at=bar.close_at,
                decision_at=bar.available_at,
            )
            for symbol, bar in current_by_symbol.items()
        }
        risk_symbol = "SPY" if "SPY" in histories else sorted(histories)[0]
        risk_record = records[risk_symbol]
        admitted_risk_atr = (
            Decimal(risk_record.values["atr_14"])
            if risk_record is not None else None
        )
        force_liquidation = current.close_at >= self.liquidation_signal_at
        risk = self._risk_envelope(
            risk_symbol,
            histories[risk_symbol],
            admitted_atr=admitted_risk_atr,
            parameters=parameters,
            force_liquidation=force_liquidation,
        )
        signals = {
            symbol: {
                "TECHNICAL": self._technical_signal(
                    symbol, current_by_symbol[symbol]
                ),
                "SEC_FORM4_INSIDER": self._insider_signal(
                    symbol, current_by_symbol[symbol].available_at
                ),
            }
            for symbol in histories
        }
        if isinstance(self.executive, FundamentalResearchExecutiveAggregatorBot):
            if self.fundamental_specialist is None:
                raise ValueError("fundamental Executive candidate lacks its Specialist")
            for symbol in histories:
                signals[symbol]["FUNDAMENTAL_VALUATION"] = (
                    self.fundamental_specialist.score_tick(
                        symbol, decision_at=current_by_symbol[symbol].available_at
                    )
                )
        stops: dict[str, StandingStopInstruction] = {}
        for symbol, record in records.items():
            if record is None:
                continue
            admitted_atr = Decimal(record.values["atr_14"])
            bar = current_by_symbol[symbol]
            stop_price = bar.close - Decimal("2") * admitted_atr
            if stop_price > 0:
                stops[symbol] = StandingStopInstruction(
                    reference_price=bar.close,
                    trigger_rule=f"LAST_PRICE_LTE_{_decimal_text(stop_price)}",
                    order_type="STOP_MARKET",
                    evidence_sha256=_hash(
                        [record.record_sha256, bar.available_at.isoformat()]
                    ),
                )
        intent = self.executive.decide(
            signals,
            risk=risk,
            current_weights={
                symbol: Decimal(weight)
                for symbol, weight in current_weights.items()
            },
            eligible_symbols=tuple(sorted(set(eligible_symbols))),
            standing_stops=stops,
            decision_at=current.available_at,
        )
        self._intent_count += 1
        self._entry_count += sum(
            item.action == "ENTER_LONG" for item in intent.symbol_intents
        )
        if risk.regime == "RISK_OFF":
            self._risk_off_count += len(intent.symbol_intents)
        self._no_quorum_count += sum(
            "NO_QUORUM" in item.reason_codes
            for item in intent.symbol_intents
        )
        return intent


class FundamentalResearchExecutiveIntentAdapter(ExecutiveIntentSignalAdapter):
    """Explicit research-only adapter for the three-alpha Stage-4 ablation."""

    version = "train-only-fundamental-executive-adapter-v1"

    def __init__(
        self,
        consumer: PITFeatureConsumer,
        *,
        insider_specialist: SECForm4InsiderSpecialistBot,
        fundamental_specialist: FundamentalValuationSpecialistBot,
        liquidation_signal_at: str | datetime,
    ) -> None:
        super().__init__(
            consumer,
            insider_specialist=insider_specialist,
            fundamental_specialist=fundamental_specialist,
            executive=FundamentalResearchExecutiveAggregatorBot(),
            liquidation_signal_at=liquidation_signal_at,
        )

    @staticmethod
    def parameters() -> dict[str, Any]:
        return fundamental_research_intent_parameters()
