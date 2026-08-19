from datetime import UTC, date, datetime

import pytest

from app.integrations.telegram import TelegramDeliveryError
from app.models.classification import CaptureType, Confidence, Domain, ShoppingKind, SurfaceContext
from app.models.reviews import (
    DeadlineUrgency,
    ReviewEntry,
    ReviewItem,
    ReviewPlan,
    ReviewWindow,
    RoutineShoppingGroup,
)
from app.repositories.reviews import ReviewPersistenceError
from app.services.item_views import ItemActionViewBuilder
from app.services.review_delivery import ReviewDeliveryService
from tests.conftest import FakeTelegramClient


def item(
    number: int,
    *,
    title: str = "Bring power bank",
    type_: CaptureType = CaptureType.TASK,
    domain: Domain = Domain.PERSONAL,
    shopping_kind: ShoppingKind = ShoppingKind.NONE,
    due: date | None = None,
    focused: bool = False,
    location: str | None = None,
) -> ReviewItem:
    page_id = f"{number:032x}"
    return ReviewItem(
        page_id=page_id,
        page_url=f"https://www.notion.so/{page_id}",
        title=title,
        type=type_,
        domain=domain,
        shopping_kind=shopping_kind,
        purchase_focus=focused,
        due=due,
        snoozed_until=None,
        confidence=Confidence.HIGH,
        surface_context=SurfaceContext.EVENING,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        last_surfaced=None,
        location=location,
    )


class FakePlanner:
    def __init__(self, plan: ReviewPlan) -> None:
        self.plan = plan
        self.calls = 0

    async def build_plan(self, **_: object) -> ReviewPlan:
        self.calls += 1
        return self.plan


class FakeReviewRepository:
    def __init__(self, *, failures: int = 0, events: list[str] | None = None) -> None:
        self.failures = failures
        self.calls: list[tuple[tuple[str, ...], date]] = []
        self.events = events

    async def record_last_surfaced(
        self, *, page_ids: tuple[str, ...], surfaced_on: date
    ) -> None:
        if self.events is not None:
            self.events.append("notion")
        self.calls.append((page_ids, surfaced_on))
        if len(self.calls) <= self.failures:
            raise ReviewPersistenceError("safe fake failure")


class OrderedTelegram(FakeTelegramClient):
    def __init__(
        self,
        *,
        events: list[str],
        fail_main: bool = False,
        fail_silent_at: int | None = None,
    ) -> None:
        super().__init__(events=events)
        self.fail_main = fail_main
        self.fail_silent_at = fail_silent_at
        self.silent_attempts = 0

    async def send_message(self, **kwargs: object) -> None:
        silent = kwargs.get("disable_notification") is True
        if not silent and self.fail_main:
            raise TelegramDeliveryError("main failed")
        if silent:
            self.silent_attempts += 1
            if self.silent_attempts == self.fail_silent_at:
                raise TelegramDeliveryError("action failed")
        await super().send_message(**kwargs)  # type: ignore[arg-type]


def plan_with_items(*, include_routine: bool = False) -> ReviewPlan:
    generated = datetime(2026, 8, 22, 20, tzinfo=UTC)  # 23 Aug in Singapore
    task = item(1, due=date(2026, 8, 23))
    group = None
    if include_routine:
        routine = item(
            2,
            title="Milk",
            type_=CaptureType.IDEA,
            domain=Domain.SHOPPING,
            shopping_kind=ShoppingKind.ROUTINE,
        )
        group = RoutineShoppingGroup(items=(routine,), total_eligible_count=3)
    return ReviewPlan(
        window=ReviewWindow.AFTER_WORK if include_routine else ReviewWindow.MORNING,
        generated_at=generated,
        entries=(ReviewEntry(item=task, score=100, urgency=DeadlineUrgency.TODAY),),
        routine_shopping=group,
    )


async def test_empty_plan_sends_and_writes_nothing() -> None:
    empty = ReviewPlan(
        window=ReviewWindow.EVENING,
        generated_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
    )
    telegram = FakeTelegramClient()
    repository = FakeReviewRepository()
    result = await ReviewDeliveryService(
        planner=FakePlanner(empty),
        repository=repository,  # type: ignore[arg-type]
        telegram_client=telegram,
        chat_id=123,
    ).deliver(window=ReviewWindow.EVENING, execution_time=empty.generated_at)

    assert result.item_count == 0
    assert telegram.sent_messages == []
    assert repository.calls == []


async def test_success_orders_primary_then_persistence_then_silent_actions() -> None:
    events: list[str] = []
    telegram = OrderedTelegram(events=events)
    repository = FakeReviewRepository(events=events)
    plan = plan_with_items(include_routine=True)

    result = await ReviewDeliveryService(
        planner=FakePlanner(plan),
        repository=repository,  # type: ignore[arg-type]
        telegram_client=telegram,
        chat_id=123,
    ).deliver(window=ReviewWindow.AFTER_WORK, execution_time=plan.generated_at)

    assert result.item_count == 2
    assert result.last_surfaced_recorded is True
    assert events == ["telegram", "notion", "telegram", "telegram"]
    assert telegram.sent_silently == [False, True, True]
    assert repository.calls == [((f"{1:032x}", f"{2:032x}"), date(2026, 8, 23))]
    assert "+ 2 more shopping items" in telegram.sent_messages[0][1]
    assert all(markup is not None for markup in telegram.sent_markups[1:])
    assert telegram.sent_markups[1] == ItemActionViewBuilder.action_keyboard(plan.entries[0].item)


async def test_primary_failure_never_records_last_surfaced() -> None:
    telegram = OrderedTelegram(events=[], fail_main=True)
    repository = FakeReviewRepository()
    plan = plan_with_items()
    service = ReviewDeliveryService(
        planner=FakePlanner(plan),
        repository=repository,  # type: ignore[arg-type]
        telegram_client=telegram,
        chat_id=123,
    )

    with pytest.raises(TelegramDeliveryError):
        await service.deliver(window=ReviewWindow.MORNING, execution_time=plan.generated_at)
    assert repository.calls == []


async def test_persistence_retries_once_without_resending_primary() -> None:
    telegram = FakeTelegramClient()
    repository = FakeReviewRepository(failures=2)
    plan = plan_with_items()
    result = await ReviewDeliveryService(
        planner=FakePlanner(plan),
        repository=repository,  # type: ignore[arg-type]
        telegram_client=telegram,
        chat_id=123,
    ).deliver(window=ReviewWindow.MORNING, execution_time=plan.generated_at)

    assert result.last_surfaced_recorded is False
    assert len(repository.calls) == 2
    assert len(telegram.sent_messages) == 2
    assert telegram.sent_silently == [False, True]


async def test_secondary_failure_does_not_fail_delivered_review() -> None:
    telegram = OrderedTelegram(events=[], fail_silent_at=1)
    repository = FakeReviewRepository()
    plan = plan_with_items(include_routine=True)

    result = await ReviewDeliveryService(
        planner=FakePlanner(plan),
        repository=repository,  # type: ignore[arg-type]
        telegram_client=telegram,
        chat_id=123,
    ).deliver(window=ReviewWindow.AFTER_WORK, execution_time=plan.generated_at)

    assert result.item_count == 2
    assert len(repository.calls) == 1
    assert telegram.silent_attempts == 2
