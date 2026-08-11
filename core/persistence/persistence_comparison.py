from __future__ import annotations

"""Opt-in shadow persistence; local files remain authoritative."""

import os
from typing import Any

import psycopg

from core.persistence.portfolio_repository import PortfolioChange
from core.persistence.postgres_portfolio_repository import PostgresPortfolioRepository


class PersistenceComparison:
    @classmethod
    def persist(
        cls,
        change: PortfolioChange,
        local_records: list[dict[str, Any]],
        *,
        mode: str | None = None,
        database_url: str | None = None,
        repository: PostgresPortfolioRepository | None = None,
    ) -> dict[str, Any]:
        resolved_mode = str(mode or os.getenv("PERSISTENCE_MODE") or "local-files")
        if resolved_mode == "local-files":
            return {"status": "DISABLED", "mode": resolved_mode}
        if resolved_mode != "comparison":
            return {"status": "FAILED", "mode": resolved_mode, "reason": "Unsupported persistence mode"}
        resolved_url = database_url or os.getenv("DATABASE_URL")
        if repository is None and not resolved_url:
            return {"status": "FAILED", "mode": resolved_mode, "reason": "DATABASE_URL is required"}
        try:
            target = repository or PostgresPortfolioRepository(
                lambda: psycopg.connect(str(resolved_url))
            )
            persisted = target.persist_change(change)
            audit = target.audit_change(persisted.portfolio_id)
        except Exception as error:
            return {
                "status": "FAILED",
                "mode": resolved_mode,
                "reason": f"PostgreSQL comparison failed ({type(error).__name__})",
            }

        local_ids = [str(record["decision_id"]) for record in local_records]
        local_hashes = [str(record["record_hash"]) for record in local_records]
        expected_holdings = sorted(
            [dict(item) for item in change.portfolio.get("holdings") or []],
            key=lambda item: str(item.get("ticker") or "").upper(),
        )
        mismatches = []
        if audit["portfolio"] != dict(change.portfolio):
            mismatches.append("portfolio")
        if audit["holdings"] != expected_holdings:
            mismatches.append("holdings")
        if audit["decision_ids"] != local_ids:
            mismatches.append("decision_ids")
        if audit["record_hashes"] != local_hashes:
            mismatches.append("record_hashes")
        return {
            "status": "MATCH" if not mismatches else "MISMATCH",
            "mode": resolved_mode,
            "portfolio_id": persisted.portfolio_id,
            "mismatches": mismatches,
        }
