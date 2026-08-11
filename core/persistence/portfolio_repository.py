from __future__ import annotations

"""Shared atomic boundary for construction and reallocation persistence."""

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol, Sequence


ChangeType = Literal["CONSTRUCTION", "REALLOCATION"]


@dataclass(frozen=True)
class PortfolioChange:
    portfolio: Mapping[str, Any]
    decisions: Sequence[Mapping[str, Any]]
    outbox_event: Mapping[str, Any]
    change_type: ChangeType

    def __post_init__(self) -> None:
        if self.change_type not in {"CONSTRUCTION", "REALLOCATION"}:
            raise ValueError(f"Unsupported portfolio change type: {self.change_type}")
        if not self.decisions:
            raise ValueError("Every portfolio change must include decision records.")


@dataclass(frozen=True)
class PersistedPortfolioChange:
    portfolio_id: str
    decision_ids: tuple[str, ...]
    outbox_event_id: str


class PortfolioRepository(Protocol):
    """Persist one portfolio mutation and all audit evidence atomically."""

    def persist_change(self, change: PortfolioChange) -> PersistedPortfolioChange:
        ...
