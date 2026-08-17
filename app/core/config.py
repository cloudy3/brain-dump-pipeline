"""Environment-backed application configuration."""

from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
