"""PIT-safe Technical + Risk/Regime + SEC Form 4 ensemble adapter."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
import json
from typing import Any, Mapping, Sequence

import pandas as pd

from core.guardrailed_backtest import ACTION_ENTER_LONG, ACTION_EXIT_LONG, MarketBar
from core.research.pit_feature_signal_adapter import (
    PITFeatureConsumer,
    SYMBOLS,
    VolatilityRiskOffSignalAdapter,
    volatility_risk_off_signal_parameters,
)
from core.research.sec_form4_insider_specialist import (
    SECForm4InsiderSpecialistBot,
    SPECIALIST_VERSION,
)
from core.research.specialist_signals import (
    ExecutiveAggregatorBot,
    SpecialistSignal,
)


ENSEMBLE_POLICY_VERSION = "pit-three-specialist-executive-ensemble-v1"


def ensemble_signal_parameters() -> dict[str, Any]:
    risk = volatility_risk_off_signal_parameters()
    return {
        **risk,
        "technical_specialist": "fixed SMA-20/50 plus Momentum-20 basket breadth",
        "risk_regime_specialist": "causal ATR-14/close prior-20 80th percentile",
        "insider_specialist": "SEC Form 4 P/S trailing-60-day cluster role intensity",
        "insider_specialist_version": SPECIALIST_VERSION,
        "executive_aggregator_version": ExecutiveAggregatorBot.VERSION,
        "specialist_weights": {
            name: str(weight)
            for name, weight in ExecutiveAggregatorBot.WEIGHTS.items()
        },
        "entry_threshold": str(ExecutiveAggregatorBot.ENTRY_THRESHOLD),
        "entry_gate": "technical > 0 AND risk_regime > 0 AND aggregate >= threshold",
        "parameter_search_allowed": False,
    }


def _evidence_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class PITTechnicalSpecialistBot:
    specialist_id = "TECHNICAL"
    version = "pit-sma-momentum-breadth-specialist-v1"

    def __init__(self, consumer: PITFeatureConsumer) -> None:
        self.consumer = consumer

    def score_tick(self, symbol: str, *, bar: MarketBar) -> SpecialistSignal:
        if symbol not in SYMBOLS or bar.symbol != symbol:
            raise ValueError("technical specialist symbol/bar is outside its basket")
        records = {
            basket_symbol: self.consumer.consume_if_available(
                basket_symbol,
                effective_at=bar.close_at,
                decision_at=bar.available_at,
            )
            for basket_symbol in SYMBOLS
        }
        if any(record is None for record in records.values()):
            return SpecialistSignal(
                specialist_id=self.specialist_id,
                specialist_version=self.version,
                symbol=symbol,
                decision_at=bar.available_at.isoformat(),
                score=Decimal("-1"),
                evidence_count=0,
                evidence_sha256=_evidence_hash([]),
                reason="CROSS_SECTIONAL_FEATURES_UNAVAILABLE",
            )
        bullish = {
            basket_symbol: (
                Decimal(record.values["sma_20"]) > Decimal(record.values["sma_50"])
                and Decimal(record.values["momentum_20"]) > 0
            )
            for basket_symbol, record in records.items()
            if record is not None
        }
        positive = bullish[symbol] and sum(bullish.values()) >= 2
        return SpecialistSignal(
            specialist_id=self.specialist_id,
            specialist_version=self.version,
            symbol=symbol,
            decision_at=bar.available_at.isoformat(),
            score=Decimal("1") if positive else Decimal("-1"),
            evidence_count=len(records),
            evidence_sha256=_evidence_hash(
                [records[name].record_sha256 for name in SYMBOLS if records[name] is not None]
            ),
            reason="BULLISH_BASKET_MAJORITY" if positive else "TECHNICAL_ENTRY_RULE_FAILED",
        )

    def score_frame(self, decisions: pd.DataFrame) -> pd.DataFrame:
        """Ordered batch interface delegated to the authoritative tick rule."""
        if set(decisions.columns) != {"symbol", "bar"}:
            raise ValueError("technical decision frame requires symbol and bar")
        rows = []
        for row in decisions.itertuples(index=False):
            if not isinstance(row.bar, MarketBar):
                raise ValueError("technical batch bar is invalid")
            signal = self.score_tick(str(row.symbol), bar=row.bar)
            rows.append(
                {
                    "symbol": signal.symbol,
                    "decision_at": signal.decision_at,
                    "score": str(signal.score),
                }
            )
        return pd.DataFrame(rows, columns=("symbol", "decision_at", "score"))


class PITRiskRegimeSpecialistBot:
    specialist_id = "RISK_REGIME"
    version = "pit-atr-percentile-risk-regime-specialist-v1"

    def score_tick(
        self,
        symbol: str,
        *,
        history: Sequence[MarketBar],
        admitted_atr: Decimal,
        parameters: Mapping[str, Any],
    ) -> SpecialistSignal:
        if (
            symbol not in SYMBOLS
            or not history
            or any(bar.symbol != symbol for bar in history)
        ):
            raise ValueError("risk specialist history differs from its basket symbol")
        current = history[-1]
        reason = VolatilityRiskOffSignalAdapter._risk_off_reason(
            history,
            parameters,
            admitted_current_atr=admitted_atr,
        )
        evidence = [
            {
                "close_at": bar.close_at.isoformat(),
                "available_at": bar.available_at.isoformat(),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
            }
            for bar in history[-35:]
        ]
        return SpecialistSignal(
            specialist_id=self.specialist_id,
            specialist_version=self.version,
            symbol=symbol,
            decision_at=current.available_at.isoformat(),
            score=Decimal("1") if reason is None else Decimal("-1"),
            evidence_count=len(evidence),
            evidence_sha256=_evidence_hash(evidence),
            reason="RISK_ON" if reason is None else reason,
        )

    def score_frame(self, decisions: pd.DataFrame) -> pd.DataFrame:
        """Ordered batch interface delegated to the authoritative tick rule."""
        required = {"symbol", "history", "admitted_atr", "parameters"}
        if set(decisions.columns) != required:
            raise ValueError("risk-regime decision frame has an unsupported schema")
        rows = []
        for row in decisions.itertuples(index=False):
            signal = self.score_tick(
                str(row.symbol),
                history=row.history,
                admitted_atr=Decimal(str(row.admitted_atr)),
                parameters=row.parameters,
            )
            rows.append(
                {
                    "symbol": signal.symbol,
                    "decision_at": signal.decision_at,
                    "score": str(signal.score),
                }
            )
        return pd.DataFrame(rows, columns=("symbol", "decision_at", "score"))


class EnsembleSignalAdapter(VolatilityRiskOffSignalAdapter):
    """Engine adapter over three isolated specialists and one executive bot."""

    version = ENSEMBLE_POLICY_VERSION

    def __init__(
        self,
        consumer: PITFeatureConsumer,
        *,
        insider_specialist: SECForm4InsiderSpecialistBot,
        liquidation_signal_at: str | datetime,
    ) -> None:
        super().__init__(consumer, liquidation_signal_at=liquidation_signal_at)
        self.technical_specialist = PITTechnicalSpecialistBot(consumer)
        self.risk_specialist = PITRiskRegimeSpecialistBot()
        self.insider_specialist = insider_specialist
        self.executive = ExecutiveAggregatorBot()
        self._aggregate_entry_candidates = 0
        self._insider_veto_suppressions = 0
        self._insider_lookback_suppressions = 0
        self._ensemble_entries_permitted = 0

    @staticmethod
    def parameters() -> dict[str, Any]:
        return ensemble_signal_parameters()

    @staticmethod
    def _validate_parameters(parameters: Mapping[str, Any]) -> None:
        if dict(parameters) != ensemble_signal_parameters():
            raise ValueError("strategy parameters differ from the fixed ensemble policy")

    def diagnostics(self) -> dict[str, int]:
        return {
            "aggregate_entry_candidates": self._aggregate_entry_candidates,
            "insider_veto_suppressions": self._insider_veto_suppressions,
            "insider_lookback_suppressions": self._insider_lookback_suppressions,
            "ensemble_entries_permitted": self._ensemble_entries_permitted,
        }

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
        if current.symbol != symbol or symbol not in SYMBOLS:
            raise ValueError("strategy history differs from the fixed campaign symbol")
        if current.close_at >= self.liquidation_signal_at:
            return ACTION_EXIT_LONG
        record = self.consumer.consume_if_available(
            symbol,
            effective_at=current.close_at,
            decision_at=current.available_at,
        )
        if record is None:
            return ACTION_EXIT_LONG

        signals = {
            "TECHNICAL": self.technical_specialist.score_tick(symbol, bar=current),
            "RISK_REGIME": self.risk_specialist.score_tick(
                symbol,
                history=history_through_signal_close,
                admitted_atr=Decimal(record.values["atr_14"]),
                parameters=parameters,
            ),
            "SEC_FORM4_INSIDER": self.insider_specialist.score_tick(
                symbol, decision_at=current.available_at
            ),
        }
        aggregate = self.executive.aggregate(
            signals, decision_at=current.available_at
        )
        if signals["SEC_FORM4_INSIDER"].reason == "INSUFFICIENT_TRAILING_LOOKBACK":
            self._insider_lookback_suppressions += 1
        technical = signals["TECHNICAL"].score
        risk = signals["RISK_REGIME"].score
        if technical > 0 and risk > 0:
            self._aggregate_entry_candidates += 1
            if aggregate.score >= self.executive.ENTRY_THRESHOLD:
                self._ensemble_entries_permitted += 1
                return ACTION_ENTER_LONG
            self._insider_veto_suppressions += 1
        return ACTION_EXIT_LONG
