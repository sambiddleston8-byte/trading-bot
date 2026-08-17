#!/usr/bin/env python3
"""Run and summarize the offline TRAIN-only Stage 4 volatility evaluation."""
from __future__ import annotations

import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from core.research.stage4_train_volatility_evaluation import (  # noqa: E402
    OUTPUT,
    evaluate_train_volatility_risk_off,
)


def _summary(report: dict[str, object]) -> dict[str, object]:
    return {
        "status": report["status"],
        "evaluation_sha256": report["evaluation_sha256"],
        "artifact_sha256": report["artifact_sha256"],
        "artifact_path": OUTPUT.as_posix(),
    }


def main() -> None:
    report = evaluate_train_volatility_risk_off(REPOSITORY_ROOT)
    print(json.dumps(_summary(report), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
