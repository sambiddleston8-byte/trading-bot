#!/usr/bin/env python3
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.research.stage3_feature_strategy_evaluation import evaluate

print(json.dumps(evaluate(ROOT), indent=2, sort_keys=True))
