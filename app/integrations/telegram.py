"""Telegram Bot API client and its application-facing interface."""

from typing import Protocol

import httpx
from pydantic import SecretStr

from app.models.telegram import InlineKeyboardMarkup


class TelegramDeliveryError(RuntimeError):
    """Raised when Telegram does not accept an outbound message."""


class TelegramClient(Protocol):
    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        disable_notification: bool = False,
    ) -> None: ...

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None: ...

    async def edit_message_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None: ...


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
        bot_api_url = f"{api_base_url.rstrip('/')}/bot{token}"
        self._send_message_url = f"{bot_api_url}/sendMessage"
        self._answer_callback_query_url = f"{bot_api_url}/answerCallbackQuery"
        self._edit_message_text_url = f"{bot_api_url}/editMessageText"
        self._http_client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_http_client = http_client is None

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        disable_notification: bool = False,
    ) -> None:
        payload: dict[str, object] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup.model_dump(exclude_none=True)
        if disable_notification:
            payload["disable_notification"] = True
        await self._post(self._send_message_url, payload)

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        payload: dict[str, object] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text is not None:
            payload["text"] = text
        await self._post(self._answer_callback_query_url, payload)

    async def edit_message_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "reply_markup": (
                reply_markup.model_dump(exclude_none=True)
                if reply_markup is not None
                else {"inline_keyboard": []}
            ),
        }
        await self._post(self._edit_message_text_url, payload)

    async def _post(self, url: str, payload: dict[str, object]) -> None:
        try:
            response = await self._http_client.post(
                url,
                json=payload,
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
