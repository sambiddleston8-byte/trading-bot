"""Persistence contracts for local and future PostgreSQL adapters."""

from core.persistence.portfolio_repository import (
    PersistedPortfolioChange,
    PortfolioChange,
    PortfolioRepository,
)
from core.persistence.postgres_portfolio_repository import PostgresPortfolioRepository
from core.persistence.portfolio_change_builder import PortfolioChangeBuilder
from core.persistence.persistence_comparison import PersistenceComparison
from core.persistence.postgres_cutover_readiness import assess_postgres_cutover_readiness

__all__ = [
    "PersistedPortfolioChange",
    "PortfolioChange",
    "PortfolioRepository",
    "PostgresPortfolioRepository",
    "PortfolioChangeBuilder",
    "PersistenceComparison",
    "assess_postgres_cutover_readiness",
]
