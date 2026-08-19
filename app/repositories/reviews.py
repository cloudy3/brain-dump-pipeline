"""Application-facing proactive review persistence contract."""

from typing import Protocol

from app.models.reviews import ReviewCandidateCriteria, ReviewItem


class ReviewPersistenceError(RuntimeError):
    """Raised when proactive candidates cannot be queried safely."""


class ReviewRepository(Protocol):
    async def list_candidates(
        self,
        *,
        criteria: ReviewCandidateCriteria,
    ) -> list[ReviewItem]:
        """Return active Brain Dump v2 candidates for one review window."""
