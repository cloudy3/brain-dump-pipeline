"""Environment-backed application configuration."""

from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.actions import ActionPolicy
from app.models.reviews import ReviewPolicy

ENV_SETTINGS_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
    frozen=True,
)


class GeminiSettings(BaseSettings):
    """Gemini settings shared by the application and live evaluation tool."""

    model_config = ENV_SETTINGS_CONFIG

    gemini_api_key: SecretStr = Field(min_length=1)
    gemini_model: str = Field(default="gemini-3.5-flash-lite", min_length=1)
    gemini_request_timeout_seconds: float = Field(default=10.0, gt=0)


class Settings(GeminiSettings):
    """Environment-backed application configuration."""

    model_config = ENV_SETTINGS_CONFIG

    telegram_bot_token: SecretStr = Field(min_length=1)
    telegram_webhook_secret: SecretStr = Field(min_length=1)
    telegram_allowed_user_id: int = Field(gt=0)
    telegram_allowed_chat_id: int
    telegram_api_base_url: AnyHttpUrl = AnyHttpUrl("https://api.telegram.org")
    telegram_request_timeout_seconds: float = Field(default=10.0, gt=0)
    notion_api_token: SecretStr = Field(min_length=1)
    notion_brain_dump_database_id: str = Field(min_length=1)
    notion_brain_dump_data_source_id: str = Field(min_length=1)
    notion_api_version: str = Field(default="2026-03-11", min_length=1)
    notion_request_timeout_seconds: float = Field(default=10.0, gt=0)
    keep_task_days: int = Field(default=7, gt=0)
    keep_idea_days: int = Field(default=14, gt=0)
    keep_thought_days: int = Field(default=30, gt=0)
    keep_reference_days: int = Field(default=30, gt=0)
    keep_planned_purchase_days: int = Field(default=30, gt=0)
    planned_purchase_post_bought_cooldown_days: int = Field(default=30, gt=0)
    telegram_query_result_limit: int = Field(default=5, ge=1, le=20)
    review_morning_limit: int = Field(default=3, ge=1, le=8)
    review_after_work_task_limit: int = Field(default=3, ge=1, le=8)
    review_evening_limit: int = Field(default=3, ge=1, le=8)
    review_weekend_limit: int = Field(default=8, ge=1, le=8)
    review_routine_shopping_limit: int = Field(default=10, ge=1, le=50)
    review_task_spacing_days: int = Field(default=2, ge=1)
    review_routine_shopping_spacing_days: int = Field(default=1, ge=1)
    review_idea_spacing_days: int = Field(default=7, ge=1)
    review_thought_spacing_days: int = Field(default=30, ge=1)
    review_planned_purchase_spacing_days: int = Field(default=14, ge=1)
    review_reference_spacing_days: int = Field(default=30, ge=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    def action_policy(self) -> ActionPolicy:
        return ActionPolicy(
            keep_task_days=self.keep_task_days,
            keep_idea_days=self.keep_idea_days,
            keep_thought_days=self.keep_thought_days,
            keep_reference_days=self.keep_reference_days,
            keep_planned_purchase_days=self.keep_planned_purchase_days,
            planned_purchase_post_bought_cooldown_days=(
                self.planned_purchase_post_bought_cooldown_days
            ),
        )

    def review_policy(self) -> ReviewPolicy:
        return ReviewPolicy(
            morning_limit=self.review_morning_limit,
            after_work_task_limit=self.review_after_work_task_limit,
            evening_limit=self.review_evening_limit,
            weekend_limit=self.review_weekend_limit,
            routine_shopping_limit=self.review_routine_shopping_limit,
            task_spacing_days=self.review_task_spacing_days,
            routine_shopping_spacing_days=self.review_routine_shopping_spacing_days,
            idea_spacing_days=self.review_idea_spacing_days,
            thought_spacing_days=self.review_thought_spacing_days,
            planned_purchase_spacing_days=self.review_planned_purchase_spacing_days,
            reference_spacing_days=self.review_reference_spacing_days,
        )
