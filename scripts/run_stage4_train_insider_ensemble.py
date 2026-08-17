#!/usr/bin/env python3
"""Run the fixed TRAIN-only Form 4 ensemble comparison."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.research.stage4_train_insider_ensemble_evaluation import (
    evaluate_train_insider_ensemble,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    report = evaluate_train_insider_ensemble(args.repository_root)
    metric_names = {
        "return": "fold_reset_chained_total_return",
        "cagr": "annualized_session_cagr",
        "sharpe": "pooled_daily_sharpe_ratio",
        "max_drawdown": "fold_reset_chained_maximum_drawdown",
        "win_rate": "win_rate",
        "annual_turnover": "session_weighted_annual_turnover",
        "trade_count": "completed_trade_count",
    }
    compact = {
        "status": report["status"],
        "evaluation_sha256": report["evaluation_sha256"],
        "artifact_sha256": report["artifact_sha256"],
        "metrics": {
            policy: {
                scenario: {
                    public_name: values[source_name]
                    for public_name, source_name in metric_names.items()
                }
                for scenario, values in report["policies"][policy]["aggregate"].items()
            }
            for policy in report["policies"]
        },
        "execution_sample": report["policies"]
        ["TECHNICAL_RISK_INSIDER_ENSEMBLE"]["aggregate"]["BASE"]["trade_log"][:5],
    }
    print(json.dumps(compact, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
