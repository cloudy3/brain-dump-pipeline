import httpx
import pytest
from pydantic import SecretStr

from app.integrations.telegram import TelegramBotAPIClient, TelegramDeliveryError


@pytest.mark.asyncio
async def test_send_message_uses_telegram_bot_api_payload() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bot123456:test-token/sendMessage"
        assert request.method == "POST"
        assert request.content == b'{"chat_id":123,"text":"Acknowledged"}'
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramBotAPIClient(
            bot_token=SecretStr("123456:test-token"),
            api_base_url="https://api.telegram.test",
            timeout_seconds=1,
            http_client=http_client,
        )

        await client.send_message(chat_id=123, text="Acknowledged")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_message"),
    [
        (httpx.Response(502, text="bad gateway"), "Telegram API request failed"),
        (httpx.Response(200, json={"ok": False}), "Telegram API rejected the message"),
    ],
)
async def test_send_message_raises_safe_error(
    response: httpx.Response,
    expected_message: str,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramBotAPIClient(
            bot_token=SecretStr("secret-token"),
            api_base_url="https://api.telegram.test",
            timeout_seconds=1,
            http_client=http_client,
        )

        with pytest.raises(TelegramDeliveryError, match=expected_message):
            await client.send_message(chat_id=123, text="Acknowledged")

