"""Strict structured output for capture classification."""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CaptureType(StrEnum):
    TASK = "Task"
    IDEA = "Idea"
    REFERENCE = "Reference"
    THOUGHT = "Thought"


class Domain(StrEnum):
    PERSONAL = "Personal"
    PORTFOLIO = "Portfolio"
    TECH = "Tech"
    SHOPPING = "Shopping"
    PLACES = "Places"
    DATING = "Dating"
    TRAVEL = "Travel"
    CAREER = "Career"
    RESERVIST = "Reservist"


class SurfaceContext(StrEnum):
    MORNING = "Morning"
    AFTER_WORK = "AfterWork"
    EVENING = "Evening"
    WEEKEND = "Weekend"
    ON_DEMAND = "OnDemand"
    ANYTIME = "Anytime"


class ShoppingKind(StrEnum):
    ROUTINE = "Routine"
    PLANNED = "Planned"
    NONE = "None"


class Confidence(StrEnum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class CaptureClassification(BaseModel):
    """Validated interpretation of one capture, suitable for persistence."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    title: str = Field(min_length=1, max_length=200)
    type: CaptureType
    domain: Domain
    location: str | None = Field(max_length=200)
    due: date | None
    surface_context: SurfaceContext
    shopping_kind: ShoppingKind
    confidence: Confidence

    @model_validator(mode="after")
    def validate_shopping_consistency(self) -> "CaptureClassification":
        if self.location == "":
            raise ValueError("location must be null or non-blank")
        if self.domain is Domain.SHOPPING:
            if self.shopping_kind is ShoppingKind.NONE:
                raise ValueError("shopping captures require Routine or Planned")
        elif self.shopping_kind is not ShoppingKind.NONE:
            raise ValueError("non-shopping captures require ShoppingKind None")
        if (
            self.shopping_kind is ShoppingKind.ROUTINE
            and self.surface_context is not SurfaceContext.AFTER_WORK
        ):
            raise ValueError("routine shopping requires AfterWork")
        if (
            self.shopping_kind is ShoppingKind.PLANNED
            and self.surface_context is SurfaceContext.AFTER_WORK
        ):
            raise ValueError("planned shopping cannot use AfterWork")
        return self
