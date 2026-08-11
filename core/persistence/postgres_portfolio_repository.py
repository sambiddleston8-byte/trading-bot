from __future__ import annotations

"""Transactional PostgreSQL persistence for portfolio changes.

This adapter is deliberately not selected by the application yet.  It gives the
construction and reallocation paths one atomic database boundary while the
existing local-file implementation remains authoritative.
"""

from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from core.decision_ledger import (
    GENESIS_HASH,
    LEDGER_SCHEMA_VERSION,
    canonical_timestamp,
    normalize_model_version,
)
from core.persistence.portfolio_repository import (
    PersistedPortfolioChange,
    PortfolioChange,
)


ConnectionFactory = Callable[[], Connection[Any]]
ALLOWED_EXECUTION_MODES = {"RECORD_ONLY", "PAPER_ONLY"}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _record_hash(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _required(mapping: Mapping[str, Any], key: str) -> Any:
    value = mapping.get(key)
    if value is None or value == "":
        raise ValueError(f"Missing required persistence field: {key}")
    return value


def _as_mapping_list(value: Any, field: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be a sequence of objects")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"{field} must contain only objects")
        result.append(dict(item))
    return result


class PostgresPortfolioRepository:
    """Persist an audited portfolio change in one PostgreSQL transaction."""

    def __init__(self, connection_factory: ConnectionFactory):
        self._connection_factory = connection_factory

    def persist_change(self, change: PortfolioChange) -> PersistedPortfolioChange:
        portfolio = dict(change.portfolio)
        portfolio_id = str(_required(portfolio, "portfolio_id"))
        git_revision = str(_required(portfolio, "git_revision"))
        data_as_of = canonical_timestamp(_required(portfolio, "data_as_of"))
        decided_at = canonical_timestamp(_required(portfolio, "decided_at"))
        execution_mode = str(portfolio.get("execution_mode") or "RECORD_ONLY")
        if execution_mode not in ALLOWED_EXECUTION_MODES:
            raise ValueError(f"Unsupported execution mode: {execution_mode}")

        holdings = _as_mapping_list(portfolio.get("holdings"), "holdings")
        decisions = [dict(item) for item in change.decisions]
        event = dict(change.outbox_event)
        event_id = str(_required(event, "event_id"))
        decision_ids = tuple(str(_required(item, "decision_id")) for item in decisions)
        if len(set(decision_ids)) != len(decision_ids):
            raise ValueError("Decision IDs must be unique within a portfolio change")

        connection = self._connection_factory()
        try:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (portfolio_id,),
                    )
                    existing = cursor.execute(
                        "SELECT 1 FROM portfolio_versions WHERE portfolio_id = %s",
                        (portfolio_id,),
                    ).fetchone()
                    if existing:
                        return self._existing_result(
                            cursor=cursor,
                            portfolio=portfolio,
                            holdings=holdings,
                            decisions=decisions,
                            event=event,
                            expected_decision_ids=decision_ids,
                        )

                    ledger_hash, ledger_count = cursor.execute(
                        """
                        SELECT record_hash, record_count
                        FROM ledger_state
                        WHERE singleton = true
                        FOR UPDATE
                        """
                    ).fetchone()

                    cursor.execute(
                        """
                        INSERT INTO portfolio_versions (
                            portfolio_id, previous_portfolio_id, change_type,
                            execution_mode, git_revision, model_versions,
                            data_as_of, decided_at, payload
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            portfolio_id,
                            portfolio.get("previous_portfolio_id"),
                            change.change_type,
                            execution_mode,
                            git_revision,
                            Jsonb(portfolio.get("model_versions") or []),
                            data_as_of,
                            decided_at,
                            Jsonb(portfolio),
                        ),
                    )
                    self._insert_holdings(cursor, portfolio_id, holdings)

                    previous_hash = str(ledger_hash or GENESIS_HASH)
                    for decision in decisions:
                        previous_hash = self._insert_decision(
                            cursor=cursor,
                            portfolio_id=portfolio_id,
                            git_revision=git_revision,
                            portfolio_decided_at=decided_at,
                            portfolio_data_as_of=data_as_of,
                            default_execution_mode=execution_mode,
                            previous_hash=previous_hash,
                            decision=decision,
                        )

                    cursor.execute(
                        """
                        UPDATE ledger_state
                        SET record_hash = %s, record_count = %s
                        WHERE singleton = true
                        """,
                        (previous_hash, int(ledger_count) + len(decisions)),
                    )
                    cursor.execute(
                        """
                        INSERT INTO outbox_events (
                            event_id, aggregate_type, aggregate_id,
                            event_type, payload
                        ) VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            event_id,
                            str(event.get("aggregate_type") or "PORTFOLIO"),
                            str(event.get("aggregate_id") or portfolio_id),
                            str(_required(event, "event_type")),
                            Jsonb(event.get("payload") or {}),
                        ),
                    )
            return PersistedPortfolioChange(portfolio_id, decision_ids, event_id)
        finally:
            connection.close()

    def audit_change(self, portfolio_id: str) -> dict[str, Any]:
        """Return the committed fields needed for local/PostgreSQL comparison."""
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                portfolio = cursor.execute(
                    "SELECT payload FROM portfolio_versions WHERE portfolio_id = %s",
                    (portfolio_id,),
                ).fetchone()
                holdings = cursor.execute(
                    "SELECT payload FROM portfolio_holdings WHERE portfolio_id = %s ORDER BY ticker",
                    (portfolio_id,),
                ).fetchall()
                decisions = cursor.execute(
                    """
                    SELECT decision_id, record_hash FROM investment_decisions
                    WHERE portfolio_id = %s ORDER BY sequence_number
                    """,
                    (portfolio_id,),
                ).fetchall()
            return {
                "portfolio": portfolio[0] if portfolio else None,
                "holdings": [row[0] for row in holdings],
                "decision_ids": [row[0] for row in decisions],
                "record_hashes": [row[1] for row in decisions],
            }
        finally:
            connection.close()

    @staticmethod
    def _existing_result(
        *,
        cursor: Any,
        portfolio: dict[str, Any],
        holdings: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
        event: dict[str, Any],
        expected_decision_ids: tuple[str, ...],
    ) -> PersistedPortfolioChange:
        portfolio_id = str(portfolio["portfolio_id"])
        stored_portfolio = cursor.execute(
            "SELECT payload FROM portfolio_versions WHERE portfolio_id = %s",
            (portfolio_id,),
        ).fetchone()
        stored_holdings = [
            row[0]
            for row in cursor.execute(
                "SELECT payload FROM portfolio_holdings WHERE portfolio_id = %s ORDER BY ticker",
                (portfolio_id,),
            ).fetchall()
        ]
        stored_decisions = cursor.execute(
                """
                SELECT decision_id, supersedes_decision_id, previous_hash, record_hash
                FROM investment_decisions
                WHERE portfolio_id = %s ORDER BY sequence_number
                """,
                (portfolio_id,),
            ).fetchall()
        stored_ids = tuple(row[0] for row in stored_decisions)
        stored_event = cursor.execute(
            """
            SELECT event_id, aggregate_type, aggregate_id, event_type, payload
            FROM outbox_events WHERE event_id = %s
            """,
            (str(event["event_id"]),),
        ).fetchone()
        expected_holdings = sorted(holdings, key=lambda item: str(item["ticker"]).upper())
        expected_event = (
            str(event["event_id"]),
            str(event.get("aggregate_type") or "PORTFOLIO"),
            str(event.get("aggregate_id") or portfolio_id),
            str(event["event_type"]),
            event.get("payload") or {},
        )
        hashes_match = len(stored_decisions) == len(decisions)
        previous_hash = stored_decisions[0][2] if stored_decisions else GENESIS_HASH
        for decision, stored in zip(decisions, stored_decisions):
            material, expected_hash = PostgresPortfolioRepository._decision_material(
                portfolio_id=portfolio_id,
                git_revision=str(portfolio["git_revision"]),
                portfolio_decided_at=canonical_timestamp(portfolio["decided_at"]),
                portfolio_data_as_of=canonical_timestamp(portfolio["data_as_of"]),
                default_execution_mode=str(
                    portfolio.get("execution_mode") or "RECORD_ONLY"
                ),
                previous_hash=previous_hash,
                decision=decision,
            )
            hashes_match = hashes_match and stored[1:] == (
                material["supersedes_decision_id"],
                material["previous_hash"],
                expected_hash,
            )
            previous_hash = expected_hash
        if (
            stored_portfolio != (portfolio,)
            or stored_holdings != expected_holdings
            or stored_ids != expected_decision_ids
            or stored_event != expected_event
            or not hashes_match
        ):
            raise ValueError(
                f"Portfolio {portfolio_id} already exists with different audit records"
            )
        return PersistedPortfolioChange(portfolio_id, stored_ids, str(event["event_id"]))

    @staticmethod
    def _insert_holdings(cursor: Any, portfolio_id: str, holdings: list[dict[str, Any]]) -> None:
        for holding in holdings:
            cursor.execute(
                """
                INSERT INTO portfolio_holdings (portfolio_id, ticker, weight, payload)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    portfolio_id,
                    str(_required(holding, "ticker")).strip().upper(),
                    _required(holding, "weight"),
                    Jsonb(holding),
                ),
            )

    @staticmethod
    def _insert_decision(
        *,
        cursor: Any,
        portfolio_id: str,
        git_revision: str,
        portfolio_decided_at: Any,
        portfolio_data_as_of: Any,
        default_execution_mode: str,
        previous_hash: str,
        decision: dict[str, Any],
    ) -> str:
        material, record_hash = PostgresPortfolioRepository._decision_material(
            portfolio_id=portfolio_id,
            git_revision=git_revision,
            portfolio_decided_at=portfolio_decided_at,
            portfolio_data_as_of=portfolio_data_as_of,
            default_execution_mode=default_execution_mode,
            previous_hash=previous_hash,
            decision=decision,
        )
        decision_id = material["decision_id"]
        ticker = material["ticker"]
        action = material["decision"]
        data_as_of = material["data_as_of"]
        decided_at = material["decided_at"]
        execution_mode = material["execution_mode"]
        payload = material["decision_payload"]
        versions = material["model_versions"]
        cursor.execute(
            """
            INSERT INTO investment_decisions (
                decision_id, portfolio_id, supersedes_decision_id, ticker,
                decision, execution_mode, data_as_of, decided_at, payload,
                previous_hash, record_hash
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                decision_id,
                portfolio_id,
                decision.get("supersedes_decision_id"),
                ticker,
                action,
                execution_mode,
                data_as_of,
                decided_at,
                Jsonb(payload),
                previous_hash,
                record_hash,
            ),
        )
        for version in versions:
            component = str(_required(version, "component"))
            version_name = str(_required(version, "version"))
            metadata = {
                "provider": version.get("provider"),
                "model": version.get("model"),
                "prompt_version": version.get("prompt_version"),
                "parameters": version.get("parameters") or {},
            }
            cursor.execute(
                """
                INSERT INTO decision_model_versions (
                    decision_id, component, version, parameters
                ) VALUES (%s, %s, %s, %s)
                """,
                (decision_id, component, version_name, Jsonb(metadata)),
            )
        return record_hash

    @staticmethod
    def _decision_material(
        *,
        portfolio_id: str,
        git_revision: str,
        portfolio_decided_at: Any,
        portfolio_data_as_of: Any,
        default_execution_mode: str,
        previous_hash: str,
        decision: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        execution_mode = str(
            decision.get("execution_mode") or default_execution_mode
        ).upper()
        if execution_mode not in ALLOWED_EXECUTION_MODES:
            raise ValueError(f"Unsupported execution mode: {execution_mode}")
        raw_versions = _as_mapping_list(decision.get("model_versions"), "model_versions")
        material = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "decision_id": str(_required(decision, "decision_id")),
            "supersedes_decision_id": decision.get("supersedes_decision_id"),
            "decided_at": canonical_timestamp(
                decision.get("decided_at") or portfolio_decided_at
            ),
            "data_as_of": canonical_timestamp(
                decision.get("data_as_of") or portfolio_data_as_of
            ),
            "ticker": str(_required(decision, "ticker")).strip().upper(),
            "decision": str(_required(decision, "decision")).strip().upper(),
            "portfolio_version": portfolio_id,
            "git_revision": git_revision,
            "model_versions": [normalize_model_version(item) for item in raw_versions],
            "decision_payload": dict(
                decision.get("decision_payload") or decision.get("payload") or {}
            ),
            "execution_mode": execution_mode,
            "previous_hash": previous_hash,
        }
        return material, _record_hash(material)
