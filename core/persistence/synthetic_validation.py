from __future__ import annotations

"""Synthetic portfolio changes used only by the manual validation harness."""

from core.persistence.portfolio_repository import PortfolioChange


def synthetic_changes() -> tuple[PortfolioChange, PortfolioChange]:
    construction_id = "PORT-SYNTHETIC-CONSTRUCTION-001"
    common_versions = [
        {
            "component": "synthetic_validation",
            "version": "1.0",
            "provider": "rules-based",
            "model": None,
            "prompt_version": None,
            "parameters": {},
        }
    ]
    construction = PortfolioChange(
        change_type="CONSTRUCTION",
        portfolio={
            "portfolio_id": construction_id,
            "previous_portfolio_id": None,
            "execution_mode": "RECORD_ONLY",
            "git_revision": "SYNTHETIC",
            "model_versions": common_versions,
            "data_as_of": "2026-01-01T12:00:00+00:00",
            "decided_at": "2026-01-01T12:01:00+00:00",
            "holdings": [
                {"ticker": "ALPHA", "weight": 0.6, "synthetic": True},
                {"ticker": "BETA", "weight": 0.4, "synthetic": True},
            ],
            "synthetic": True,
        },
        decisions=[
            {
                "decision_id": f"{construction_id}-ALPHA",
                "ticker": "ALPHA",
                "decision": "BUY",
                "decision_payload": {"reason": "synthetic construction"},
                "model_versions": common_versions,
            },
            {
                "decision_id": f"{construction_id}-BETA",
                "ticker": "BETA",
                "decision": "BUY",
                "decision_payload": {"reason": "synthetic construction"},
                "model_versions": common_versions,
            },
        ],
        outbox_event={
            "event_id": f"EVENT-{construction_id}",
            "event_type": "PORTFOLIO_CONSTRUCTED",
            "payload": {"synthetic": True},
        },
    )
    reallocation_id = "PORT-SYNTHETIC-REALLOCATION-001"
    reallocation = PortfolioChange(
        change_type="REALLOCATION",
        portfolio={
            "portfolio_id": reallocation_id,
            "previous_portfolio_id": construction_id,
            "execution_mode": "RECORD_ONLY",
            "git_revision": "SYNTHETIC",
            "model_versions": common_versions,
            "data_as_of": "2026-01-02T12:00:00+00:00",
            "decided_at": "2026-01-02T12:01:00+00:00",
            "holdings": [
                {"ticker": "ALPHA", "weight": 0.5, "synthetic": True},
                {"ticker": "BETA", "weight": 0.5, "synthetic": True},
            ],
            "synthetic": True,
        },
        decisions=[
            {
                "decision_id": f"{reallocation_id}-ALPHA",
                "ticker": "ALPHA",
                "decision": "REALLOCATE",
                "decision_payload": {"before_weight": 0.6, "after_weight": 0.5},
                "model_versions": common_versions,
            },
            {
                "decision_id": f"{reallocation_id}-BETA",
                "ticker": "BETA",
                "decision": "REALLOCATE",
                "decision_payload": {"before_weight": 0.4, "after_weight": 0.5},
                "model_versions": common_versions,
            },
        ],
        outbox_event={
            "event_id": f"EVENT-{reallocation_id}",
            "event_type": "PORTFOLIO_REALLOCATED",
            "payload": {"synthetic": True},
        },
    )
    return construction, reallocation


def validate_synthetic_database_name(name: str) -> str:
    prefix = "synthetic_validation_"
    if not name.startswith(prefix):
        raise ValueError("Synthetic database must use the synthetic_validation_ prefix")
    compact = name.removeprefix(prefix).replace("_", "")
    if not compact or not compact.isascii() or not compact.isalnum():
        raise ValueError("Synthetic database name contains unsupported characters")
    return name
