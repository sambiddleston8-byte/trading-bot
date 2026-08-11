from pathlib import Path

import pytest

from core.persistence import PortfolioChange


ROOT = Path(__file__).resolve().parents[1]


def test_compose_is_private_manual_and_non_authoritative():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    research_service = compose.split("  research-cycle:", 1)[1].split(
        "  portfolio-monitor:", 1
    )[0]
    monitor_service = compose.split("  portfolio-monitor:", 1)[1].split(
        "volumes:", 1
    )[0]
    web_service = compose.split("  web:", 1)[1].split("  research-cycle:", 1)[0]

    assert '"127.0.0.1:8501:8501"' in compose
    assert '"127.0.0.1:5432:5432"' in compose
    assert 'profiles: ["manual"]' in research_service
    assert 'profiles: ["manual"]' in monitor_service
    assert "profiles:" not in web_service
    assert compose.count("PERSISTENCE_MODE: local-files") == 3
    assert compose.count("app_data:/app/data") == 3
    assert "scheduler" not in compose.lower()
    assert "alpaca" not in compose.lower()
    assert "aws" not in compose.lower()


def test_initial_schema_preserves_transaction_and_ledger_invariants():
    sql = (
        ROOT / "infra/postgres/migrations/0001_initial.sql"
    ).read_text(encoding="utf-8")

    required_tables = {
        "job_runs",
        "research_artifacts",
        "portfolio_versions",
        "portfolio_holdings",
        "investment_decisions",
        "decision_model_versions",
        "outbox_events",
        "ledger_state",
        "ledger_anchors",
    }
    for table in required_tables:
        assert f"CREATE TABLE {table}" in sql
    portfolio_sql = sql.split("CREATE TABLE portfolio_versions", 1)[1].split(
        "CREATE TABLE portfolio_holdings", 1
    )[0]
    decision_sql = sql.split("CREATE TABLE investment_decisions", 1)[1].split(
        "CREATE TABLE decision_model_versions", 1
    )[0]
    assert "CHECK (data_as_of <= decided_at)" in portfolio_sql
    assert "CHECK (data_as_of <= decided_at)" in decision_sql
    assert "previous_hash char(64)" in decision_sql
    assert "record_hash char(64)" in decision_sql
    immutable_tables = {
        "investment_decisions",
        "decision_model_versions",
        "portfolio_versions",
        "portfolio_holdings",
        "research_artifacts",
        "ledger_anchors",
    }
    for table in immutable_tables:
        assert f"BEFORE UPDATE OR DELETE ON {table}" in sql
    assert sql.startswith("BEGIN;")
    assert sql.rstrip().endswith("COMMIT;")


def test_repository_contract_requires_decisions_for_every_change():
    common = {
        "portfolio": {"portfolio_id": "PORT-001"},
        "outbox_event": {"event_id": "EVENT-001"},
    }
    construction = PortfolioChange(
        **common,
        decisions=[{"decision_id": "DEC-001"}],
        change_type="CONSTRUCTION",
    )
    reallocation = PortfolioChange(
        **common,
        decisions=[{"decision_id": "DEC-002"}],
        change_type="REALLOCATION",
    )

    assert construction.change_type == "CONSTRUCTION"
    assert reallocation.change_type == "REALLOCATION"
    with pytest.raises(ValueError, match="must include decision records"):
        PortfolioChange(**common, decisions=[], change_type="REALLOCATION")
