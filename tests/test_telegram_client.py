import httpx
import pytest
from pydantic import SecretStr

from app.integrations.telegram import TelegramBotAPIClient, TelegramDeliveryError
from app.models.telegram import InlineKeyboardButton, InlineKeyboardMarkup


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
async def test_inline_message_callback_answer_and_edit_use_expected_payloads() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": True})

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Done", callback_data="bd1:d:page")],
            [InlineKeyboardButton(text="Open", url="https://www.notion.so/page")],
        ]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramBotAPIClient(
            bot_token=SecretStr("123456:test-token"),
            api_base_url="https://api.telegram.test",
            timeout_seconds=1,
            http_client=http_client,
        )

        await client.send_message(chat_id=123, text="Saved", reply_markup=markup)
        await client.answer_callback_query(callback_query_id="query-1")
        await client.edit_message_text(
            chat_id=123,
            message_id=456,
            text="Done\nItem",
        )

    assert [request.url.path for request in requests] == [
        "/bot123456:test-token/sendMessage",
        "/bot123456:test-token/answerCallbackQuery",
        "/bot123456:test-token/editMessageText",
    ]
    assert requests[0].content == (
        b'{"chat_id":123,"text":"Saved","reply_markup":{"inline_keyboard":'
        b'[[{"text":"Done","callback_data":"bd1:d:page"}],'
        b'[{"text":"Open","url":"https://www.notion.so/page"}]]}}'
    )
    assert requests[1].content == (b'{"callback_query_id":"query-1","show_alert":false}')
    assert requests[2].content == (
        b'{"chat_id":123,"message_id":456,"text":"Done\\nItem",'
        b'"reply_markup":{"inline_keyboard":[]}}'
    )


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
