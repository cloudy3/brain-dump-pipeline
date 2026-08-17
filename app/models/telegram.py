"""Validated Telegram update and inline-keyboard boundaries."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TelegramUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int


class TelegramChat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int


class TelegramMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    message_id: int
    from_: TelegramUser | None = Field(default=None, alias="from")
    chat: TelegramChat
    text: str | None = None


class TelegramCallbackQuery(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(min_length=1)
    from_: TelegramUser | None = Field(default=None, alias="from")
    message: TelegramMessage | None = None
    data: str | None = None


class TelegramUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    update_id: int
    message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None


class InlineKeyboardButton(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    callback_data: str | None = Field(default=None, min_length=1, max_length=64)
    url: str | None = Field(default=None, pattern=r"^https://")

    @model_validator(mode="after")
    def require_one_action(self) -> "InlineKeyboardButton":
        if (self.callback_data is None) == (self.url is None):
            raise ValueError("button requires exactly one of callback_data or url")
        return self


class InlineKeyboardMarkup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    inline_keyboard: list[list[InlineKeyboardButton]]


class WebhookResponse(BaseModel):
    status: Literal["acknowledged", "ignored"]
