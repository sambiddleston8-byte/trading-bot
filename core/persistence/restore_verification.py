from __future__ import annotations

"""Pure comparison helpers for PostgreSQL restore rehearsals."""

from typing import Any, Mapping


VERIFICATION_FIELDS = (
    "schema_migrations",
    "public_tables",
    "portfolio_versions",
    "portfolio_holdings",
    "investment_decisions",
    "decision_model_versions",
    "outbox_events",
    "job_runs",
    "research_artifacts",
    "ledger_anchors",
    "ledger_record_count",
    "ledger_record_hash",
)


def compare_database_snapshots(
    source: Mapping[str, Any], restored: Mapping[str, Any]
) -> dict[str, Any]:
    mismatches = [
        field for field in VERIFICATION_FIELDS if source.get(field) != restored.get(field)
    ]
    return {
        "status": "PASS" if not mismatches else "FAIL",
        "mismatches": mismatches,
        "source": {field: source.get(field) for field in VERIFICATION_FIELDS},
        "restored": {field: restored.get(field) for field in VERIFICATION_FIELDS},
    }


def validate_restore_database_name(name: str) -> str:
    if name == "trading_bot" or not name.startswith("restore_rehearsal_"):
        raise ValueError(
            "Restore database must use the restore_rehearsal_ safety prefix."
        )
    suffix = name.removeprefix("restore_rehearsal_")
    compact = suffix.replace("_", "")
    if not compact or not compact.isascii() or not compact.isalnum():
        raise ValueError("Restore database name contains unsupported characters.")
    return name
