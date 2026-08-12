from __future__ import annotations

"""Create a local, gitignored preview of pre-ledger portfolio snapshots."""

from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.persistence.legacy_import_preview import build_legacy_import_preview, write_preview_manifest


SOURCE_DIRECTORY = ROOT / "data" / "research" / "portfolios"
OUTPUT_DIRECTORY = ROOT / "data" / "backups" / "legacy_import_previews"


def run() -> tuple[Path, dict[str, object]]:
    preview = build_legacy_import_preview(SOURCE_DIRECTORY)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = write_preview_manifest(preview, OUTPUT_DIRECTORY / f"legacy_import_preview_{stamp}.json")
    return path, preview["summary"]


if __name__ == "__main__":
    manifest, summary = run()
    print(f"Legacy snapshots discovered: {summary['discovered']}")
    print(f"Promotion eligible: {summary['promotion_eligible']}")
    print(f"Malformed sources: {summary['malformed_sources']}")
    print(f"Duplicate hash groups: {summary['duplicate_hash_groups']}")
    print(f"Local manifest: {manifest}")
