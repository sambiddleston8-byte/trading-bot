"""TRAIN-only comparison of risk-off baseline with the Form 4 ensemble."""
from __future__ import annotations

import json
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Mapping

from core.research.ensemble_signal_adapter import (
    EnsembleSignalAdapter,
    ensemble_signal_parameters,
)
from core.features.pit_feature_contract import DECIMAL_CONTEXT
from core.research.pit_feature_signal_adapter import (
    DeterministicSignalAdapter,
    PITFeatureConsumer,
    VolatilityRiskOffSignalAdapter,
    volatility_risk_off_signal_parameters,
)
from core.research.sec_form4_insider_specialist import (
    ISSUER_CIKS,
    LEGACY_ADMITTED_SCHEMA_VERSION,
    LOOKBACK_COMPLETE_FROM,
    ROLE_TAXONOMY_VERSION,
    SYMBOLS,
    SECForm4InsiderSpecialistBot,
)
from core.research.stage3_feature_strategy_evaluation import (
    ADMITTED_MATRIX_SHA256,
    ROOT,
)
from core.research.stage3_train_rolling_diagnostic import _evaluate_train_rolling
from core.research.stage4_train_volatility_evaluation import _evaluation_metadata


FORM4_ARTIFACT = ROOT / "stage4/sec_form4/train_form4_pit.json"
FORM4_CAPTURE_ARTIFACT = ROOT / "stage4/sec_form4/train_form4_pit_v2.json"
OUTPUT = ROOT / "stage4/train_insider_ensemble_evaluation_v3.json"
ADMITTED_FORM4_ARTIFACT_SHA256 = (
    "358a66ff56dfa19ee2a038d72872a4a8629aff88be4be439eb72549fd685e874"
)


def _annualized_session_metrics(aggregate: Mapping[str, Any]) -> Mapping[str, str]:
    total_return = Decimal(aggregate["fold_reset_chained_total_return"])
    sessions = Decimal(aggregate["pooled_evaluated_sessions"])
    if sessions <= 0 or total_return <= Decimal("-1"):
        raise ValueError("annualized session CAGR inputs are outside their domain")
    with localcontext(DECIMAL_CONTEXT):
        cagr = (
            (Decimal("1") + total_return)
            ** (Decimal("252") / sessions)
            - Decimal("1")
        )
    text = format(cagr, "f")
    return {
        "annualized_session_cagr": text.rstrip("0").rstrip(".") if "." in text else text,
        "annualized_session_cagr_definition": (
            "(1 + fold-reset chained return)^(252 / pooled evaluated sessions) - 1"
        ),
    }


def evaluate_train_insider_ensemble(
    repository_root: Path,
    *,
    admitted_train_matrix_sha256: str = ADMITTED_MATRIX_SHA256["TRAIN"],
    expected_form4_artifact_sha256: str = ADMITTED_FORM4_ARTIFACT_SHA256,
) -> dict[str, Any]:
    form4_path = repository_root / FORM4_ARTIFACT
    form4 = json.loads(form4_path.read_bytes())
    insider = SECForm4InsiderSpecialistBot(
        form4, expected_sha256=expected_form4_artifact_sha256
    )
    metadata = _evaluation_metadata(
        repository_root,
        admitted_train_matrix_sha256=admitted_train_matrix_sha256,
    )
    policies: Mapping[
        str, tuple[type[DeterministicSignalAdapter], Mapping[str, Any]]
    ] = {
        "PRIOR_VOLATILITY_RISK_OFF": (
            VolatilityRiskOffSignalAdapter,
            volatility_risk_off_signal_parameters(),
        ),
        "TECHNICAL_RISK_INSIDER_ENSEMBLE": (
            EnsembleSignalAdapter,
            ensemble_signal_parameters(),
        ),
    }

    def strategy_factory(
        adapter_type: type[DeterministicSignalAdapter],
        consumer: PITFeatureConsumer,
        liquidation_signal_at: str,
    ) -> DeterministicSignalAdapter:
        if adapter_type is EnsembleSignalAdapter:
            return EnsembleSignalAdapter(
                consumer,
                insider_specialist=insider,
                liquidation_signal_at=liquidation_signal_at,
            )
        return adapter_type(
            consumer, liquidation_signal_at=liquidation_signal_at
        )

    return _evaluate_train_rolling(
        repository_root,
        admitted_train_matrix_sha256=admitted_train_matrix_sha256,
        policies=policies,
        output=OUTPUT,
        status="TRAIN_ONLY_FORM4_ENSEMBLE_EVALUATION_COMPLETE",
        observe_policy_divergence=False,
        artifact_lineage={
            "revision": 3,
            "path": OUTPUT.as_posix(),
            "predecessor_paths": [
                (
                    ROOT
                    / "stage4/train_insider_ensemble_evaluation_v2.json"
                ).as_posix(),
            ],
            "reason": (
                "explicit legacy-taxonomy lineage and authoritative batch-interface revision"
            ),
        },
        report_metadata={
            "volatility_warmup_and_lineage": metadata,
            "form4_artifact_path": FORM4_ARTIFACT.as_posix(),
            "form4_artifact_sha256": expected_form4_artifact_sha256,
            "form4_record_count": len(form4["records"]),
            "form4_schema_version": form4["schema_version"],
            "form4_role_taxonomy": (
                "v1-substring-predates-v2-taxonomy"
                if form4["schema_version"] == LEGACY_ADMITTED_SCHEMA_VERSION
                else ROLE_TAXONOMY_VERSION
            ),
            "form4_role_weights_precomputed_in_artifact": True,
            "raw_source_quarantine_complete": False,
            "insider_lookback_complete_from": LOOKBACK_COMPLETE_FROM,
            "insider_covered_symbols": sorted(ISSUER_CIKS),
            "insider_uncovered_symbols": sorted(set(SYMBOLS) - set(ISSUER_CIKS)),
            "specialist_architecture": [
                "TECHNICAL",
                "RISK_REGIME",
                "SEC_FORM4_INSIDER",
            ],
            "baseline_class": "LEGACY_RESEARCH_ONLY_RISK_AS_ALPHA",
            "promotion_allowed": False,
            "validation_data_read": False,
            "untouched_test_included": False,
        },
        strategy_factory=strategy_factory,
        aggregate_metrics_augmenter=_annualized_session_metrics,
    )
