from __future__ import annotations

"""Install an opt-in daily macOS schedule for the research-cycle script.

No schedule is installed merely by adding this file. ``--load`` is explicit
because a full refresh consumes provider quota and should be run at a cadence
the owner understands. The scheduled process writes only project research and
paper-portfolio records; it has no brokerage capability.
"""

import argparse
import os
import plistlib
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.sambiddleston.tradingbot.portfolio-refresh"
LAUNCH_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_DIRECTORY = PROJECT_ROOT / "data" / "research" / "scheduler_logs"


def schedule_payload(hour: int, minute: int, candidate_batch_size: int) -> dict:
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    script = PROJECT_ROOT / "scripts" / "run_portfolio_research_cycle.py"
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(python),
            str(script),
            "--candidate-batch-size",
            str(candidate_batch_size),
        ],
        "WorkingDirectory": str(PROJECT_ROOT),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "ProcessType": "Background",
        "StandardOutPath": str(LOG_DIRECTORY / "portfolio_refresh.out.log"),
        "StandardErrorPath": str(LOG_DIRECTORY / "portfolio_refresh.err.log"),
    }


def write_schedule(hour: int, minute: int, candidate_batch_size: int) -> Path:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Hour must be 0–23 and minute must be 0–59.")
    if candidate_batch_size < 1:
        raise ValueError("Candidate batch size must be at least one.")
    LAUNCH_AGENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    with LAUNCH_AGENT_PATH.open("wb") as handle:
        plistlib.dump(schedule_payload(hour, minute, candidate_batch_size), handle)
    return LAUNCH_AGENT_PATH


def load_schedule(path: Path) -> None:
    uid = str(os.getuid())
    domain = f"gui/{uid}"
    subprocess.run(["launchctl", "bootout", domain, str(path)], check=False)
    subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an opt-in daily research refresh for the proposed paper portfolio."
    )
    parser.add_argument("--hour", type=int, default=18)
    parser.add_argument("--minute", type=int, default=0)
    parser.add_argument("--candidate-batch-size", type=int, default=12)
    parser.add_argument(
        "--load",
        action="store_true",
        help="Activate the saved schedule immediately. Without this flag, the file is written but not activated.",
    )
    args = parser.parse_args()
    path = write_schedule(args.hour, args.minute, args.candidate_batch_size)
    print(f"Schedule written: {path}")
    if args.load:
        load_schedule(path)
        print(f"Schedule active: daily at {args.hour:02d}:{args.minute:02d} local time.")
    else:
        print("Schedule is not active yet. Re-run with --load when the daily time and provider usage are approved.")


if __name__ == "__main__":
    main()
