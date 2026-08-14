"""Application-facing capture persistence contract."""

from typing import Protocol

from app.models.capture import CaptureInput, CaptureSaveStatus


class CapturePersistenceError(RuntimeError):
    """Raised when a capture cannot be safely persisted or verified."""


class CaptureRepository(Protocol):
    async def save_if_new(self, capture: CaptureInput) -> CaptureSaveStatus:
        """Persist a capture once, returning whether it was newly created."""

    async def validate(self) -> None:
        """Validate the configured persistence target without writing to it."""
