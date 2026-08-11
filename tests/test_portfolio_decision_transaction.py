import json

import pytest

from core.decision_ledger import InvestmentDecisionLedger
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
