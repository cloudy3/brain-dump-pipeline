"""Environment-backed application configuration."""

from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration required to start the Phase 1 application."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    telegram_bot_token: SecretStr = Field(min_length=1)
    telegram_webhook_secret: SecretStr = Field(min_length=1)
    telegram_allowed_user_id: int = Field(gt=0)
    telegram_allowed_chat_id: int
    telegram_api_base_url: AnyHttpUrl = AnyHttpUrl("https://api.telegram.org")
    telegram_request_timeout_seconds: float = Field(default=10.0, gt=0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

