import pytest
import json

from core.persistence.restore_verification import (
    VERIFICATION_FIELDS,
    compare_database_snapshots,
    validate_restore_database_name,
)


def snapshot():
    return {
        "schema_migrations": ["0001_initial"],
        "public_tables": 10,
        "portfolio_versions": 1,
        "portfolio_holdings": 2,
        "investment_decisions": 2,
        "decision_model_versions": 4,
        "outbox_events": 1,
        "job_runs": 1,
        "research_artifacts": 2,
        "ledger_anchors": 1,
        "ledger_record_count": 2,
        "ledger_record_hash": "a" * 64,
    }


def test_matching_snapshot_passes():
    result = compare_database_snapshots(snapshot(), snapshot())
    assert result["status"] == "PASS"
    assert result["mismatches"] == []


def test_mismatch_names_only_changed_fields():
    restored = snapshot()
    restored["ledger_record_hash"] = "b" * 64
    restored["portfolio_holdings"] = 3
    result = compare_database_snapshots(snapshot(), restored)
    assert result["status"] == "FAIL"
    assert result["mismatches"] == ["portfolio_holdings", "ledger_record_hash"]
    assert set(result["source"]) == set(VERIFICATION_FIELDS)


@pytest.mark.parametrize("name", ["trading_bot", "temporary", "restore_rehearsal_", "restore_rehearsal_bad-name", "restore_rehearsal_café"])
def test_restore_database_name_must_be_disposable(name):
    with pytest.raises(ValueError, match="[Rr]estore"):
        validate_restore_database_name(name)


def test_valid_restore_database_name_is_accepted():
    assert validate_restore_database_name("restore_rehearsal_20260812_ab12") == "restore_rehearsal_20260812_ab12"


def test_early_database_failure_still_writes_sanitized_report(tmp_path, monkeypatch):
    from scripts import rehearse_postgres_restore as rehearsal

    monkeypatch.setattr(rehearsal, "ARTIFACT_DIRECTORY", tmp_path)

    def fail_psql(*_args, **_kwargs):
        import subprocess

        raise subprocess.CalledProcessError(7, ["psql"])

    monkeypatch.setattr(rehearsal, "_psql", fail_psql)
    with pytest.raises(RuntimeError, match="Restore rehearsal failed"):
        rehearsal.rehearse()

    reports = list(tmp_path.glob("*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["error_phase"] == "snapshot_source"
    assert report["error_returncode"] == 7
    assert report["verification"]["status"] == "FAIL"
