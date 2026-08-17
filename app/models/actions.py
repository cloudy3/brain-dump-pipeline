"""Structured models for Telegram item actions and stored Brain Dump items."""

import re
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.classification import (
    CaptureType,
    Confidence,
    Domain,
    ShoppingKind,
)

CALLBACK_PREFIX = "bd1"
TELEGRAM_CALLBACK_DATA_LIMIT = 64
_PAGE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class CallbackAction(StrEnum):
    DONE = "d"
    BOUGHT = "b"
    DELETE = "x"
    KEEP = "k"
    SNOOZE_MENU = "s"
    SNOOZE_TOMORROW = "st"
    SNOOZE_NEXT_WEEK = "sw"
    SNOOZE_TWO_WEEKS = "s2"
    SNOOZE_ONE_MONTH = "sm"
    FOCUS = "f"
    BACK = "r"


class ActionPolicy(BaseModel):
    """Runtime-tunable deterministic item-action durations."""

    model_config = ConfigDict(frozen=True)

    keep_task_days: int = Field(default=7, gt=0)
    keep_idea_days: int = Field(default=14, gt=0)
    keep_thought_days: int = Field(default=30, gt=0)
    keep_reference_days: int = Field(default=30, gt=0)
    keep_planned_purchase_days: int = Field(default=30, gt=0)
    planned_purchase_post_bought_cooldown_days: int = Field(default=30, gt=0)


class ActionCallback(BaseModel):
    """Compact, versioned callback payload containing no personal content."""

    model_config = ConfigDict(frozen=True)

    action: CallbackAction
    page_id: str = Field(pattern=r"^[0-9a-f]{32}$")

    def encode(self) -> str:
        payload = f"{CALLBACK_PREFIX}:{self.action.value}:{self.page_id}"
        if len(payload.encode("utf-8")) > TELEGRAM_CALLBACK_DATA_LIMIT:
            raise ValueError("callback payload exceeds Telegram's size limit")
        return payload

    @classmethod
    def decode(cls, payload: str) -> "ActionCallback":
        parts = payload.split(":")
        if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
            raise ValueError("invalid callback payload")
        page_id = normalize_page_id(parts[2])
        try:
            action = CallbackAction(parts[1])
        except ValueError as error:
            raise ValueError("unknown callback action") from error
        return cls(action=action, page_id=page_id)


class BrainDumpItem(BaseModel):
    """Notion-independent projection required by Telegram actions."""

    model_config = ConfigDict(frozen=True)

    page_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    page_url: str = Field(pattern=r"^https://")
    title: str = Field(min_length=1)
    type: CaptureType
    domain: Domain
    shopping_kind: ShoppingKind
    purchase_focus: bool
    due: date | None
    snoozed_until: date | None
    confidence: Confidence

    @property
    def is_planned_purchase(self) -> bool:
        return self.domain is Domain.SHOPPING and self.shopping_kind is ShoppingKind.PLANNED

    @property
    def is_routine_purchase(self) -> bool:
        return self.domain is Domain.SHOPPING and self.shopping_kind is ShoppingKind.ROUTINE


def normalize_page_id(value: str) -> str:
    normalized = value.replace("-", "").lower()
    if _PAGE_ID_PATTERN.fullmatch(normalized) is None:
        raise ValueError("invalid Notion page ID")
    return normalized
