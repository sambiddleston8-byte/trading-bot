#!/usr/bin/env python3
from __future__ import annotations
import json, os, stat, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.orchestration.stage2_bounded_capture import execute_capture

KEY_PATH = Path("/private/tmp/massive_api_key.txt")

def _key() -> str:
    descriptor = os.open(KEY_PATH, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or stat.S_IMODE(details.st_mode) != 0o600 or details.st_nlink != 1:
            raise ValueError("temporary key must be an owner-only regular file")
        raw = os.read(descriptor, 502)
    finally: os.close(descriptor)
    value = raw.decode("ascii").rstrip("\r\n")
    if not value or "\n" in value or "\r" in value or len(value) > 500:
        raise ValueError("temporary key must contain one bounded ASCII line")
    return value

def main() -> int:
    result_code = 1
    try:
        result = execute_capture(repository_root=ROOT, api_key=_key())
        print(json.dumps(result, indent=2, sort_keys=True))
        result_code = 0
    except Exception as error:
        print(f"Stage 2 bounded capture failed: {error}", file=sys.stderr)
    finally:
        try:
            KEY_PATH.unlink(missing_ok=True)
            if KEY_PATH.exists(): raise OSError("temporary key still exists")
        except OSError as error:
            print(f"Stage 2 key deletion failed: {error}", file=sys.stderr)
            result_code = 1
    return result_code

if __name__ == "__main__": raise SystemExit(main())
