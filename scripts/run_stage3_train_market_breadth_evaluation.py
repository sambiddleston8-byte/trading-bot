#!/usr/bin/env python3
"""Run the offline TRAIN-only Stage 3 market-breadth evaluation."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.research.stage3_train_rolling_diagnostic import (  # noqa: E402
    BREADTH_OUTPUT,
    evaluate_train_market_breadth,
)


def _summary(report: dict[str, object]) -> dict[str, object]:
    return {
        "status": report["status"],
        "evaluation_sha256": report["evaluation_sha256"],
        "artifact_sha256": report["artifact_sha256"],
        "artifact_path": BREADTH_OUTPUT.as_posix(),
    }


def main() -> None:
    report = evaluate_train_market_breadth(ROOT)
    print(json.dumps(_summary(report), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
