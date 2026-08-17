#!/usr/bin/env python3
"""Run the offline three-fold Stage 3 TRAIN-only policy diagnostic."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.research.stage3_train_rolling_diagnostic import (  # noqa: E402
    evaluate_train_rolling,
)


if __name__ == "__main__":
    print(json.dumps(evaluate_train_rolling(ROOT), indent=2, sort_keys=True))
