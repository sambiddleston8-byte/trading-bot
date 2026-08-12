from __future__ import annotations

"""Read-only inventory for portfolio snapshots created before the decision ledger."""

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any


LEGACY_STATUS = "UNVALIDATED_LEGACY"
_FILENAME_TIMESTAMP = re.compile(r"research_portfolio_(\d{8}T\d{6}Z)\.json$")
_AUDIT_FIELDS = (
    "portfolio_id",
    "previous_portfolio_id",
    "execution_mode",
    "git_revision",
    "data_as_of",
    "decided_at",
    "model_versions",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _filename_timestamp(path: Path) -> str | None:
    match = _FILENAME_TIMESTAMP.fullmatch(path.name)
    if not match:
        return None
    value = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return value.isoformat()


def _base_entry(path: Path, source_directory: Path, digest: str | None) -> dict[str, Any]:
    timestamp = _filename_timestamp(path)
    try:
        source_size = path.stat().st_size
    except OSError:
        source_size = None
    entry: dict[str, Any] = {
        "source_path": path.relative_to(source_directory).as_posix(),
        "source_sha256": digest,
        "source_size_bytes": source_size,
        "classification": LEGACY_STATUS,
        "timestamp": timestamp,
        "timestamp_source": "filename" if timestamp else None,
        "portfolio_id": None,
        "version": None,
        "holdings_count": None,
        "missing_audit_fields": list(_AUDIT_FIELDS),
        "promotion_eligible": False,
        "promotion_blockers": [
            "unvalidated_legacy_source",
            "missing_immutable_decision_ledger",
        ],
        "parse_status": "PASS",
    }
    return entry


def _entry(path: Path, source_directory: Path, digest: str) -> dict[str, Any]:
    entry = _base_entry(path, source_directory, digest)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("top-level JSON value is not an object")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        entry["parse_status"] = "FAIL"
        entry["parse_error_type"] = type(error).__name__
        entry["promotion_blockers"].append("malformed_source")
        return entry

    entry["portfolio_id"] = payload.get("portfolio_id")
    entry["version"] = payload.get("version")
    holdings = payload.get("holdings")
    entry["holdings_count"] = len(holdings) if isinstance(holdings, list) else None
    entry["missing_audit_fields"] = [field for field in _AUDIT_FIELDS if not payload.get(field)]
    entry["promotion_blockers"].extend(
        f"missing_{field}" for field in entry["missing_audit_fields"]
    )
    if not isinstance(holdings, list):
        entry["promotion_blockers"].append("missing_or_invalid_holdings")
    return entry


def build_legacy_import_preview(source_directory: Path) -> dict[str, Any]:
    """Inspect JSON snapshots without modifying them or any persistence store."""
    source_directory = Path(source_directory)
    paths = sorted(source_directory.glob("research_portfolio_*.json"))
    before: dict[Path, str] = {}
    entries = []
    for path in paths:
        try:
            before[path] = _sha256(path)
        except OSError as error:
            entry = _base_entry(path, source_directory, None)
            entry["parse_status"] = "FAIL"
            entry["parse_error_type"] = type(error).__name__
            entry["promotion_blockers"].append("unreadable_source")
            entries.append(entry)
            continue
        entries.append(_entry(path, source_directory, before[path]))

    try:
        after = {path: _sha256(path) for path in before}
    except OSError as error:
        raise RuntimeError(
            "A legacy source became unreadable while the read-only preview was running"
        ) from error
    if before != after:
        raise RuntimeError("A legacy source changed while the read-only preview was running")

    hashes: dict[str, list[str]] = defaultdict(list)
    identifiers: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        if entry["source_sha256"]:
            hashes[entry["source_sha256"]].append(entry["source_path"])
        if entry["portfolio_id"]:
            identifiers[str(entry["portfolio_id"])].append(entry["source_path"])

    duplicate_hashes = {key: value for key, value in hashes.items() if len(value) > 1}
    identifier_collisions = {key: value for key, value in identifiers.items() if len(value) > 1}
    return {
        "schema_version": "1.0",
        "classification": LEGACY_STATUS,
        "read_only": True,
        "database_writes": False,
        "source_directory": str(source_directory),
        "summary": {
            "discovered": len(entries),
            "unique_source_hashes": len(hashes),
            "duplicate_hash_groups": len(duplicate_hashes),
            "portfolio_id_collision_groups": len(identifier_collisions),
            "malformed_sources": sum(item["parse_status"] == "FAIL" for item in entries),
            "promotion_eligible": 0,
        },
        "duplicate_hashes": duplicate_hashes,
        "portfolio_id_collisions": identifier_collisions,
        "entries": entries,
    }


def write_preview_manifest(preview: dict[str, Any], destination: Path) -> Path:
    """Write only the derived manifest; source snapshots remain untouched."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(preview, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination
