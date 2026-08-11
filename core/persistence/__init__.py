"""Persistence contracts for local and future PostgreSQL adapters."""

from core.persistence.portfolio_repository import (
    PersistedPortfolioChange,
    PortfolioChange,
    PortfolioRepository,
)
from core.persistence.postgres_portfolio_repository import PostgresPortfolioRepository
from core.persistence.portfolio_change_builder import PortfolioChangeBuilder
from core.persistence.persistence_comparison import PersistenceComparison

__all__ = [
    "PersistedPortfolioChange",
    "PortfolioChange",
    "PortfolioRepository",
    "PostgresPortfolioRepository",
    "PortfolioChangeBuilder",
    "PersistenceComparison",
]
