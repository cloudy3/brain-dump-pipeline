"""Structured boundaries for storing Telegram captures."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class CaptureInput(BaseModel):
    """The lossless input needed to persist one Telegram text message."""

    model_config = ConfigDict(frozen=True)

    original_input: str
    telegram_update_id: int
    telegram_message_id: int


class CaptureSaveStatus(StrEnum):
    CREATED = "created"
    EXISTING = "existing"
