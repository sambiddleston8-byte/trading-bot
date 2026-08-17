"""TRAIN-only Stage 4 comparison of breadth with a volatility risk-off gate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from core.research.pit_feature_signal_adapter import (
    DeterministicSignalAdapter,
    MarketBreadthSignalAdapter,
    SYMBOLS,
    VolatilityRiskOffSignalAdapter,
    market_breadth_signal_parameters,
    volatility_risk_off_signal_parameters,
)
from core.research.stage3_feature_strategy_evaluation import ADMITTED_MATRIX_SHA256, ROOT
from core.research.stage3_train_rolling_diagnostic import (
    FOLD_COUNT,
    FOLD_SIZE,
    _evaluate_train_rolling,
)


OUTPUT = ROOT / "stage4/train_volatility_risk_off_evaluation_committed_v2.json"
PREDECESSOR = ROOT / "stage4/train_volatility_risk_off_evaluation_committed_v1.json"
POLICIES: Mapping[
    str, tuple[type[DeterministicSignalAdapter], Mapping[str, Any]]
] = {
    "PRIOR_MARKET_BREADTH": (
        MarketBreadthSignalAdapter,
        market_breadth_signal_parameters(),
    ),
    "VOLATILITY_RISK_OFF": (
        VolatilityRiskOffSignalAdapter,
        volatility_risk_off_signal_parameters(),
    ),
}


def _evaluation_metadata(
    repository_root: Path, *, admitted_train_matrix_sha256: str
) -> dict[str, Any]:
    matrix_path = repository_root / ROOT / "stage3/technical_features/train_matrix.json"
    matrix = json.loads(matrix_path.read_text())
    if matrix.get("matrix_sha256") != admitted_train_matrix_sha256:
        raise ValueError("TRAIN feature matrix differs from its admitted pin")
    session_dates: dict[str, set[str]] = {}
    for row in matrix["rows"]:
        inputs = row.get("provenance", {}).get("input_rows", ())
        if not inputs:
            raise ValueError("TRAIN feature row lacks source-session provenance")
        session_dates.setdefault(row["effective_at"], set()).add(
            inputs[-1]["session_date"]
        )
    if any(len(days) != 1 for days in session_dates.values()):
        raise ValueError("TRAIN feature effective_at does not map to one source session")
    feature_sessions = sorted(next(iter(days)) for days in session_dates.values())
    if len(feature_sessions) != FOLD_COUNT * FOLD_SIZE:
        raise ValueError("TRAIN feature sessions do not match the fixed fold contract")
    folds = [
        feature_sessions[index : index + FOLD_SIZE]
        for index in range(0, len(feature_sessions), FOLD_SIZE)
    ]
    train_path = repository_root / ROOT / "stage2/clean_feature_store/train.json"
    train_bytes = train_path.read_bytes()
    qualification = json.loads(
        (repository_root / ROOT / "stage2/qualification_report.json").read_bytes()
    )
    if hashlib.sha256(train_bytes).hexdigest() != qualification["artifacts"]["TRAIN"]:
        raise ValueError("TRAIN partition differs from qualification")
    train = json.loads(train_bytes)
    unsupported_actions = sorted(
        {
            str(action.get("action_type", "")).strip() or "<MISSING>"
            for action in train["corporate_actions"]
            if action.get("action_type") != "CASH_DIVIDEND"
        }
    )
    if unsupported_actions:
        raise ValueError(
            "Stage 4 raw-volatility evaluation permits CASH_DIVIDEND only; "
            f"unsupported corporate-action types: {unsupported_actions}"
        )
    required = 14 + 20 + 1
    counts = {
        fold[0]: {
            symbol: sum(
                row["session_date"] <= fold[0]
                for row in train["bars"]
                if row["symbol"] == symbol
            )
            for symbol in SYMBOLS
        }
        for fold in folds
    }
    if any(
        count < required
        for symbols in counts.values()
        for count in symbols.values()
    ):
        raise ValueError(
            "Stage 4 evaluation begins before the fixed volatility warm-up completes"
        )
    return {
        "minimum_history_bars": required,
        "available_history_bars_at_fold_start": counts,
        "insufficient_history_suppressions_expected": 0,
        "raw_bar_source": (
            "qualified TRAIN clean-feature-store artifact pinned by "
            "source_artifact_sha256"
        ),
        "current_atr_reconciliation": (
            "bar-derived ATR-14 must equal admitted same-session atr_14"
        ),
        "corporate_action_policy": (
            "CASH_DIVIDEND records are allowed; every other action type is rejected "
            "before execution. Dividend-related unadjusted price gaps remain part of "
            "realized ATR by design."
        ),
        "signal_diagnostics_source": (
            "actual deep-copied strategy instances executed by "
            "GuardrailedBacktestEngine"
        ),
    }


def evaluate_train_volatility_risk_off(
    repository_root: Path,
    *,
    admitted_train_matrix_sha256: str = ADMITTED_MATRIX_SHA256["TRAIN"],
) -> dict[str, Any]:
    metadata = _evaluation_metadata(
        repository_root,
        admitted_train_matrix_sha256=admitted_train_matrix_sha256,
    )
    return _evaluate_train_rolling(
        repository_root,
        admitted_train_matrix_sha256=admitted_train_matrix_sha256,
        policies=POLICIES,
        output=OUTPUT,
        status="TRAIN_ONLY_VOLATILITY_RISK_OFF_EVALUATION_COMPLETE",
        observe_policy_divergence=False,
        artifact_lineage={
            "revision": 2,
            "path": OUTPUT.as_posix(),
            "predecessor_paths": [PREDECESSOR.as_posix()],
            "reason": (
                "review-hardening revision captures diagnostics from the exact "
                "engine-executed strategy instance and pins source-session, Decimal, "
                "warm-up, and corporate-action boundaries"
            ),
        },
        report_metadata={"volatility_warmup_and_lineage": metadata},
    )
