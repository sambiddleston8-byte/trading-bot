#!/usr/bin/env python3
"""Run the offline Stage 3 two-session momentum-confirmed evaluation."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.research.stage3_feature_strategy_evaluation import (  # noqa: E402
    evaluate_momentum_confirmed,
)


if __name__ == "__main__":
    print(json.dumps(evaluate_momentum_confirmed(ROOT), indent=2, sort_keys=True))
