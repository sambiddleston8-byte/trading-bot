import pytest

from core.persistence.synthetic_validation import (
    synthetic_changes,
    validate_synthetic_database_name,
)


def test_synthetic_changes_are_complete_and_linked():
    construction, reallocation = synthetic_changes()
    assert construction.change_type == "CONSTRUCTION"
    assert reallocation.change_type == "REALLOCATION"
    assert reallocation.portfolio["previous_portfolio_id"] == construction.portfolio["portfolio_id"]
    assert all(item["synthetic"] for item in construction.portfolio["holdings"])
    assert len(construction.decisions) == len(reallocation.decisions) == 2
    assert construction.outbox_event["event_id"] != reallocation.outbox_event["event_id"]


@pytest.mark.parametrize("name", ["trading_bot", "restore_rehearsal_x", "synthetic_validation_", "synthetic_validation_bad-name"])
def test_synthetic_database_name_is_strict(name):
    with pytest.raises(ValueError, match="[Ss]ynthetic"):
        validate_synthetic_database_name(name)


def test_valid_synthetic_database_name():
    assert validate_synthetic_database_name("synthetic_validation_ab12") == "synthetic_validation_ab12"


class FakeAdmin:
    def __init__(self, fail_name=None):
        self.fail_name = fail_name
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query):
        self.executed.append(query)
        if self.fail_name and self.fail_name in query:
            raise RuntimeError("simulated drop failure")


def test_cleanup_attempts_both_databases_and_records_drop_failure(monkeypatch):
    from scripts import validate_populated_postgres as validation

    admin = FakeAdmin(fail_name="restore")
    monkeypatch.setattr(validation.psycopg, "connect", lambda *_args, **_kwargs: admin)
    result = validation.cleanup_databases(
        "redacted",
        (("synthetic_validation_restore_x", "restore"), ("synthetic_validation_source_x", "source")),
    )

    assert result == {"restore": "FAIL", "source": "PASS"}
    assert len(admin.executed) == 2


def test_cleanup_connection_failure_is_recorded(monkeypatch):
    from scripts import validate_populated_postgres as validation

    def fail_connect(*_args, **_kwargs):
        raise RuntimeError("simulated admin connection failure")

    monkeypatch.setattr(validation.psycopg, "connect", fail_connect)
    result = validation.cleanup_databases(
        "redacted",
        (("synthetic_validation_restore_x", "restore"), ("synthetic_validation_source_x", "source")),
    )

    assert result == {"restore": "FAIL", "source": "FAIL"}
