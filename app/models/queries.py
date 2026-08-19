"""Strict models for manual Brain Dump retrieval."""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.actions import BrainDumpItem
from app.models.classification import CaptureType, Confidence, Domain, ShoppingKind


class DueFilter(StrEnum):
    ANY = "Any"
    TODAY = "Today"
    OVERDUE = "Overdue"
    THIS_WEEK = "ThisWeek"
    UPCOMING = "Upcoming"
    NO_DUE_DATE = "NoDueDate"


class QuerySort(StrEnum):
    RELEVANCE = "Relevance"
    NEWEST = "Newest"
    OLDEST = "Oldest"
    DUE_SOON = "DueSoon"


class QueryPlan(BaseModel):
    """Validated interpretation of one intentional retrieval request."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    types: list[CaptureType] = Field(default_factory=list, max_length=4)
    domains: list[Domain] = Field(default_factory=list, max_length=9)
    location: str | None = Field(default=None, min_length=1, max_length=200)
    keywords: list[str] = Field(default_factory=list, max_length=8)
    shopping_kind: Literal[ShoppingKind.ROUTINE, ShoppingKind.PLANNED] | None = None
    due_filter: DueFilter = DueFilter.ANY
    sort: QuerySort = QuerySort.RELEVANCE
    limit: int = Field(default=10, ge=1, le=20)
    confidence: Confidence

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            compact = " ".join(value.split())
            if not compact or len(compact) > 80:
                raise ValueError("keywords must be non-blank and at most 80 characters")
            key = compact.casefold()
            if key not in seen:
                normalized.append(compact)
                seen.add(key)
        return normalized

    @model_validator(mode="after")
    def deduplicate_enum_filters(self) -> "QueryPlan":
        if len(set(self.types)) != len(self.types):
            raise ValueError("types must not contain duplicates")
        if len(set(self.domains)) != len(self.domains):
            raise ValueError("domains must not contain duplicates")
        return self


class QueryCriteria(BaseModel):
    """Notion-independent structured filters used by a query repository."""

    model_config = ConfigDict(frozen=True)

    types: tuple[CaptureType, ...] = ()
    domains: tuple[Domain, ...] = ()
    location: str | None = None
    shopping_kind: Literal[ShoppingKind.ROUTINE, ShoppingKind.PLANNED] | None = None
    due_filter: DueFilter = DueFilter.ANY
    reference_date: date


class QueryItem(BrainDumpItem):
    """Stored item projection needed for matching, ranking, and display."""

    location: str | None = None
    original_input: str
    created_at: datetime
