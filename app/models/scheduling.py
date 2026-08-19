"""Typed boundaries for authenticated scheduled review execution."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.reviews import ReviewWindow


class SchedulerSlot(StrEnum):
    MORNING = "morning"
    AFTER_WORK = "afterwork"
    EVENING = "evening"


class SchedulerRunStatus(StrEnum):
    DELIVERED = "delivered"
    EMPTY = "empty"
    DUPLICATE = "duplicate"


class ScheduledReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: SchedulerSlot


class SchedulerExecutionIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_name: str = Field(min_length=1, max_length=500)
    schedule_time: datetime

    @field_validator("schedule_time")
    @classmethod
    def require_aware_schedule_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("schedule_time must be timezone-aware")
        return value

    @property
    def run_key(self) -> str:
        return f"{self.job_name}\n{self.schedule_time.isoformat()}"


class ReviewDeliveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    window: ReviewWindow
    item_count: int = Field(ge=0)
    last_surfaced_recorded: bool

    @property
    def status(self) -> SchedulerRunStatus:
        return (
            SchedulerRunStatus.EMPTY
            if self.item_count == 0
            else SchedulerRunStatus.DELIVERED
        )


class SchedulerRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SchedulerRunStatus
    window: ReviewWindow
    item_count: int = Field(ge=0)
    last_surfaced_recorded: bool
