import json

import pytest

from core.decision_ledger import InvestmentDecisionLedger, LedgerIntegrityError
from core.portfolio_decision_transaction import PortfolioDecisionTransaction


class GoodPortfolio:
    @staticmethod
    def save(portfolio, path):
        path.write_text(json.dumps(portfolio, sort_keys=True), encoding="utf-8")


class FailingPortfolio:
    @staticmethod
    def save(portfolio, path):
        raise OSError("simulated snapshot failure")


def ledger_entry():
    return {
        "ticker": "NVDA",
        "decision": "BUY",
        "decision_payload": {"confidence": 72},
        "model_versions": [],
        "data_as_of": "2026-08-11T12:00:00+00:00",
        "portfolio_version": "PORT-RECOVERY-001",
        "git_revision": "abc123",
        "decided_at": "2026-08-11T12:01:00+00:00",
        "decision_id": "PORT-RECOVERY-001-NVDA",
    }


def test_pending_transaction_recovers_without_duplicate_ledger_records(tmp_path):
    ledger = InvestmentDecisionLedger(tmp_path / "decisions.jsonl")
    snapshot = tmp_path / "portfolios" / "portfolio.json"
    portfolio = {"portfolio_id": "PORT-RECOVERY-001", "holdings": []}
    failing = PortfolioDecisionTransaction(ledger, FailingPortfolio)

    with pytest.raises(OSError, match="simulated snapshot failure"):
        failing.persist(
            transaction_id="PORT-RECOVERY-001",
            portfolio=portfolio,
            snapshot_path=snapshot,
            ledger_entries=[ledger_entry()],
        )

    assert len(ledger.verify()) == 1
    assert not snapshot.exists()
    assert len(list(failing.directory.glob("*.pending.json"))) == 1

    recovered = PortfolioDecisionTransaction(ledger, GoodPortfolio).recover_pending()

    assert len(recovered) == 1
    assert snapshot.exists()
    assert json.loads(snapshot.read_text(encoding="utf-8")) == portfolio
    assert len(ledger.verify()) == 1
    assert not list(failing.directory.glob("*.pending.json"))
    assert len(list(failing.directory.glob("*.committed.json"))) == 1


def test_repeat_persist_reuses_original_snapshot(tmp_path):
    ledger = InvestmentDecisionLedger(tmp_path / "decisions.jsonl")
    transaction = PortfolioDecisionTransaction(ledger, GoodPortfolio)
    portfolio = {"portfolio_id": "PORT-RECOVERY-001", "holdings": []}
    original = tmp_path / "portfolios" / "original.json"
    duplicate = tmp_path / "portfolios" / "duplicate.json"

    first = transaction.persist(
        transaction_id="PORT-RECOVERY-001",
        portfolio=portfolio,
        snapshot_path=original,
        ledger_entries=[ledger_entry()],
    )
    repeated = transaction.persist(
        transaction_id="PORT-RECOVERY-001",
        portfolio=portfolio,
        snapshot_path=duplicate,
        ledger_entries=[ledger_entry()],
    )

    assert first["snapshot_path"] == original
    assert repeated["snapshot_path"] == original
    assert original.exists()
    assert not duplicate.exists()
    assert len(ledger.verify()) == 1

    with pytest.raises(LedgerIntegrityError, match="different content"):
        transaction.persist(
            transaction_id="PORT-RECOVERY-001",
            portfolio={**portfolio, "holdings": [{"ticker": "AAPL"}]},
            snapshot_path=duplicate,
            ledger_entries=[ledger_entry()],
        )


def test_fresh_transaction_rejects_duplicate_decision_ids(tmp_path):
    ledger = InvestmentDecisionLedger(tmp_path / "decisions.jsonl")
    transaction = PortfolioDecisionTransaction(ledger, GoodPortfolio)

    with pytest.raises(LedgerIntegrityError, match="more than once"):
        transaction.persist(
            transaction_id="PORT-RECOVERY-001",
            portfolio={"portfolio_id": "PORT-RECOVERY-001", "holdings": []},
            snapshot_path=tmp_path / "portfolio.json",
            ledger_entries=[ledger_entry(), ledger_entry()],
        )

    assert not (tmp_path / "portfolio.json").exists()


def test_recovery_finishes_after_snapshot_but_before_commit_journal(tmp_path, monkeypatch):
    ledger = InvestmentDecisionLedger(tmp_path / "decisions.jsonl")
    transaction = PortfolioDecisionTransaction(ledger, GoodPortfolio)
    snapshot = tmp_path / "portfolio.json"
    portfolio = {"portfolio_id": "PORT-RECOVERY-001", "holdings": []}
    original_atomic_json = PortfolioDecisionTransaction._atomic_json
    original_descriptor = PortfolioDecisionTransaction.__dict__["_atomic_json"]
    writes = 0

    def fail_committed_journal(_cls, path, value):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated committed-journal failure")
        return original_atomic_json(path, value)

    monkeypatch.setattr(
        PortfolioDecisionTransaction,
        "_atomic_json",
        classmethod(fail_committed_journal),
    )
    with pytest.raises(OSError, match="committed-journal failure"):
        transaction.persist(
            transaction_id="PORT-RECOVERY-001",
            portfolio=portfolio,
            snapshot_path=snapshot,
            ledger_entries=[ledger_entry()],
        )

    assert snapshot.exists()
    assert len(list(transaction.directory.glob("*.pending.json"))) == 1

    monkeypatch.setattr(
        PortfolioDecisionTransaction,
        "_atomic_json",
        original_descriptor,
    )
    recovered = transaction.recover_pending()

    assert len(recovered) == 1
    assert len(ledger.verify()) == 1
    assert not list(transaction.directory.glob("*.pending.json"))
