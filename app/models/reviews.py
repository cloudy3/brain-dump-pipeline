"""Strict models for deterministic proactive review generation."""

from datetime import date, datetime
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.models.actions import BrainDumpItem
from app.models.classification import CaptureType, SurfaceContext


class ReviewWindow(StrEnum):
    MORNING = "Morning"
    AFTER_WORK = "AfterWork"
    EVENING = "Evening"
    WEEKEND = "Weekend"


class DeadlineUrgency(StrEnum):
    NONE = "None"
    LATER = "Later"
    MODERATE = "Moderate"
    STRONG = "Strong"
    TODAY = "Today"
    OVERDUE = "Overdue"


class ReviewPolicy(BaseModel):
    """Central, injectable limits, intervals, and readable score weights."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    morning_limit: int = Field(default=3, ge=1, le=8)
    after_work_task_limit: int = Field(default=3, ge=1, le=8)
    evening_limit: int = Field(default=3, ge=1, le=8)
    weekend_limit: int = Field(default=8, ge=1, le=8)
    routine_shopping_limit: int = Field(default=10, ge=1, le=50)

    task_spacing_days: int = Field(default=2, ge=1)
    routine_shopping_spacing_days: int = Field(default=1, ge=1)
    idea_spacing_days: int = Field(default=7, ge=1)
    thought_spacing_days: int = Field(default=30, ge=1)
    planned_purchase_spacing_days: int = Field(default=14, ge=1)
    reference_spacing_days: int = Field(default=30, ge=1)

    idea_minimum_age_days: int = Field(default=3, ge=0)
    thought_minimum_age_days: int = Field(default=30, ge=0)
    planned_purchase_minimum_age_days: int = Field(default=7, ge=0)
    minimum_score: int = 35

    exact_context_score: int = 20
    anytime_context_score: int = 10
    task_score: int = 30
    idea_score: int = 18
    planned_purchase_score: int = 8
    thought_score: int = -5
    reference_score: int = -10
    never_surfaced_score: int = 12
    purchase_focus_score: int = 25
    urgent_cooldown_override_penalty: int = -25
    age_bucket_days: int = Field(default=7, ge=1)
    age_bucket_score: int = 3
    maximum_age_buckets: int = Field(default=6, ge=0)

    moderate_deadline_score: int = 15
    strong_deadline_score: int = 35
    today_deadline_score: int = 60
    overdue_deadline_score: int = 70

    evening_idea_affinity_score: int = 4
    weekend_personal_score: int = 8
    weekend_activity_score: int = 6
    weekend_portfolio_score: int = 4
    evening_domain_soft_cap: int = Field(default=2, ge=1)
    weekend_domain_soft_cap: int = Field(default=3, ge=1)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    window: ReviewWindow
    reference_time: datetime
    limit_override: int | None = Field(default=None, ge=1, le=8)

    @field_validator("reference_time")
    @classmethod
    def require_aware_reference_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reference_time must be timezone-aware")
        return value


class ReviewCandidateCriteria(BaseModel):
    """Notion-independent coarse criteria for proactive candidates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    window: ReviewWindow
    reference_date: date


class ReviewItem(BrainDumpItem):
    """Stable item projection needed for selection and later Phase 7 rendering."""

    surface_context: SurfaceContext
    created_at: datetime
    last_surfaced: date | None = None
    location: str | None = Field(default=None, max_length=200)

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class ReviewEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item: ReviewItem
    score: int
    urgency: DeadlineUrgency


class RoutineShoppingGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ReviewItem, ...]
    total_eligible_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_group(self) -> "RoutineShoppingGroup":
        if not self.items:
            raise ValueError("routine shopping group must contain items")
        if self.total_eligible_count < len(self.items):
            raise ValueError("total_eligible_count cannot be smaller than items")
        if any(not item.is_routine_purchase for item in self.items):
            raise ValueError("routine shopping group can contain only Routine purchases")
        return self

    @computed_field
    @property
    def additional_count(self) -> int:
        return self.total_eligible_count - len(self.items)


class ReviewPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    window: ReviewWindow
    generated_at: datetime
    entries: tuple[ReviewEntry, ...] = ()
    routine_shopping: RoutineShoppingGroup | None = None

    @field_validator("generated_at")
    @classmethod
    def require_aware_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_contents(self) -> "ReviewPlan":
        if self.routine_shopping is not None and self.window is not ReviewWindow.AFTER_WORK:
            raise ValueError("routine shopping is allowed only in AfterWork reviews")
        if any(entry.item.is_routine_purchase for entry in self.entries):
            raise ValueError("Routine purchases must use the grouped shopping section")
        if sum(entry.item.is_planned_purchase for entry in self.entries) > 1:
            raise ValueError("a review can contain at most one Planned purchase")
        if sum(entry.item.type is CaptureType.THOUGHT for entry in self.entries) > 1:
            raise ValueError("a review can contain at most one Thought")
        return self

    @computed_field
    @property
    def is_empty(self) -> bool:
        return not self.entries and self.routine_shopping is None
