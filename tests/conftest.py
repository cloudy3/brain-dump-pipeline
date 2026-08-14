from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.telegram import TelegramClient
from app.main import create_app
from app.models.capture import CaptureInput, CaptureSaveStatus
from app.repositories.captures import CaptureRepository

WEBHOOK_SECRET = "test-webhook-secret"
BOT_TOKEN = "123456:test-bot-token"
ALLOWED_USER_ID = 111_222_333
ALLOWED_CHAT_ID = 111_222_333
NOTION_DATABASE_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
NOTION_DATA_SOURCE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class FakeTelegramClient:
    def __init__(self, *, events: list[str] | None = None) -> None:
        self.sent_messages: list[tuple[int, str]] = []
        self._events = events

    async def send_message(self, *, chat_id: int, text: str) -> None:
        if self._events is not None:
            self._events.append("telegram")
        self.sent_messages.append((chat_id, text))


class FakeCaptureRepository:
    def __init__(self, *, events: list[str] | None = None) -> None:
        self.saved_captures: list[CaptureInput] = []
        self.save_attempts = 0
        self._identities: set[tuple[int, int]] = set()
        self._events = events

    async def save_if_new(self, capture: CaptureInput) -> CaptureSaveStatus:
        self.save_attempts += 1
        if self._events is not None:
            self._events.append("repository")
        identity = (capture.telegram_update_id, capture.telegram_message_id)
        if identity in self._identities:
            return CaptureSaveStatus.EXISTING
        self._identities.add(identity)
        self.saved_captures.append(capture)
        return CaptureSaveStatus.CREATED

    async def validate(self) -> None:
        return None


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_bot_token=SecretStr(BOT_TOKEN),
        telegram_webhook_secret=SecretStr(WEBHOOK_SECRET),
        telegram_allowed_user_id=ALLOWED_USER_ID,
        telegram_allowed_chat_id=ALLOWED_CHAT_ID,
        notion_api_token=SecretStr("test-notion-token"),
        notion_brain_dump_database_id=NOTION_DATABASE_ID,
        notion_brain_dump_data_source_id=NOTION_DATA_SOURCE_ID,
    )


@pytest.fixture
def telegram_client() -> FakeTelegramClient:
    return FakeTelegramClient()


@pytest.fixture
def capture_repository() -> FakeCaptureRepository:
    return FakeCaptureRepository()


@pytest_asyncio.fixture
async def client(
    settings: Settings,
    telegram_client: TelegramClient,
    capture_repository: CaptureRepository,
) -> AsyncIterator[httpx.AsyncClient]:
    application = create_app(
        settings=settings,
        telegram_client=telegram_client,
        capture_repository=capture_repository,
    )
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as test_client:
            yield test_client


@pytest.fixture
def webhook_headers() -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET}


@pytest.fixture
def text_update() -> dict[str, object]:
    return {
        "update_id": 9001,
        "message": {
            "message_id": 42,
            "from": {"id": ALLOWED_USER_ID, "is_bot": False, "first_name": "Owner"},
            "chat": {"id": ALLOWED_CHAT_ID, "type": "private"},
            "date": 1_700_000_000,
            "text": "Remember the architecture sketch",
        },
    }
