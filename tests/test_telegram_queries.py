from collections.abc import AsyncIterator
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.main import create_app
from app.models.classification import CaptureType, Confidence, Domain, ShoppingKind
from app.models.queries import QueryItem, QueryPlan
from app.repositories.queries import QueryPersistenceError
from tests.conftest import (
    ALLOWED_CHAT_ID,
    ALLOWED_USER_ID,
    WEBHOOK_SECRET,
    FakeCaptureRepository,
    FakeClassifier,
    FakeTelegramClient,
)


class FakeQueryInterpreter:
    def __init__(self, plan: QueryPlan | None = None, error: Exception | None = None) -> None:
        self.plan = plan or QueryPlan(confidence=Confidence.HIGH)
        self.error = error
        self.calls: list[str] = []

    async def interpret_query(
        self,
        *,
        original_input: str,
        reference_datetime: datetime,
    ) -> QueryPlan:
        self.calls.append(original_input)
        if self.error is not None:
            raise self.error
        return self.plan


def item(page: int, *, title: str = "Architecture diagram") -> QueryItem:
    page_id = f"{page:032x}"
    return QueryItem(
        page_id=page_id,
        page_url=f"https://www.notion.so/{page_id}",
        title=title,
        type=CaptureType.IDEA,
        domain=Domain.PORTFOLIO,
        shopping_kind=ShoppingKind.NONE,
        purchase_focus=False,
        due=None,
        snoozed_until=None,
        confidence=Confidence.HIGH,
        location=None,
        original_input=title,
        created_at=datetime(2026, 8, page, tzinfo=ZoneInfo("Asia/Singapore")),
    )


def update(text: str, *, user_id: int = ALLOWED_USER_ID) -> dict[str, object]:
    return {
        "update_id": 9500,
        "message": {
            "message_id": 500,
            "from": {"id": user_id},
            "chat": {"id": ALLOWED_CHAT_ID},
            "text": text,
        },
    }


async def query_client(
    *,
    settings: object,
    telegram: FakeTelegramClient,
    repository: FakeCaptureRepository,
    interpreter: FakeQueryInterpreter,
) -> AsyncIterator[httpx.AsyncClient]:
    application = create_app(
        settings=settings,  # type: ignore[arg-type]
        telegram_client=telegram,
        capture_repository=repository,
        classifier=FakeClassifier(),
        query_interpreter=interpreter,
    )
    async with application.router.lifespan_context(application), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        yield client


async def post_query(
    client: httpx.AsyncClient, text: str, *, user_id: int = ALLOWED_USER_ID
) -> httpx.Response:
    return await client.post(
        "/webhooks/telegram",
        json=update(text, user_id=user_id),
        headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
    )


async def test_natural_query_does_not_enter_capture_path_and_reuses_actions(
    settings: object,
) -> None:
    telegram = FakeTelegramClient()
    repository = FakeCaptureRepository()
    repository.query_items = [item(1)]
    interpreter = FakeQueryInterpreter(
        QueryPlan(
            types=[CaptureType.IDEA],
            domains=[Domain.PORTFOLIO],
            confidence=Confidence.HIGH,
        )
    )
    async for client in query_client(
        settings=settings,
        telegram=telegram,
        repository=repository,
        interpreter=interpreter,
    ):
        response = await post_query(client, "show portfolio ideas")

    assert response.status_code == 200
    assert interpreter.calls == ["show portfolio ideas"]
    assert repository.lookup_attempts == 0
    assert repository.save_attempts == 0
    assert [text for _, text in telegram.sent_messages] == [
        "Portfolio · Idea\n1 saved item",
        "1. Architecture diagram\nSaved 1 Aug",
    ]
    assert [
        [button.text for button in row] for row in telegram.sent_markups[1].inline_keyboard
    ] == [["Keep", "Delete"], ["Open"]]


async def test_deterministic_shortcut_works_when_gemini_is_unavailable(settings: object) -> None:
    telegram = FakeTelegramClient()
    repository = FakeCaptureRepository()
    interpreter = FakeQueryInterpreter(error=TimeoutError())
    async for client in query_client(
        settings=settings,
        telegram=telegram,
        repository=repository,
        interpreter=interpreter,
    ):
        response = await post_query(client, "/today")

    assert response.status_code == 200
    assert interpreter.calls == []
    assert repository.save_attempts == 0
    assert telegram.sent_messages == [(ALLOWED_CHAT_ID, "No tasks are due today.")]


@pytest.mark.parametrize(
    "interpreter",
    [
        FakeQueryInterpreter(error=TimeoutError()),
        FakeQueryInterpreter(QueryPlan(confidence=Confidence.LOW)),
    ],
)
async def test_query_failure_is_reported_and_never_persisted(
    settings: object,
    interpreter: FakeQueryInterpreter,
) -> None:
    telegram = FakeTelegramClient()
    repository = FakeCaptureRepository()
    async for client in query_client(
        settings=settings,
        telegram=telegram,
        repository=repository,
        interpreter=interpreter,
    ):
        response = await post_query(client, "show my portfolio ideas")

    assert response.status_code == 200
    assert repository.lookup_attempts == 0
    assert repository.save_attempts == 0
    assert repository.query_criteria == []
    assert "I wasn't sure" in telegram.sent_messages[0][1]


async def test_notion_query_failure_is_reported_without_capture(settings: object) -> None:
    class FailingRepository(FakeCaptureRepository):
        async def search(self, **_: object) -> list[QueryItem]:
            raise QueryPersistenceError("safe failure")

    telegram = FakeTelegramClient()
    repository = FailingRepository()
    interpreter = FakeQueryInterpreter(QueryPlan(confidence=Confidence.HIGH))
    async for client in query_client(
        settings=settings,
        telegram=telegram,
        repository=repository,
        interpreter=interpreter,
    ):
        response = await post_query(client, "show saved things")

    assert response.status_code == 200
    assert repository.save_attempts == 0
    assert telegram.sent_messages == [
        (ALLOWED_CHAT_ID, "Couldn't search saved Brain Dump items. Please try again.")
    ]


async def test_results_are_capped_at_five_actionable_messages(settings: object) -> None:
    telegram = FakeTelegramClient()
    repository = FakeCaptureRepository()
    repository.query_items = [item(page) for page in range(1, 8)]
    interpreter = FakeQueryInterpreter(QueryPlan(confidence=Confidence.HIGH, limit=10))
    async for client in query_client(
        settings=settings,
        telegram=telegram,
        repository=repository,
        interpreter=interpreter,
    ):
        await post_query(client, "show saved items")

    assert len(telegram.sent_messages) == 6
    assert telegram.sent_messages[0][1].endswith("Showing 5 of 7")
    assert sum(markup is not None for markup in telegram.sent_markups) == 5


async def test_unauthorized_user_cannot_invoke_query_interpreter_or_repository(
    settings: object,
) -> None:
    telegram = FakeTelegramClient()
    repository = FakeCaptureRepository()
    interpreter = FakeQueryInterpreter()
    async for client in query_client(
        settings=settings,
        telegram=telegram,
        repository=repository,
        interpreter=interpreter,
    ):
        response = await post_query(client, "show tasks", user_id=999)

    assert response.status_code == 403
    assert interpreter.calls == []
    assert repository.query_criteria == []
    assert telegram.sent_messages == []


async def test_ambiguous_capture_still_uses_existing_capture_path(settings: object) -> None:
    telegram = FakeTelegramClient()
    repository = FakeCaptureRepository()
    interpreter = FakeQueryInterpreter(error=AssertionError("must not be called"))
    async for client in query_client(
        settings=settings,
        telegram=telegram,
        repository=repository,
        interpreter=interpreter,
    ):
        response = await post_query(client, "I should find somewhere to eat sometime")

    assert response.status_code == 200
    assert interpreter.calls == []
    assert repository.save_attempts == 1
