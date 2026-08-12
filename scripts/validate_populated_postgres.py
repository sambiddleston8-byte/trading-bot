from __future__ import annotations

"""Run comparison and restore gates against disposable synthetic databases."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from core.decision_ledger import InvestmentDecisionLedger
from core.persistence import PersistenceComparison, PostgresPortfolioRepository
from core.persistence.restore_verification import compare_database_snapshots
from core.persistence.synthetic_validation import (
    synthetic_changes,
    validate_synthetic_database_name,
)
from scripts.rehearse_postgres_restore import database_snapshot


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIRECTORY = ROOT / "data" / "backups" / "postgres_restore_rehearsals"


def _database_url(base_url: str, database: str) -> str:
    values = conninfo_to_dict(base_url)
    values["dbname"] = database
    return make_conninfo(**values)


def _local_records(change, ledger):
    entries = []
    for raw in change.decisions:
        entry = dict(raw)
        entry.update(
            {
                "portfolio_version": change.portfolio["portfolio_id"],
                "git_revision": change.portfolio["git_revision"],
                "data_as_of": change.portfolio["data_as_of"],
                "decided_at": change.portfolio["decided_at"],
                "execution_mode": change.portfolio["execution_mode"],
            }
        )
        entries.append(entry)
    return ledger.append_batch(entries)


def cleanup_databases(admin_url: str, names: tuple[tuple[str, str], ...]) -> dict[str, str]:
    cleanup = {key: "NOT_RUN" for _, key in names}
    try:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            for name, key in names:
                try:
                    admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                    cleanup[key] = "PASS"
                except Exception:
                    cleanup[key] = "FAIL"
    except Exception:
        for key, status in cleanup.items():
            if status == "NOT_RUN":
                cleanup[key] = "FAIL"
    return cleanup


def run_validation(base_url: str) -> tuple[Path, dict[str, object]]:
    ARTIFACT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    suffix = uuid4().hex[:8]
    source_name = validate_synthetic_database_name(f"synthetic_validation_source_{suffix}")
    restore_name = validate_synthetic_database_name(f"synthetic_validation_restore_{suffix}")
    started_at = datetime.now(timezone.utc)
    backup_path = ARTIFACT_DIRECTORY / f"synthetic_populated_{suffix}.dump"
    report_path = ARTIFACT_DIRECTORY / f"synthetic_populated_{suffix}.json"
    admin_url = _database_url(base_url, "postgres")
    results: list[dict[str, object]] = []
    verification = {"status": "FAIL", "mismatches": ["not_run"]}
    cleanup = {"source": "NOT_RUN", "restore": "NOT_RUN"}
    error_type = None
    try:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{source_name}"')
        source_url = _database_url(base_url, source_name)
        migration = (ROOT / "infra/postgres/migrations/0001_initial.sql").read_text(encoding="utf-8")
        with psycopg.connect(source_url) as connection:
            connection.execute(migration)
        repository = PostgresPortfolioRepository(lambda: psycopg.connect(source_url))
        with tempfile.TemporaryDirectory() as directory:
            ledger = InvestmentDecisionLedger(Path(directory) / "synthetic.jsonl")
            for change in synthetic_changes():
                local_records = _local_records(change, ledger)
                first = PersistenceComparison.persist(change, local_records, mode="comparison", repository=repository)
                repeated = PersistenceComparison.persist(change, local_records, mode="comparison", repository=repository)
                if first["status"] != "MATCH" or repeated["status"] != "MATCH":
                    raise RuntimeError("Synthetic comparison did not match")
                results.append({"portfolio_id": change.portfolio["portfolio_id"], "first": first["status"], "repeated": repeated["status"]})
            if len(ledger.verify()) != 4:
                raise RuntimeError("Synthetic local ledger count mismatch")

        descriptor = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as backup:
            subprocess.run(
                ["docker", "compose", "exec", "-T", "postgres", "pg_dump", "-U", "trading_bot", "-d", source_name, "--format=custom", "--no-owner", "--no-privileges"],
                cwd=ROOT, check=True, stdout=backup, stderr=subprocess.PIPE,
            )
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{restore_name}"')
        with backup_path.open("rb") as backup:
            subprocess.run(
                ["docker", "compose", "exec", "-T", "postgres", "pg_restore", "-U", "trading_bot", "-d", restore_name, "--no-owner", "--no-privileges"],
                cwd=ROOT, check=True, stdin=backup, stderr=subprocess.PIPE,
            )
        verification = compare_database_snapshots(
            database_snapshot(source_name), database_snapshot(restore_name)
        )
        if verification["status"] != "PASS":
            raise RuntimeError("Populated restore mismatch")
    except Exception as error:
        error_type = type(error).__name__
    finally:
        cleanup = cleanup_databases(
            admin_url,
            ((restore_name, "restore"), (source_name, "source")),
        )

    report = {
        "schema_version": "1.0",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "synthetic_only": True,
        "comparisons": results,
        "verification": verification,
        "backup_sha256": hashlib.sha256(backup_path.read_bytes()).hexdigest() if backup_path.exists() else None,
        "cleanup": cleanup,
        "error_type": error_type,
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if error_type or verification["status"] != "PASS" or set(cleanup.values()) != {"PASS"}:
        raise RuntimeError(f"Populated validation failed; see {report_path}")
    return report_path, report


if __name__ == "__main__":
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        raise SystemExit("Set TEST_DATABASE_URL for the local disposable validation.")
    path, result = run_validation(url)
    print(f"Synthetic comparisons: {len(result['comparisons'])} passed twice")
    print(f"Populated restore: {result['verification']['status']}")
    print(f"Disposable cleanup: {result['cleanup']}")
    print(f"Sanitized report: {path}")
