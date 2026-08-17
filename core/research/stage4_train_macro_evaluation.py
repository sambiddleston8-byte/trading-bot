"""TRAIN-only Macro/Cross-Asset ablation through GuardrailedBacktestEngine."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from core.research.executive_intent_signal_adapter import (
    ExecutiveIntentSignalAdapter,
    MacroResearchExecutiveIntentAdapter,
    executive_intent_signal_parameters,
    macro_research_intent_parameters,
)
from core.research.macro_cross_asset_specialist import MacroCrossAssetSpecialistBot
from core.research.pit_feature_signal_adapter import PITFeatureConsumer
from core.research.sec_form4_insider_specialist import SECForm4InsiderSpecialistBot
from core.research.stage3_feature_strategy_evaluation import ADMITTED_MATRIX_SHA256, ROOT
from core.research.stage3_train_rolling_diagnostic import _evaluate_train_rolling
from core.research.stage4_train_insider_ensemble_evaluation import (
    ADMITTED_FORM4_ARTIFACT_SHA256,
    FORM4_ARTIFACT,
    _annualized_session_metrics,
)


MACRO_ARTIFACT = ROOT / "stage4/macro/train_macro_cross_asset_pit.json"
OUTPUT = ROOT / "stage4/train_macro_cross_asset_ablation_v1.json"
STATUS = "TRAIN_ONLY_MACRO_CROSS_ASSET_RESEARCH_ABLATION_COMPLETE"


def evaluate_train_macro_ablation(
    repository_root: Path,
    *,
    expected_macro_artifact_sha256: str,
    admitted_train_matrix_sha256: str = ADMITTED_MATRIX_SHA256["TRAIN"],
    expected_form4_artifact_sha256: str = ADMITTED_FORM4_ARTIFACT_SHA256,
) -> dict[str, Any]:
    form4 = json.loads((repository_root / FORM4_ARTIFACT).read_bytes())
    macro = json.loads((repository_root / MACRO_ARTIFACT).read_bytes())
    insider = SECForm4InsiderSpecialistBot(form4, expected_sha256=expected_form4_artifact_sha256)
    macro_bot = MacroCrossAssetSpecialistBot(macro, expected_sha256=expected_macro_artifact_sha256)
    policies: Mapping[str, tuple[type[Any], Mapping[str, Any]]] = {
        "TECHNICAL_INSIDER_BASELINE": (
            ExecutiveIntentSignalAdapter, executive_intent_signal_parameters(),
        ),
        "TECHNICAL_INSIDER_MACRO": (
            MacroResearchExecutiveIntentAdapter, macro_research_intent_parameters(),
        ),
    }

    def factory(adapter_type: type[Any], consumer: PITFeatureConsumer, liquidation_signal_at: str) -> Any:
        common = {"insider_specialist": insider, "liquidation_signal_at": liquidation_signal_at}
        if adapter_type is MacroResearchExecutiveIntentAdapter:
            return adapter_type(consumer, macro_specialist=macro_bot, **common)
        if adapter_type is ExecutiveIntentSignalAdapter:
            return adapter_type(consumer, **common)
        raise ValueError("macro ablation contains an unsupported policy")

    return _evaluate_train_rolling(
        repository_root,
        admitted_train_matrix_sha256=admitted_train_matrix_sha256,
        policies=policies,
        output=OUTPUT,
        status=STATUS,
        observe_policy_divergence=False,
        artifact_lineage={
            "revision": 1, "path": OUTPUT.as_posix(), "predecessor_paths": [],
            "reason": "first Macro/Cross-Asset TRAIN-only research ablation",
        },
        report_metadata={
            "authoritative_engine": "core.guardrailed_backtest:GuardrailedBacktestEngine",
            "comparison": "TECHNICAL+INSIDER versus TECHNICAL+INSIDER+MACRO",
            "cost_models": ["BASE", "PESSIMISTIC"],
            "macro_artifact_sha256": expected_macro_artifact_sha256,
            "synthetic_macro_fixture": True,
            "macro_alpha_only": True,
            "macro_risk_authority": False,
            "registration_decision": "RESEARCH_ONLY",
            "registration_reason": "synthetic engineering evidence cannot establish stable incremental TRAIN value",
            "fixture_limitation": "AAPL/MSFT/SPY is short, narrow, and non-promotable",
            "validation_data_read": False,
            "untouched_test_included": False,
            "production_candidate": False,
        },
        strategy_factory=factory,
        aggregate_metrics_augmenter=_annualized_session_metrics,
    )
