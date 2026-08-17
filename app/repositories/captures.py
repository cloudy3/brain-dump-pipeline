"""Application-facing capture persistence contract."""

from typing import Protocol

from app.models.capture import CaptureInput, CaptureSaveResult, CaptureSummary


class CapturePersistenceError(RuntimeError):
    """Raised when a capture cannot be safely persisted or verified."""


class CaptureRepository(Protocol):
    async def find_by_telegram_identity(
        self,
        *,
        telegram_update_id: int,
        telegram_message_id: int,
    ) -> CaptureSummary | None:
        """Return stored confirmation data for an already processed message."""

    async def save_if_new(self, capture: CaptureInput) -> CaptureSaveResult:
        """Persist a capture once and return stored confirmation data."""

    async def validate(self) -> None:
        """Validate the configured persistence target without writing to it."""
