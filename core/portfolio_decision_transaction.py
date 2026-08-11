from __future__ import annotations

"""Recoverable local transaction for a portfolio snapshot and ledger batch."""

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core.decision_ledger import InvestmentDecisionLedger, LedgerIntegrityError


class PortfolioDecisionTransaction:
    """Coordinate two local files through a durable transaction journal.

    This is a local precursor to a database transaction. A crash leaves a
    ``.pending.json`` journal that can idempotently finish the ledger batch and
    snapshot on the next construction attempt.
    """

    def __init__(self, ledger: InvestmentDecisionLedger, portfolio_class: type):
        self.ledger = ledger
        self.portfolio_class = portfolio_class
        self.directory = ledger.path.parent / "portfolio_transactions"

    @contextmanager
    def _coordinator_lock(self):
        """Serialize journal recovery and persistence across local processes."""
        self.directory.mkdir(parents=True, exist_ok=True)
        lock_path = self.directory / ".coordinator.lock"
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def _atomic_json(cls, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        encoded = json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            written = 0
            while written < len(encoded):
                count = os.write(descriptor, encoded[written:])
                if count <= 0:
                    raise OSError("Transaction journal write made no progress.")
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        cls._fsync_directory(path.parent)

    def _save_snapshot(self, portfolio: dict[str, Any], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != portfolio:
                raise LedgerIntegrityError(
                    f"Portfolio snapshot collision at {path}."
                )
            return
        temporary = path.with_suffix(path.suffix + ".pending")
        self.portfolio_class.save(portfolio, path=temporary)
        descriptor = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        self._fsync_directory(path.parent)

    def _recover_unlocked(self, journal_path: Path) -> dict[str, Any]:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        records = self.ledger.append_batch(
            journal["ledger_entries"],
            allow_existing=True,
        )
        snapshot_path = Path(journal["snapshot_path"])
        self._save_snapshot(journal["portfolio"], snapshot_path)
        committed = {
            **journal,
            "state": "COMMITTED",
            "record_hashes": [record["record_hash"] for record in records],
        }
        committed_path = journal_path.with_name(
            journal_path.name.replace(".pending.json", ".committed.json")
        )
        self._atomic_json(committed_path, committed)
        journal_path.unlink()
        self._fsync_directory(journal_path.parent)
        return {
            "snapshot_path": snapshot_path,
            "ledger_records": records,
            "transaction_path": committed_path,
        }

    def recover(self, journal_path: Path) -> dict[str, Any]:
        with self._coordinator_lock():
            return self._recover_unlocked(journal_path)

    def recover_pending(self) -> list[dict[str, Any]]:
        if not self.directory.exists():
            return []
        with self._coordinator_lock():
            return [
                self._recover_unlocked(path)
                for path in sorted(self.directory.glob("*.pending.json"))
            ]

    def persist(
        self,
        *,
        transaction_id: str,
        portfolio: dict[str, Any],
        snapshot_path: Path,
        ledger_entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._coordinator_lock():
            pending_path = self.directory / f"{transaction_id}.pending.json"
            committed_path = self.directory / f"{transaction_id}.committed.json"
            if committed_path.exists():
                committed = json.loads(committed_path.read_text(encoding="utf-8"))
                records = self.ledger.append_batch(
                    committed["ledger_entries"],
                    allow_existing=True,
                )
                self._save_snapshot(portfolio, snapshot_path)
                return {
                    "snapshot_path": snapshot_path,
                    "ledger_records": records,
                    "transaction_path": committed_path,
                }
            self._atomic_json(
                pending_path,
                {
                    "transaction_id": transaction_id,
                    "state": "PREPARED",
                    "snapshot_path": str(snapshot_path),
                    "portfolio": portfolio,
                    "ledger_entries": ledger_entries,
                },
            )
            return self._recover_unlocked(pending_path)
