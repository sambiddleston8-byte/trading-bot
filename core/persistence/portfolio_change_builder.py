from __future__ import annotations

"""Build one audited persistence shape for construction and reallocation."""

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping, Sequence

from core.decision_ledger import ModelVersion, canonical_timestamp
from core.persistence.portfolio_repository import PortfolioChange


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _required(value: Any, name: str) -> Any:
    if value is None or value == "":
        raise ValueError(f"Missing required portfolio-change field: {name}")
    return value


class PortfolioChangeBuilder:
    """Normalize all portfolio mutations before either backend sees them."""

    @staticmethod
    def construction(
        portfolio: Mapping[str, Any],
        ledger_entries: Sequence[Mapping[str, Any]],
    ) -> PortfolioChange:
        decisions = [deepcopy(dict(item)) for item in ledger_entries]
        if not decisions:
            raise ValueError("Construction requires decision records")
        decided_at = canonical_timestamp(_required(decisions[0].get("decided_at"), "decided_at"))
        if any(canonical_timestamp(item["decided_at"]) != decided_at for item in decisions):
            raise ValueError("Construction decisions must share one decided_at timestamp")
        data_cutoffs = [canonical_timestamp(_required(item.get("data_as_of"), "data_as_of")) for item in decisions]
        git_revision = str(_required(decisions[0].get("git_revision"), "git_revision"))
        normalized = deepcopy(dict(portfolio))
        portfolio_id = str(_required(normalized.get("portfolio_id"), "portfolio_id"))
        portfolio_versions: list[dict[str, Any]] = []
        seen_versions: set[str] = set()
        for decision in decisions:
            for version in decision.get("model_versions") or []:
                value = deepcopy(dict(version))
                key = _canonical_json(value)
                if key not in seen_versions:
                    seen_versions.add(key)
                    portfolio_versions.append(value)
        normalized.update(
            {
                "execution_mode": "RECORD_ONLY",
                "git_revision": git_revision,
                "model_versions": portfolio_versions,
                "data_as_of": max(data_cutoffs),
                "decided_at": decided_at,
            }
        )
        return PortfolioChange(
            portfolio=normalized,
            decisions=decisions,
            change_type="CONSTRUCTION",
            outbox_event={
                "event_id": f"EVENT-{portfolio_id}",
                "event_type": "PORTFOLIO_CONSTRUCTED",
                "payload": {"portfolio_id": portfolio_id},
            },
        )

    @staticmethod
    def reallocation(
        *,
        previous_portfolio: Mapping[str, Any],
        updated_portfolio: Mapping[str, Any],
        changes: Sequence[Mapping[str, Any]],
        checked_at: str,
        applied_at: str,
        git_revision: str,
        monitor_version: str,
        portfolio_version: str,
    ) -> PortfolioChange:
        if not changes:
            raise ValueError("Reallocation requires at least one allocation change")
        previous_id = str(_required(previous_portfolio.get("portfolio_id"), "previous_portfolio_id"))
        decided_at = canonical_timestamp(applied_at)
        data_as_of = canonical_timestamp(checked_at)
        identity_material = {
            "previous_portfolio_id": previous_id,
            "decided_at": decided_at,
            "changes": list(changes),
        }
        suffix = hashlib.sha256(_canonical_json(identity_material).encode("utf-8")).hexdigest()[:16].upper()
        portfolio_id = f"PORT-REALLOCATION-{suffix}"
        versions = [
            ModelVersion(component="portfolio_monitor", version=monitor_version).as_dict(),
            ModelVersion(component="portfolio", version=portfolio_version).as_dict(),
        ]
        normalized = deepcopy(dict(updated_portfolio))
        normalized.update(
            {
                "portfolio_id": portfolio_id,
                "previous_portfolio_id": previous_id,
                "execution_mode": "RECORD_ONLY",
                "git_revision": git_revision,
                "model_versions": versions,
                "data_as_of": data_as_of,
                "decided_at": decided_at,
            }
        )
        decisions = []
        for item in changes:
            change = deepcopy(dict(item))
            ticker = str(_required(change.get("ticker"), "ticker")).strip().upper()
            decisions.append(
                {
                    "decision_id": f"{portfolio_id}-{ticker}",
                    "ticker": ticker,
                    "decision": "REALLOCATE",
                    "decision_payload": change,
                    "model_versions": versions,
                    "data_as_of": data_as_of,
                    "decided_at": decided_at,
                    "portfolio_version": portfolio_id,
                    "git_revision": git_revision,
                    "execution_mode": "RECORD_ONLY",
                }
            )
        return PortfolioChange(
            portfolio=normalized,
            decisions=decisions,
            change_type="REALLOCATION",
            outbox_event={
                "event_id": f"EVENT-{portfolio_id}",
                "event_type": "PORTFOLIO_REALLOCATED",
                "payload": {
                    "portfolio_id": portfolio_id,
                    "previous_portfolio_id": previous_id,
                    "decision_count": len(decisions),
                },
            },
        )
