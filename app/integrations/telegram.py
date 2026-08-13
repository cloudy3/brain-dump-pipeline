"""Telegram Bot API client and its application-facing interface."""

from typing import Protocol

import httpx
from pydantic import SecretStr


class TelegramDeliveryError(RuntimeError):
    """Raised when Telegram does not accept an outbound message."""


class TelegramClient(Protocol):
    async def send_message(self, *, chat_id: int, text: str) -> None:
        """Send a plain-text Telegram message."""


class TelegramBotAPIClient:
    """Small async adapter around Telegram's sendMessage endpoint."""

    def __init__(
        self,
        *,
        bot_token: SecretStr,
        api_base_url: str,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        token = bot_token.get_secret_value()
        self._send_message_url = f"{api_base_url.rstrip('/')}/bot{token}/sendMessage"
        self._http_client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_http_client = http_client is None

    async def send_message(self, *, chat_id: int, text: str) -> None:
        try:
            response = await self._http_client.post(
                self._send_message_url,
                json={"chat_id": chat_id, "text": text},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise TelegramDeliveryError("Telegram API request failed") from error

        if payload.get("ok") is not True:
            raise TelegramDeliveryError("Telegram API rejected the message")

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()

