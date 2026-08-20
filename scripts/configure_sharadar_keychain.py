#!/usr/bin/env python3
from __future__ import annotations

"""Store the Sharadar API key through macOS's hidden Keychain prompt."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts._sharadar_keychain import store_interactively


def main() -> int:
    try:
        store_interactively()
        print("Sharadar API key stored in macOS Keychain.")
        return 0
    except (OSError, RuntimeError) as error:
        print(f"Sharadar Keychain setup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
