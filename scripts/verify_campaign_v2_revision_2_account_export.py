#!/usr/bin/env python3
"""Verify the fixed Campaign v2 R2 account export inbox without network or writes."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.orchestration.campaign_v2_revision_2_account_export_verifier import (
    verify_account_export_offline,
)


def main() -> int:
    try:
        result = verify_account_export_offline(ROOT)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Campaign v2 R2 account-export verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
