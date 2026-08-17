"""Structured boundaries for storing Telegram captures."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.classification import CaptureClassification, CaptureType, Confidence, Domain


class CaptureInput(BaseModel):
    """A classified Telegram text message ready for persistence."""

    model_config = ConfigDict(frozen=True)

    original_input: str
    telegram_update_id: int
    telegram_message_id: int
    classification: CaptureClassification


class CaptureSummary(BaseModel):
    """Small stored projection used for duplicate acknowledgements."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1)
    type: CaptureType
    domain: Domain
    confidence: Confidence


class CaptureSaveStatus(StrEnum):
    CREATED = "created"
    EXISTING = "existing"


class CaptureSaveResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CaptureSaveStatus
    summary: CaptureSummary
