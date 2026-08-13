from copy import deepcopy

import pytest

from core.persistence import assess_postgres_cutover_readiness


def observations(count=30):
    return [
        {"status": "MATCH", "portfolio_id": f"PORT-{index:03d}", "mismatches": []}
        for index in range(count)
    ]


def restore():
    return {
        "completed_at": "2026-08-12T12:00:00+00:00",
        "verification": {"status": "PASS", "mismatches": []},
    }


def snapshot():
    return {
        "schema_migrations": ["0001_initial"],
        "ledger_record_count": 50,
        "ledger_record_hash": "a" * 64,
        "undelivered_outbox_events": 0,
        "failed_or_running_job_runs": 0,
    }


def assess(**overrides):
    values = {
        "comparison_observations": observations(),
        "restore_report": restore(),
        "database_snapshot": snapshot(),
        "local_decision_record_count": 50,
        "local_decision_tail_hash": "a" * 64,
        "assessed_at": "2026-08-13T12:00:00+00:00",
    }
    values.update(overrides)
    return assess_postgres_cutover_readiness(**values)


def test_complete_evidence_only_allows_human_consideration():
    result = assess()
    assert result["status"] == "EVIDENCE_READY_FOR_HUMAN_DECISION"
    assert result["consecutive_match_count"] == 30
    assert len(result["evidence_snapshot_sha256"]) == 64
    assert result["postgres_authoritative"] is False
    assert result["persistence_mode_changed"] is False
    assert result["deployment_performed"] is False
    assert result["human_cutover_approval_required"] is True
    assert result["live_trading_enabled"] is False


@pytest.mark.parametrize(
    "change,fragment",
    [
        (lambda values: values.update(comparison_observations=observations(29)), "At least 30"),
        (lambda values: values["comparison_observations"][0].update(status="MISMATCH", mismatches=["hash"]), "exact MATCH"),
        (lambda values: values["comparison_observations"][1].update(portfolio_id="PORT-000"), "distinct"),
        (lambda values: values["restore_report"]["verification"].update(status="FAIL"), "successful isolated"),
        (lambda values: values["restore_report"].update(completed_at="2026-07-01T00:00:00+00:00"), "seven days"),
        (lambda values: values["database_snapshot"].update(schema_migrations=[]), "migrations"),
        (lambda values: values["database_snapshot"].update(ledger_record_count=49), "counts"),
        (lambda values: values["database_snapshot"].update(ledger_record_hash="b" * 64), "tail hashes"),
        (lambda values: values["database_snapshot"].update(undelivered_outbox_events=1), "outbox"),
        (lambda values: values["database_snapshot"].update(failed_or_running_job_runs=1), "job"),
    ],
)
def test_any_missing_cutover_evidence_blocks(change, fragment):
    values = {
        "comparison_observations": observations(),
        "restore_report": restore(),
        "database_snapshot": snapshot(),
        "local_decision_record_count": 50,
        "local_decision_tail_hash": "a" * 64,
        "assessed_at": "2026-08-13T12:00:00+00:00",
    }
    change(values)
    result = assess_postgres_cutover_readiness(**values)
    assert result["status"] == "BLOCKED"
    assert fragment in " ".join(result["reasons"])
    assert result["postgres_authoritative"] is False


def test_evidence_fingerprint_changes_with_comparison_history():
    first = assess()
    changed = observations()
    changed[-1]["portfolio_id"] = "PORT-NEW"
    second = assess(comparison_observations=changed)
    assert second["status"] == "EVIDENCE_READY_FOR_HUMAN_DECISION"
    assert first["evidence_snapshot_sha256"] != second["evidence_snapshot_sha256"]
