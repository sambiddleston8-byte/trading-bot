import pytest

from core.persistence import PersistenceComparison, PortfolioChangeBuilder
from core.persistence.portfolio_repository import PersistedPortfolioChange
from core.application.portfolio_construction_service import PortfolioConstructionService


class JsonPortfolio:
    VERSION = "test-portfolio"

    @staticmethod
    def save(portfolio, path):
        import json

        path.write_text(json.dumps(portfolio, sort_keys=True), encoding="utf-8")


def test_construction_normalizes_one_shared_change():
    entry = {
        "decision_id": "PORT-001-NVDA",
        "ticker": "NVDA",
        "decision": "BUY",
        "decision_payload": {"confidence": 72},
        "model_versions": [{"component": "portfolio", "version": "0.7"}],
        "data_as_of": "2026-08-11T12:00:00+00:00",
        "decided_at": "2026-08-11T12:01:00+00:00",
        "portfolio_version": "PORT-001",
        "git_revision": "abc123",
    }
    change = PortfolioChangeBuilder.construction(
        {"portfolio_id": "PORT-001", "holdings": [{"ticker": "NVDA", "weight": 1.0}]},
        [entry],
    )

    assert change.change_type == "CONSTRUCTION"
    assert change.portfolio["execution_mode"] == "RECORD_ONLY"
    assert change.portfolio["git_revision"] == "abc123"
    assert change.outbox_event["event_id"] == "EVENT-PORT-001"


def test_construction_aggregates_distinct_model_versions():
    common = {
        "decision_payload": {},
        "data_as_of": "2026-08-11T12:00:00+00:00",
        "decided_at": "2026-08-11T12:01:00+00:00",
        "portfolio_version": "PORT-001",
        "git_revision": "abc123",
        "decision": "BUY",
    }
    change = PortfolioChangeBuilder.construction(
        {"portfolio_id": "PORT-001", "holdings": []},
        [
            {**common, "decision_id": "D1", "ticker": "NVDA", "model_versions": [{"component": "research", "version": "1"}]},
            {**common, "decision_id": "D2", "ticker": "AAPL", "model_versions": [{"component": "research", "version": "2"}]},
        ],
    )

    assert [item["version"] for item in change.portfolio["model_versions"]] == ["1", "2"]


def test_reallocation_creates_new_version_and_per_ticker_decisions():
    kwargs = {
        "previous_portfolio": {"portfolio_id": "PORT-001"},
        "updated_portfolio": {
            "portfolio_id": "PORT-001",
            "holdings": [
                {"ticker": "NVDA", "weight": 0.4},
                {"ticker": "AAPL", "weight": 0.6},
            ],
        },
        "changes": [
            {"ticker": "NVDA", "before_weight": 0.5, "after_weight": 0.4, "reasons": ["risk"]},
            {"ticker": "AAPL", "before_weight": 0.5, "after_weight": 0.6, "reasons": ["evidence"]},
        ],
        "checked_at": "2026-08-11T12:00:00+00:00",
        "applied_at": "2026-08-11T12:01:00+00:00",
        "git_revision": "abc123",
        "monitor_version": "1.4",
        "portfolio_version": "0.7",
    }
    first = PortfolioChangeBuilder.reallocation(**kwargs)
    repeated = PortfolioChangeBuilder.reallocation(**kwargs)

    assert first == repeated
    assert first.change_type == "REALLOCATION"
    assert first.portfolio["portfolio_id"] != "PORT-001"
    assert first.portfolio["previous_portfolio_id"] == "PORT-001"
    assert [item["ticker"] for item in first.decisions] == ["NVDA", "AAPL"]
    assert all(item["decision"] == "REALLOCATE" for item in first.decisions)


def test_reallocation_without_changes_writes_nothing():
    with pytest.raises(ValueError, match="at least one allocation change"):
        PortfolioChangeBuilder.reallocation(
            previous_portfolio={"portfolio_id": "PORT-001"},
            updated_portfolio={"holdings": []},
            changes=[],
            checked_at="2026-08-11T12:00:00+00:00",
            applied_at="2026-08-11T12:01:00+00:00",
            git_revision="abc123",
            monitor_version="1.4",
            portfolio_version="0.7",
        )


def test_reallocation_rejects_timezone_naive_cutoff():
    with pytest.raises(ValueError, match="explicit timezone"):
        PortfolioChangeBuilder.reallocation(
            previous_portfolio={"portfolio_id": "PORT-001"},
            updated_portfolio={"holdings": []},
            changes=[{"ticker": "NVDA"}],
            checked_at="2026-08-11T12:00:00",
            applied_at="2026-08-11T12:01:00+00:00",
            git_revision="abc123",
            monitor_version="1.4",
            portfolio_version="0.7",
        )


def test_reallocation_uses_local_snapshot_and_ledger_transaction(tmp_path, monkeypatch):
    monkeypatch.setattr(
        PortfolioConstructionService,
        "DECISION_LEDGER_PATH",
        tmp_path / "decisions.jsonl",
    )
    monkeypatch.setattr(
        PortfolioConstructionService,
        "PORTFOLIO_DIRECTORY",
        tmp_path / "portfolios",
    )
    previous = {"portfolio_id": "PORT-001", "holdings": []}
    applied = {
        "portfolio": {
            "version": "test-portfolio",
            "holdings": [{"ticker": "NVDA", "weight": 1.0}],
            "updated_at": "2026-08-11T12:01:00+00:00",
            "last_rebalance": {"applied_at": "2026-08-11T12:01:00+00:00"},
        },
        "changes": [
            {"ticker": "NVDA", "before_weight": 0.9, "after_weight": 1.0}
        ],
    }

    result = PortfolioConstructionService.persist_reallocation(
        previous_portfolio=previous,
        applied_result=applied,
        checked_at="2026-08-11T12:00:00+00:00",
        monitor_version="1.4",
        portfolio_class=JsonPortfolio,
    )

    assert result["snapshot_path"].exists()
    assert result["portfolio"]["previous_portfolio_id"] == "PORT-001"
    assert result["ledger_records"][0]["decision"] == "REALLOCATE"
    assert result["ledger_records"][0]["portfolio_version"] == result["portfolio"]["portfolio_id"]
    assert result["persistence_comparison"] == {
        "status": "DISABLED",
        "mode": "local-files",
    }


class FakePostgresRepository:
    def __init__(self, change, records, *, mismatch=None, fail=False):
        self.change = change
        self.records = records
        self.mismatch = mismatch
        self.fail = fail

    def persist_change(self, change):
        if self.fail:
            raise RuntimeError("secret database details must not escape")
        assert change == self.change
        return PersistedPortfolioChange(
            str(change.portfolio["portfolio_id"]),
            tuple(record["decision_id"] for record in self.records),
            str(change.outbox_event["event_id"]),
        )

    def audit_change(self, _portfolio_id):
        holdings = sorted(
            [dict(item) for item in self.change.portfolio.get("holdings") or []],
            key=lambda item: item["ticker"],
        )
        audit = {
            "portfolio": dict(self.change.portfolio),
            "holdings": holdings,
            "decision_ids": [record["decision_id"] for record in self.records],
            "record_hashes": [record["record_hash"] for record in self.records],
        }
        if self.mismatch:
            audit[self.mismatch] = []
        return audit


def test_comparison_mode_reports_match_without_promoting_database():
    change = PortfolioChangeBuilder.construction(
        {"portfolio_id": "PORT-001", "holdings": [{"ticker": "NVDA", "weight": 1.0}]},
        [{
            "decision_id": "PORT-001-NVDA", "ticker": "NVDA", "decision": "BUY",
            "decision_payload": {}, "model_versions": [],
            "data_as_of": "2026-08-11T12:00:00+00:00",
            "decided_at": "2026-08-11T12:01:00+00:00",
            "portfolio_version": "PORT-001", "git_revision": "abc123",
        }],
    )
    records = [{"decision_id": "PORT-001-NVDA", "record_hash": "a" * 64}]
    result = PersistenceComparison.persist(
        change,
        records,
        mode="comparison",
        repository=FakePostgresRepository(change, records),
    )

    assert result["status"] == "MATCH"
    assert result["mismatches"] == []


def test_comparison_mode_reports_mismatch_and_sanitizes_failures():
    change = PortfolioChangeBuilder.construction(
        {"portfolio_id": "PORT-001", "holdings": []},
        [{
            "decision_id": "PORT-001-NVDA", "ticker": "NVDA", "decision": "BUY",
            "decision_payload": {}, "model_versions": [],
            "data_as_of": "2026-08-11T12:00:00+00:00",
            "decided_at": "2026-08-11T12:01:00+00:00",
            "portfolio_version": "PORT-001", "git_revision": "abc123",
        }],
    )
    records = [{"decision_id": "PORT-001-NVDA", "record_hash": "a" * 64}]
    mismatch = PersistenceComparison.persist(
        change, records, mode="comparison",
        repository=FakePostgresRepository(change, records, mismatch="record_hashes"),
    )
    failure = PersistenceComparison.persist(
        change, records, mode="comparison",
        repository=FakePostgresRepository(change, records, fail=True),
    )

    assert mismatch["status"] == "MISMATCH"
    assert mismatch["mismatches"] == ["record_hashes"]
    assert failure["status"] == "FAILED"
    assert "secret" not in failure["reason"]
