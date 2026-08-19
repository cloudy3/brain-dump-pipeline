"""Application-facing manual query persistence contract."""

from typing import Protocol

from app.models.queries import QueryCriteria, QueryItem


class QueryPersistenceError(RuntimeError):
    """Raised when saved items cannot be queried safely."""


class QueryRepository(Protocol):
    async def search(self, *, criteria: QueryCriteria) -> list[QueryItem]:
        """Return active Brain Dump v2 candidates matching structured filters."""
