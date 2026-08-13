from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.telegram import TelegramClient
from app.main import create_app

WEBHOOK_SECRET = "test-webhook-secret"
BOT_TOKEN = "123456:test-bot-token"
ALLOWED_USER_ID = 111_222_333
ALLOWED_CHAT_ID = 111_222_333


class FakeTelegramClient:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str) -> None:
        self.sent_messages.append((chat_id, text))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_bot_token=SecretStr(BOT_TOKEN),
        telegram_webhook_secret=SecretStr(WEBHOOK_SECRET),
        telegram_allowed_user_id=ALLOWED_USER_ID,
        telegram_allowed_chat_id=ALLOWED_CHAT_ID,
    )


@pytest.fixture
def telegram_client() -> FakeTelegramClient:
    return FakeTelegramClient()


@pytest_asyncio.fixture
async def client(
    settings: Settings,
    telegram_client: TelegramClient,
) -> AsyncIterator[httpx.AsyncClient]:
    application = create_app(settings=settings, telegram_client=telegram_client)
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
