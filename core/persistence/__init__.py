"""Persistence contracts for local and future PostgreSQL adapters."""

from core.persistence.portfolio_repository import (
    PersistedPortfolioChange,
    PortfolioChange,
    PortfolioRepository,
)
from core.persistence.postgres_portfolio_repository import PostgresPortfolioRepository

__all__ = [
    "PersistedPortfolioChange",
    "PortfolioChange",
    "PortfolioRepository",
    "PostgresPortfolioRepository",
]
