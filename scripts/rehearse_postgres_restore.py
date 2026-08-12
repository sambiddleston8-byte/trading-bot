from __future__ import annotations

"""Back up PostgreSQL and verify a restore in a disposable database."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone
from uuid import uuid4

from core.persistence.restore_verification import (
    compare_database_snapshots,
    validate_restore_database_name,
)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIRECTORY = ROOT / "data" / "backups" / "postgres_restore_rehearsals"
SOURCE_DATABASE = "trading_bot"
DATABASE_USER = "trading_bot"


def _run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        list(args),
        cwd=ROOT,
        check=True,
        capture_output=capture,
        text=capture,
    )
    return result.stdout.strip() if capture else ""


def _psql(database: str, query: str) -> str:
    return _run(
        "docker", "compose", "exec", "-T", "postgres", "psql",
        "-X", "-v", "ON_ERROR_STOP=1", "-U", DATABASE_USER,
        "-d", database, "-At", "-c", query, capture=True,
    )


def database_snapshot(database: str) -> dict[str, object]:
    query = """
    SELECT json_build_object(
      'schema_migrations', COALESCE((SELECT json_agg(version ORDER BY version) FROM schema_migrations), '[]'::json),
      'public_tables', (SELECT count(*) FROM pg_tables WHERE schemaname = 'public'),
      'portfolio_versions', (SELECT count(*) FROM portfolio_versions),
      'portfolio_holdings', (SELECT count(*) FROM portfolio_holdings),
      'investment_decisions', (SELECT count(*) FROM investment_decisions),
      'decision_model_versions', (SELECT count(*) FROM decision_model_versions),
      'outbox_events', (SELECT count(*) FROM outbox_events),
      'job_runs', (SELECT count(*) FROM job_runs),
      'research_artifacts', (SELECT count(*) FROM research_artifacts),
      'ledger_anchors', (SELECT count(*) FROM ledger_anchors),
      'ledger_record_count', (SELECT record_count FROM ledger_state WHERE singleton = true),
      'ledger_record_hash', (SELECT record_hash FROM ledger_state WHERE singleton = true)
    );
    """
    return json.loads(_psql(database, query))


def rehearse() -> tuple[Path, dict[str, object]]:
    ARTIFACT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    run_suffix = uuid4().hex[:8]
    restore_database = validate_restore_database_name(
        f"restore_rehearsal_{stamp.lower()}_{run_suffix}"
    )
    backup_path = ARTIFACT_DIRECTORY / f"trading_bot_{stamp}_{run_suffix}.dump"
    report_path = ARTIFACT_DIRECTORY / f"restore_rehearsal_{stamp}_{run_suffix}.json"
    source: dict[str, object] = {}
    restored: dict[str, object] = {}
    verification: dict[str, object] = {"status": "FAIL", "mismatches": ["not_run"]}
    cleanup_status = "NOT_RUN"
    error_type = None
    error_phase = None
    error_returncode = None
    phase = "snapshot_source"
    try:
        source = database_snapshot(SOURCE_DATABASE)
        phase = "backup"
        descriptor = os.open(
            backup_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as backup:
            subprocess.run(
                [
                    "docker", "compose", "exec", "-T", "postgres", "pg_dump",
                    "-U", DATABASE_USER, "-d", SOURCE_DATABASE, "--format=custom",
                    "--no-owner", "--no-privileges",
                ],
                cwd=ROOT,
                check=True,
                stdout=backup,
                stderr=subprocess.PIPE,
            )
        phase = "create_restore_database"
        _psql("postgres", f'CREATE DATABASE "{restore_database}";')
    except subprocess.CalledProcessError as error:
        error_type = type(error).__name__
        error_phase = phase
        error_returncode = error.returncode
        if phase == "backup":
            backup_path.unlink(missing_ok=True)
    finally:
        try:
            if backup_path.exists() and error_type is None:
                phase = "restore"
                with backup_path.open("rb") as backup:
                    restore = subprocess.run(
                        [
                            "docker", "compose", "exec", "-T", "postgres", "pg_restore",
                            "-U", DATABASE_USER, "-d", restore_database, "--no-owner",
                            "--no-privileges",
                        ],
                        cwd=ROOT,
                        check=True,
                        stdin=backup,
                        stderr=subprocess.PIPE,
                    )
                restored = database_snapshot(restore_database)
                phase = "verify"
                verification = compare_database_snapshots(source, restored)
        except subprocess.CalledProcessError as error:
            error_type = type(error).__name__
            error_phase = phase
            error_returncode = error.returncode
        finally:
            try:
                _psql(
                    "postgres",
                    f'DROP DATABASE IF EXISTS "{restore_database}" WITH (FORCE);',
                )
                cleanup_status = "PASS"
            except subprocess.CalledProcessError:
                cleanup_status = "FAIL"

    digest = hashlib.sha256(backup_path.read_bytes()).hexdigest() if backup_path.exists() else None
    report = {
        "schema_version": "1.0",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "source_database": SOURCE_DATABASE,
        "restore_database_prefix": "restore_rehearsal_",
        "backup_file": backup_path.name,
        "backup_sha256": digest,
        "verification": verification,
        "cleanup_status": cleanup_status,
        "error_type": error_type,
        "error_phase": error_phase,
        "error_returncode": error_returncode,
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if verification.get("status") != "PASS" or cleanup_status != "PASS":
        raise RuntimeError(f"Restore rehearsal failed; see {report_path}")
    return report_path, report


if __name__ == "__main__":
    path, result = rehearse()
    print(f"Restore rehearsal: {result['verification']['status']}")
    print(f"Temporary database cleanup: {result['cleanup_status']}")
    print(f"Sanitized report: {path}")
