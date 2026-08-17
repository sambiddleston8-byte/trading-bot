"""TRAIN-only Executive-intent engine comparison against the frozen baseline."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from core.research.ensemble_signal_adapter import (
    EnsembleSignalAdapter,
    ensemble_signal_parameters,
)
from core.research.executive_intent_signal_adapter import (
    ExecutiveIntentSignalAdapter,
    executive_intent_signal_parameters,
)
from core.research.pit_feature_signal_adapter import PITFeatureConsumer
from core.research.sec_form4_insider_specialist import SECForm4InsiderSpecialistBot
from core.research.stage3_feature_strategy_evaluation import (
    ADMITTED_MATRIX_SHA256,
    ROOT,
)
from core.guardrailed_backtest import BacktestResult
from core.research.stage3_train_rolling_diagnostic import _evaluate_train_rolling
from core.research.stage4_train_insider_ensemble_evaluation import (
    ADMITTED_FORM4_ARTIFACT_SHA256,
    FORM4_ARTIFACT,
    _annualized_session_metrics,
)


OUTPUT = ROOT / "stage3/train_executive_intent_evaluation_v2.json"


def _validate_result_trace(
    policy_name: str, symbol: str, result: BacktestResult
) -> None:
    if policy_name == "LEGACY_RESEARCH_THREE_VOTE":
        if result.executive_intents:
            raise ValueError("legacy baseline unexpectedly emitted Executive intents")
        return
    if policy_name != "EXECUTIVE_INTENT_BRIDGE":
        raise ValueError("Executive comparison contains an unknown policy")
    if not result.executive_intents:
        raise ValueError("Executive evaluation lacks immutable intent traces")
    trace_index = {
        (trace.decision_at, trace.symbol): trace
        for trace in result.executive_intents
    }
    if len(trace_index) != len(result.executive_intents):
        raise ValueError("Executive intent trace contains duplicate decisions")
    for execution in result.executions:
        if execution.reason != "EXECUTIVE_TARGET":
            continue
        trace = trace_index.get((execution.signal_at, symbol))
        if trace is None:
            raise ValueError("Executive execution lacks its originating intent trace")
        expected_actions = (
            {"ENTER_LONG"} if execution.action == "BUY" else {"REDUCE", "EXIT"}
        )
        if trace.action not in expected_actions:
            raise ValueError("Executive execution direction differs from its intent")


def evaluate_train_executive_intents(
    repository_root: Path,
    *,
    admitted_train_matrix_sha256: str = ADMITTED_MATRIX_SHA256["TRAIN"],
    expected_form4_artifact_sha256: str = ADMITTED_FORM4_ARTIFACT_SHA256,
) -> dict[str, Any]:
    """Compare old and new authority paths without opening later partitions."""
    form4 = json.loads((repository_root / FORM4_ARTIFACT).read_bytes())
    insider = SECForm4InsiderSpecialistBot(
        form4, expected_sha256=expected_form4_artifact_sha256
    )
    policies: Mapping[str, tuple[type[Any], Mapping[str, Any]]] = {
        "LEGACY_RESEARCH_THREE_VOTE": (
            EnsembleSignalAdapter,
            ensemble_signal_parameters(),
        ),
        "EXECUTIVE_INTENT_BRIDGE": (
            ExecutiveIntentSignalAdapter,
            executive_intent_signal_parameters(),
        ),
    }

    def strategy_factory(
        adapter_type: type[Any],
        consumer: PITFeatureConsumer,
        liquidation_signal_at: str,
    ) -> Any:
        if adapter_type in {EnsembleSignalAdapter, ExecutiveIntentSignalAdapter}:
            return adapter_type(
                consumer,
                insider_specialist=insider,
                liquidation_signal_at=liquidation_signal_at,
            )
        raise ValueError("Executive comparison contains an unsupported adapter")

    return _evaluate_train_rolling(
        repository_root,
        admitted_train_matrix_sha256=admitted_train_matrix_sha256,
        policies=policies,
        output=OUTPUT,
        status="TRAIN_ONLY_EXECUTIVE_INTENT_ENGINE_COMPARISON_COMPLETE",
        observe_policy_divergence=False,
        artifact_lineage={
            "revision": 2,
            "path": OUTPUT.as_posix(),
            "predecessor_paths": [
                (
                    ROOT / "stage3/train_executive_intent_evaluation_v1.json"
                ).as_posix()
            ],
            "reason": (
                "insufficient volatility history now yields stale Risk and can never "
                "originate a forced exit"
            ),
        },
        report_metadata={
            "partition_role": "TRAIN",
            "validation_data_read": False,
            "untouched_test_included": False,
            "legacy_baseline_research_only": True,
            "risk_counted_as_alpha_in_executive_path": False,
            "every_executive_order_requires_intent_trace": True,
            "bounded_single_instrument_engine_runs": True,
            "simultaneous_portfolio_batching_complete": False,
            "promotion_allowed": False,
            "next_gate": (
                "simultaneous portfolio-wide batching and intent/order/cash conformance"
            ),
        },
        strategy_factory=strategy_factory,
        aggregate_metrics_augmenter=_annualized_session_metrics,
        result_validator=_validate_result_trace,
    )
