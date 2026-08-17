"""TRAIN-only Specialist #3 ablation through GuardrailedBacktestEngine."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from core.research.executive_intent_signal_adapter import (
    ExecutiveIntentSignalAdapter,
    FundamentalResearchExecutiveIntentAdapter,
    executive_intent_signal_parameters,
    fundamental_research_intent_parameters,
)
from core.research.fundamental_valuation_specialist import (
    FundamentalValuationSpecialistBot,
)
from core.research.pit_feature_signal_adapter import PITFeatureConsumer
from core.research.sec_form4_insider_specialist import SECForm4InsiderSpecialistBot
from core.research.stage3_feature_strategy_evaluation import ADMITTED_MATRIX_SHA256, ROOT
from core.research.stage3_train_rolling_diagnostic import _evaluate_train_rolling
from core.research.stage4_train_insider_ensemble_evaluation import (
    ADMITTED_FORM4_ARTIFACT_SHA256,
    FORM4_ARTIFACT,
    _annualized_session_metrics,
)


FUNDAMENTAL_ARTIFACT = ROOT / "stage4/fundamental/train_fundamental_pit.json"
OUTPUT = ROOT / "stage4/train_fundamental_ablation_v1.json"
STATUS = "TRAIN_ONLY_FUNDAMENTAL_RESEARCH_ABLATION_COMPLETE"


def evaluate_train_fundamental_ablation(
    repository_root: Path,
    *,
    expected_fundamental_artifact_sha256: str,
    admitted_train_matrix_sha256: str = ADMITTED_MATRIX_SHA256["TRAIN"],
    expected_form4_artifact_sha256: str = ADMITTED_FORM4_ARTIFACT_SHA256,
) -> dict[str, Any]:
    """Compare two- versus three-alpha research policies under both cost models."""
    form4 = json.loads((repository_root / FORM4_ARTIFACT).read_bytes())
    fundamental = json.loads((repository_root / FUNDAMENTAL_ARTIFACT).read_bytes())
    insider = SECForm4InsiderSpecialistBot(
        form4, expected_sha256=expected_form4_artifact_sha256
    )
    fundamental_bot = FundamentalValuationSpecialistBot(
        fundamental, expected_sha256=expected_fundamental_artifact_sha256
    )
    policies: Mapping[str, tuple[type[Any], Mapping[str, Any]]] = {
        "TECHNICAL_INSIDER_BASELINE": (
            ExecutiveIntentSignalAdapter,
            executive_intent_signal_parameters(),
        ),
        "TECHNICAL_INSIDER_FUNDAMENTAL": (
            FundamentalResearchExecutiveIntentAdapter,
            fundamental_research_intent_parameters(),
        ),
    }

    def factory(
        adapter_type: type[Any],
        consumer: PITFeatureConsumer,
        liquidation_signal_at: str,
    ) -> Any:
        arguments = {
            "insider_specialist": insider,
            "liquidation_signal_at": liquidation_signal_at,
        }
        if adapter_type is FundamentalResearchExecutiveIntentAdapter:
            return adapter_type(
                consumer,
                fundamental_specialist=fundamental_bot,
                **arguments,
            )
        if adapter_type is ExecutiveIntentSignalAdapter:
            return adapter_type(consumer, **arguments)
        raise ValueError("fundamental ablation contains an unsupported policy")

    return _evaluate_train_rolling(
        repository_root,
        admitted_train_matrix_sha256=admitted_train_matrix_sha256,
        policies=policies,
        output=OUTPUT,
        status=STATUS,
        observe_policy_divergence=False,
        artifact_lineage={
            "revision": 1,
            "path": OUTPUT.as_posix(),
            "predecessor_paths": [],
            "reason": "first Specialist #3 TRAIN-only research ablation",
        },
        report_metadata={
            "authoritative_engine": (
                "core.guardrailed_backtest:GuardrailedBacktestEngine"
            ),
            "comparison": (
                "TECHNICAL+INSIDER versus TECHNICAL+INSIDER+FUNDAMENTAL"
            ),
            "cost_models": ["BASE", "PESSIMISTIC"],
            "fundamental_artifact_sha256": expected_fundamental_artifact_sha256,
            "synthetic_fundamental_fixture": True,
            "registration_decision": "RESEARCH_ONLY",
            "registration_reason": (
                "synthetic engineering evidence cannot establish stable incremental TRAIN value"
            ),
            "validation_data_read": False,
            "untouched_test_included": False,
            "production_candidate": False,
        },
        strategy_factory=factory,
        aggregate_metrics_augmenter=_annualized_session_metrics,
    )
