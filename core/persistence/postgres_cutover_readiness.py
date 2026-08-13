from __future__ import annotations

"""Pure fail-closed gate before PostgreSQL authority may be considered."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Mapping, Sequence

from core.decision_ledger import canonical_timestamp


POLICY_VERSION = "postgres-authority-cutover-readiness-v2"
MINIMUM_CONSECUTIVE_COMPARISONS = 30
MAX_RESTORE_AGE = timedelta(days=7)
REQUIRED_MIGRATIONS = {"0001_initial"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _timestamp(value: Any) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def assess_postgres_cutover_readiness(
    *,
    comparison_observations: Sequence[Mapping[str, Any]],
    restore_report: Mapping[str, Any],
    database_snapshot: Mapping[str, Any],
    local_decision_record_count: int,
    local_decision_tail_hash: str,
) -> dict[str, Any]:
    """Return evidence readiness only; never changes persistence mode."""

    assessed = datetime.now(timezone.utc)
    reasons: list[str] = []
    observations = list(comparison_observations)
    sequence_valid = True
    sequence_numbers: list[int] = []
    observation_times: list[datetime] = []
    for item in observations:
        try:
            sequence_numbers.append(int(item.get("sequence_number")))
            observation_times.append(_timestamp(item.get("observed_at")))
        except (TypeError, ValueError, OverflowError):
            sequence_valid = False
            break
    if sequence_valid and observations:
        sequence_valid = (
            all(
                current == previous + 1
                for previous, current in zip(sequence_numbers, sequence_numbers[1:])
            )
            and all(
                current > previous
                for previous, current in zip(observation_times, observation_times[1:])
            )
            and observation_times[-1] <= assessed
        )
    if not sequence_valid or not observations:
        reasons.append(
            "Comparison history requires contiguous sequence numbers and strictly "
            "increasing, non-future observation times."
        )
    trailing_matches = 0
    for item in reversed(observations):
        if item.get("status") == "MATCH" and item.get("mismatches") == []:
            trailing_matches += 1
        else:
            break
    if trailing_matches < MINIMUM_CONSECUTIVE_COMPARISONS:
        reasons.append(
            f"At least {MINIMUM_CONSECUTIVE_COMPARISONS} consecutive comparison observations are required."
        )
    trailing_observations = observations[-trailing_matches:] if trailing_matches else []
    portfolio_ids = [str(item.get("portfolio_id") or "") for item in trailing_observations]
    if any(not value for value in portfolio_ids) or len(set(portfolio_ids)) != len(portfolio_ids):
        reasons.append("Trailing matching observations require distinct nonblank portfolio IDs.")

    verification = restore_report.get("verification")
    if not isinstance(verification, Mapping) or verification.get("status") != "PASS":
        reasons.append("A successful isolated PostgreSQL restore rehearsal is required.")
    completed_at = restore_report.get("completed_at")
    try:
        restored = _timestamp(completed_at)
    except (TypeError, ValueError, OverflowError):
        restored = None
        reasons.append("Restore rehearsal completion time is missing or invalid.")
    if restored is not None and not assessed - MAX_RESTORE_AGE <= restored <= assessed:
        reasons.append("Restore rehearsal must be no more than seven days old and not future-dated.")

    migrations = database_snapshot.get("schema_migrations")
    if not isinstance(migrations, Sequence) or isinstance(migrations, (str, bytes)):
        reasons.append("Database migration inventory is missing.")
    elif not REQUIRED_MIGRATIONS.issubset({str(item) for item in migrations}):
        reasons.append("Required database migrations are not all applied.")

    try:
        database_count = int(database_snapshot.get("ledger_record_count"))
        local_count = int(local_decision_record_count)
    except (TypeError, ValueError, OverflowError):
        database_count = local_count = -1
        reasons.append("Ledger record counts are invalid.")
    database_hash = str(database_snapshot.get("ledger_record_hash") or "")
    local_hash = str(local_decision_tail_hash or "")
    if database_count < 0 or database_count != local_count:
        reasons.append("PostgreSQL and local decision-ledger counts must match exactly.")
    if (
        len(database_hash) != 64
        or len(local_hash) != 64
        or database_hash != local_hash
    ):
        reasons.append("PostgreSQL and local decision-ledger tail hashes must match exactly.")
    try:
        undelivered_outbox_events = int(
            database_snapshot.get("undelivered_outbox_events", -1)
        )
        failed_or_running_job_runs = int(
            database_snapshot.get("failed_or_running_job_runs", -1)
        )
    except (TypeError, ValueError, OverflowError):
        undelivered_outbox_events = failed_or_running_job_runs = -1
        reasons.append("Outbox and job-run counters must be valid integers.")
    if undelivered_outbox_events != 0:
        reasons.append("Every committed outbox event must be delivered before cutover consideration.")
    if failed_or_running_job_runs != 0:
        reasons.append("No failed or still-running database job may remain at cutover consideration.")

    reasons = sorted(set(reasons))
    evidence = {
        "policy_version": POLICY_VERSION,
        "comparison_observations": observations,
        "restore_verification": verification,
        "restore_completed_at": completed_at,
        "schema_migrations": list(migrations) if isinstance(migrations, Sequence) and not isinstance(migrations, (str, bytes)) else [],
        "database_ledger_record_count": database_count,
        "local_ledger_record_count": local_count,
        "database_ledger_tail_hash": database_hash,
        "local_ledger_tail_hash": local_hash,
        "undelivered_outbox_events": undelivered_outbox_events,
        "failed_or_running_job_runs": failed_or_running_job_runs,
    }
    ready = not reasons
    return {
        "policy_version": POLICY_VERSION,
        "status": "EVIDENCE_READY_FOR_HUMAN_DECISION" if ready else "BLOCKED",
        "assessed_at": assessed.isoformat(),
        "reasons": reasons,
        "consecutive_match_count": trailing_matches,
        "minimum_consecutive_match_count": MINIMUM_CONSECUTIVE_COMPARISONS,
        "evidence_snapshot_sha256": hashlib.sha256(
            _canonical_json(evidence).encode("utf-8")
        ).hexdigest(),
        "postgres_authoritative": False,
        "persistence_mode_changed": False,
        "deployment_performed": False,
        "aws_spend_authorized": False,
        "broker_access_enabled": False,
        "live_trading_enabled": False,
        "human_cutover_approval_required": True,
    }
