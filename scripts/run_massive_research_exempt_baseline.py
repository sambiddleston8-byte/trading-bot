#!/usr/bin/env python3
from __future__ import annotations

"""Run the explicitly authorized, non-evidentiary Massive baseline package."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research.massive_research_exempt_replay import (
    ResearchExemptionLedger,
    admit_train_validation,
    execute_untouched_once,
    load_campaign_store,
    register_exemption,
    vectorbt_fixed_parameter_screen,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Register the explicit research exemption, admit only TRAIN/VALIDATION "
            "to a separate research store, screen the sole preregistered parameters "
            "and optionally consume the untouched test exactly once."
        )
    )
    parser.add_argument(
        "--campaign-root",
        type=Path,
        default=ROOT / "data/research/massive_quarantine",
    )
    parser.add_argument(
        "--research-store-root",
        type=Path,
        default=ROOT / "data/research/massive_research_exempt_admitted",
    )
    parser.add_argument(
        "--audit-ledger",
        type=Path,
        default=ROOT / "data/research/massive_quarantine/research_exemption_audit.jsonl",
    )
    parser.add_argument("--authorized-by", default="SAM_AND_PAT_USER_AUTHORIZATION")
    parser.add_argument(
        "--consume-untouched-test",
        action="store_true",
        help="Irreversibly consume the sole preregistered untouched evaluation.",
    )
    return parser.parse_args()


def main() -> int:
    args = arguments()
    try:
        prereg, store = load_campaign_store(
            repository_root=ROOT,
            campaign_root=args.campaign_root,
            admitted_store_root=args.research_store_root,
        )
        plan = prereg.verify()
        if len(plan) != 1:
            raise ValueError("the baseline requires exactly one preregistration")
        captures = store.verify()
        ledger = ResearchExemptionLedger(args.audit_ledger)
        exemption = register_exemption(
            ledger=ledger,
            preregistration=plan[0],
            captures=captures,
            authorized_by=args.authorized_by,
        )
        admitted = admit_train_validation(
            ledger=ledger,
            store=store,
            admitted_root=args.research_store_root,
        )
        screen = vectorbt_fixed_parameter_screen(admitted)
        output = {
            "exemption": exemption,
            "train_validation_research_bar_count": len(admitted),
            "vectorbt_screen": screen,
            "untouched_test_consumed": False,
        }
        if args.consume_untouched_test:
            output["untouched_test"] = execute_untouched_once(
                ledger=ledger,
                store=store,
                admitted_bars=admitted,
                vectorbt_screen=screen,
            )
            output["untouched_test_consumed"] = True
        print(json.dumps(output, sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Research-exempt Massive baseline failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
