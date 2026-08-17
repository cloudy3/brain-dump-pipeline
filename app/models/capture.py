"""Structured boundaries for storing Telegram captures."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.models.actions import BrainDumpItem
from app.models.classification import CaptureClassification


class CaptureInput(BaseModel):
    """A classified Telegram text message ready for persistence."""

    model_config = ConfigDict(frozen=True)

    original_input: str
    telegram_update_id: int
    telegram_message_id: int
    classification: CaptureClassification


CaptureSummary = BrainDumpItem


class CaptureSaveStatus(StrEnum):
    CREATED = "created"
    EXISTING = "existing"


class CaptureSaveResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CaptureSaveStatus
    summary: CaptureSummary
