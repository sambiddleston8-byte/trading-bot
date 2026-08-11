"""Persistence contracts for local and future PostgreSQL adapters."""

from core.persistence.portfolio_repository import (
    PersistedPortfolioChange,
    PortfolioChange,
    PortfolioRepository,
)

__all__ = ["PersistedPortfolioChange", "PortfolioChange", "PortfolioRepository"]
