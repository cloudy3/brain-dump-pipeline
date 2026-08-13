import logging
from copy import deepcopy

import httpx
import pytest

from app.core.config import Settings
from app.integrations.telegram import TelegramDeliveryError
from app.main import create_app
from app.services.telegram_updates import TEMPORARY_ACKNOWLEDGEMENT
from tests.conftest import ALLOWED_CHAT_ID, BOT_TOKEN, FakeTelegramClient


async def test_authorized_text_update_is_acknowledged(
    client: httpx.AsyncClient,
    telegram_client: FakeTelegramClient,
    webhook_headers: dict[str, str],
    text_update: dict[str, object],
) -> None:
    response = await client.post(
        "/webhooks/telegram", headers=webhook_headers, json=text_update
    )

    assert response.status_code == 200
    assert response.json() == {"status": "acknowledged"}
    assert telegram_client.sent_messages == [
        (ALLOWED_CHAT_ID, TEMPORARY_ACKNOWLEDGEMENT)
    ]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
    ],
)
async def test_missing_or_invalid_webhook_secret_is_rejected(
    client: httpx.AsyncClient,
    telegram_client: FakeTelegramClient,
    text_update: dict[str, object],
    headers: dict[str, str],
) -> None:
    response = await client.post("/webhooks/telegram", headers=headers, json=text_update)

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert telegram_client.sent_messages == []


@pytest.mark.parametrize(
    ("field", "unauthorized_id"),
    [
        ("from", 999_888_777),
        ("chat", -999_888_777),
    ],
)
async def test_unauthorized_user_or_chat_is_rejected(
    client: httpx.AsyncClient,
    telegram_client: FakeTelegramClient,
    webhook_headers: dict[str, str],
    text_update: dict[str, object],
    field: str,
    unauthorized_id: int,
) -> None:
    update = deepcopy(text_update)
    update["message"][field]["id"] = unauthorized_id  # type: ignore[index]

    response = await client.post("/webhooks/telegram", headers=webhook_headers, json=update)

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert telegram_client.sent_messages == []


@pytest.mark.parametrize(
    "update",
    [
        {"update_id": 9002, "callback_query": {"id": "not-supported-yet"}},
        {
            "update_id": 9003,
            "message": {
                "message_id": 43,
                "from": {"id": 111_222_333},
                "chat": {"id": 111_222_333},
                "photo": [{"file_id": "not-supported-yet"}],
            },
        },
    ],
)
async def test_unsupported_updates_are_deterministic_noops(
    client: httpx.AsyncClient,
    telegram_client: FakeTelegramClient,
    webhook_headers: dict[str, str],
    update: dict[str, object],
) -> None:
    first_response = await client.post(
        "/webhooks/telegram", headers=webhook_headers, json=update
    )
    second_response = await client.post(
        "/webhooks/telegram", headers=webhook_headers, json=update
    )

    assert first_response.status_code == second_response.status_code == 200
    assert first_response.json() == second_response.json() == {"status": "ignored"}
    assert telegram_client.sent_messages == []


class FailingTelegramClient:
    async def send_message(self, *, chat_id: int, text: str) -> None:
        raise TelegramDeliveryError("safe error")


async def test_telegram_failure_returns_explicit_error_without_sensitive_logs(
    settings: Settings,
    webhook_headers: dict[str, str],
    text_update: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = create_app(settings=settings, telegram_client=FailingTelegramClient())

    with caplog.at_level(logging.INFO):
        async with application.router.lifespan_context(application):
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as failing_client:
                response = await failing_client.post(
                    "/webhooks/telegram",
                    headers=webhook_headers,
                    json=text_update,
                )

    assert response.status_code == 502
    assert response.json() == {"detail": "Telegram acknowledgement failed"}
    combined_logs = " ".join(record.getMessage() for record in caplog.records)
    assert BOT_TOKEN not in combined_logs
    assert "Remember the architecture sketch" not in combined_logs
