import logging
from copy import deepcopy

import httpx
import pytest

from app.core.config import Settings
from app.integrations.telegram import TelegramDeliveryError
from app.main import create_app
from app.models.capture import CaptureInput, CaptureSaveStatus
from app.repositories.captures import CapturePersistenceError
from app.services.telegram_updates import PERSISTED_ACKNOWLEDGEMENT
from tests.conftest import (
    ALLOWED_CHAT_ID,
    BOT_TOKEN,
    FakeCaptureRepository,
    FakeTelegramClient,
)


async def test_authorized_text_update_is_acknowledged(
    client: httpx.AsyncClient,
    telegram_client: FakeTelegramClient,
    capture_repository: FakeCaptureRepository,
    webhook_headers: dict[str, str],
    text_update: dict[str, object],
) -> None:
    response = await client.post(
        "/webhooks/telegram", headers=webhook_headers, json=text_update
    )

    assert response.status_code == 200
    assert response.json() == {"status": "acknowledged"}
    assert telegram_client.sent_messages == [
        (ALLOWED_CHAT_ID, PERSISTED_ACKNOWLEDGEMENT)
    ]
    assert capture_repository.saved_captures == [
        CaptureInput(
            original_input="Remember the architecture sketch",
            telegram_update_id=9001,
            telegram_message_id=42,
        )
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
    capture_repository: FakeCaptureRepository,
    text_update: dict[str, object],
    headers: dict[str, str],
) -> None:
    response = await client.post("/webhooks/telegram", headers=headers, json=text_update)

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert telegram_client.sent_messages == []
    assert capture_repository.save_attempts == 0


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
    capture_repository: FakeCaptureRepository,
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
    assert capture_repository.save_attempts == 0


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
    capture_repository: FakeCaptureRepository,
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
    assert capture_repository.save_attempts == 0


async def test_duplicate_delivery_persists_once_and_acknowledges_each_delivery(
    client: httpx.AsyncClient,
    telegram_client: FakeTelegramClient,
    capture_repository: FakeCaptureRepository,
    webhook_headers: dict[str, str],
    text_update: dict[str, object],
) -> None:
    first_response = await client.post(
        "/webhooks/telegram", headers=webhook_headers, json=text_update
    )
    second_response = await client.post(
        "/webhooks/telegram", headers=webhook_headers, json=text_update
    )

    assert first_response.status_code == second_response.status_code == 200
    assert len(capture_repository.saved_captures) == 1
    assert capture_repository.save_attempts == 2
    assert telegram_client.sent_messages == [
        (ALLOWED_CHAT_ID, PERSISTED_ACKNOWLEDGEMENT),
        (ALLOWED_CHAT_ID, PERSISTED_ACKNOWLEDGEMENT),
    ]


async def test_acknowledgement_happens_after_persistence(
    settings: Settings,
    webhook_headers: dict[str, str],
    text_update: dict[str, object],
) -> None:
    events: list[str] = []
    repository = FakeCaptureRepository(events=events)
    telegram = FakeTelegramClient(events=events)
    application = create_app(
        settings=settings,
        telegram_client=telegram,
        capture_repository=repository,
    )

    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/webhooks/telegram", headers=webhook_headers, json=text_update
            )

    assert response.status_code == 200
    assert events == ["repository", "telegram"]


class FailingCaptureRepository:
    async def save_if_new(self, capture: CaptureInput) -> CaptureSaveStatus:
        raise CapturePersistenceError("safe persistence error")

    async def validate(self) -> None:
        raise CapturePersistenceError("safe persistence error")


async def test_notion_failure_does_not_send_success_acknowledgement(
    settings: Settings,
    webhook_headers: dict[str, str],
    text_update: dict[str, object],
) -> None:
    telegram = FakeTelegramClient()
    application = create_app(
        settings=settings,
        telegram_client=telegram,
        capture_repository=FailingCaptureRepository(),
    )

    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/webhooks/telegram", headers=webhook_headers, json=text_update
            )

    assert response.status_code == 502
    assert response.json() == {"detail": "Capture could not be saved"}
    assert telegram.sent_messages == []


class FailingTelegramClient:
    async def send_message(self, *, chat_id: int, text: str) -> None:
        raise TelegramDeliveryError("safe error")


async def test_telegram_failure_returns_explicit_error_without_sensitive_logs(
    settings: Settings,
    webhook_headers: dict[str, str],
    text_update: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = FakeCaptureRepository()
    application = create_app(
        settings=settings,
        telegram_client=FailingTelegramClient(),
        capture_repository=repository,
    )

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
    assert len(repository.saved_captures) == 1
    combined_logs = " ".join(record.getMessage() for record in caplog.records)
    assert BOT_TOKEN not in combined_logs
    assert "Remember the architecture sketch" not in combined_logs
