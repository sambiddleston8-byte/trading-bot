from __future__ import annotations

import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from core.decision_ledger import GENESIS_HASH, InvestmentDecisionLedger
from core.persistence import PortfolioChange, PostgresPortfolioRepository


ROOT = Path(__file__).resolve().parents[1]


class RedactedDatabaseUrl(str):
    def __repr__(self) -> str:
        return "'<redacted test database URL>'"


def _test_database_url() -> str | None:
    return os.getenv("TEST_DATABASE_URL")


@pytest.fixture()
def database_url():
    url = _test_database_url()
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
    schema = f"test_{uuid4().hex}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        yield RedactedDatabaseUrl(f"{url}?options=-csearch_path%3D{schema}")
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture()
def migrated_database(database_url):
    migration = (ROOT / "infra/postgres/migrations/0001_initial.sql").read_text(
        encoding="utf-8"
    )
    with psycopg.connect(database_url) as connection:
        connection.execute(migration)
    return database_url


def _change(portfolio_id="PORT-PG-001", event_id="EVENT-PG-001"):
    decided_at = "2026-08-11T12:01:00+00:00"
    data_as_of = "2026-08-11T12:00:00+00:00"
    return PortfolioChange(
        change_type="CONSTRUCTION",
        portfolio={
            "portfolio_id": portfolio_id,
            "previous_portfolio_id": None,
            "execution_mode": "RECORD_ONLY",
            "git_revision": "abc123",
            "model_versions": [{"component": "portfolio", "version": "0.7"}],
            "data_as_of": data_as_of,
            "decided_at": decided_at,
            "holdings": [{"ticker": "NVDA", "weight": 0.6}],
        },
        decisions=[
            {
                "decision_id": f"{portfolio_id}-NVDA",
                "ticker": "NVDA",
                "decision": "BUY",
                "decision_payload": {"confidence": 72},
                "model_versions": [
                    {
                        "component": "investment_committee",
                        "version": "2.7",
                        "provider": "openai",
                        "model": "test-model",
                        "prompt_version": "committee-v1",
                    }
                ],
                "data_as_of": data_as_of,
                "decided_at": decided_at,
            }
        ],
        outbox_event={
            "event_id": event_id,
            "event_type": "PORTFOLIO_CONSTRUCTED",
            "payload": {"portfolio_id": portfolio_id},
        },
    )


def test_postgres_change_is_atomic_hash_chained_and_idempotent(migrated_database):
    repository = PostgresPortfolioRepository(
        lambda: psycopg.connect(migrated_database)
    )
    change = _change()

    first = repository.persist_change(change)
    repeated = repository.persist_change(change)

    assert repeated == first
    with psycopg.connect(migrated_database) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM portfolio_versions),
                (SELECT count(*) FROM portfolio_holdings),
                (SELECT count(*) FROM investment_decisions),
                (SELECT count(*) FROM decision_model_versions),
                (SELECT count(*) FROM outbox_events)
            """
        ).fetchone()
        previous_hash, record_hash = connection.execute(
            "SELECT previous_hash, record_hash FROM investment_decisions"
        ).fetchone()
        ledger_hash, ledger_count = connection.execute(
            "SELECT record_hash, record_count FROM ledger_state"
        ).fetchone()

    assert counts == (1, 1, 1, 1, 1)
    assert previous_hash == GENESIS_HASH
    assert ledger_hash == record_hash
    assert ledger_count == 1


def test_postgres_hash_matches_local_ledger(migrated_database, tmp_path):
    change = _change()
    repository = PostgresPortfolioRepository(lambda: psycopg.connect(migrated_database))
    repository.persist_change(change)
    entry = dict(change.decisions[0])
    entry["portfolio_version"] = change.portfolio["portfolio_id"]
    entry["git_revision"] = change.portfolio["git_revision"]
    local_record = InvestmentDecisionLedger(tmp_path / "decisions.jsonl").append_batch(
        [entry]
    )[0]

    with psycopg.connect(migrated_database) as connection:
        postgres_hash = connection.execute(
            "SELECT record_hash FROM investment_decisions"
        ).fetchone()[0]

    assert postgres_hash == local_record["record_hash"]


def test_separate_changes_extend_one_chain(migrated_database):
    repository = PostgresPortfolioRepository(lambda: psycopg.connect(migrated_database))
    repository.persist_change(_change())
    second = _change("PORT-PG-002", "EVENT-PG-002")
    second.portfolio["previous_portfolio_id"] = "PORT-PG-001"
    repository.persist_change(second)

    with psycopg.connect(migrated_database) as connection:
        rows = connection.execute(
            "SELECT previous_hash, record_hash FROM investment_decisions ORDER BY sequence_number"
        ).fetchall()
        ledger_count = connection.execute(
            "SELECT record_count FROM ledger_state"
        ).fetchone()[0]

    assert rows[1][0] == rows[0][1]
    assert ledger_count == 2


def test_concurrent_duplicate_is_idempotent(migrated_database):
    repository = PostgresPortfolioRepository(lambda: psycopg.connect(migrated_database))
    change = _change()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(repository.persist_change, [change, change]))

    assert results[0] == results[1]
    with psycopg.connect(migrated_database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM portfolio_versions"
        ).fetchone()[0] == 1


def test_failed_decision_rolls_back_the_whole_change(migrated_database):
    repository = PostgresPortfolioRepository(
        lambda: psycopg.connect(migrated_database)
    )
    invalid = _change()
    invalid.decisions[0]["data_as_of"] = "2026-08-12T12:00:00+00:00"

    with pytest.raises(psycopg.errors.CheckViolation):
        repository.persist_change(invalid)

    with psycopg.connect(migrated_database) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM portfolio_versions),
                (SELECT count(*) FROM investment_decisions),
                (SELECT count(*) FROM outbox_events)
            """
        ).fetchone()
        ledger = connection.execute(
            "SELECT record_hash, record_count FROM ledger_state"
        ).fetchone()

    assert counts == (0, 0, 0)
    assert ledger == (GENESIS_HASH, 0)


def test_existing_portfolio_rejects_different_audit_records(migrated_database):
    repository = PostgresPortfolioRepository(
        lambda: psycopg.connect(migrated_database)
    )
    repository.persist_change(_change())

    with pytest.raises(ValueError, match="different audit records"):
        repository.persist_change(_change(event_id="EVENT-DIFFERENT"))


def test_existing_portfolio_rejects_changed_holdings(migrated_database):
    repository = PostgresPortfolioRepository(lambda: psycopg.connect(migrated_database))
    repository.persist_change(_change())
    changed = _change()
    changed.portfolio["holdings"][0]["weight"] = 0.5

    with pytest.raises(ValueError, match="different audit records"):
        repository.persist_change(changed)


def test_existing_portfolio_rejects_changed_supersedes_reference(migrated_database):
    repository = PostgresPortfolioRepository(lambda: psycopg.connect(migrated_database))
    repository.persist_change(_change())
    changed = _change()
    changed.decisions[0]["supersedes_decision_id"] = "DEC-OLDER"

    with pytest.raises(ValueError, match="different audit records"):
        repository.persist_change(changed)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("execution_mode", "LIVE", "Unsupported execution mode"),
        ("decided_at", "2026-08-11T12:01:00", "explicit timezone"),
    ],
)
def test_invalid_safety_metadata_is_rejected(
    migrated_database, field, value, message
):
    repository = PostgresPortfolioRepository(lambda: psycopg.connect(migrated_database))
    change = _change()
    change.portfolio[field] = value

    with pytest.raises(ValueError, match=message):
        repository.persist_change(change)
