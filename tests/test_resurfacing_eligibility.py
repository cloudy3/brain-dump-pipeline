from datetime import UTC, date, datetime

import pytest

from app.models.classification import (
    CaptureType,
    Confidence,
    Domain,
    ShoppingKind,
    SurfaceContext,
)
from app.models.reviews import (
    ReviewCandidateCriteria,
    ReviewItem,
    ReviewPolicy,
    ReviewRequest,
    ReviewWindow,
)
from app.services.resurfacing import ResurfacingService

REFERENCE = datetime(2026, 8, 19, 19, tzinfo=UTC)


class FakeReviewRepository:
    def __init__(self, items: list[ReviewItem]) -> None:
        self.items = items
        self.criteria: list[ReviewCandidateCriteria] = []

    async def list_candidates(
        self, *, criteria: ReviewCandidateCriteria
    ) -> list[ReviewItem]:
        self.criteria.append(criteria)
        return self.items


def item(
    page: int,
    *,
    type_: CaptureType = CaptureType.TASK,
    domain: Domain = Domain.PERSONAL,
    context: SurfaceContext = SurfaceContext.MORNING,
    shopping_kind: ShoppingKind = ShoppingKind.NONE,
    created: date = date(2026, 7, 1),
    due: date | None = None,
    snoozed: date | None = None,
    surfaced: date | None = None,
    focused: bool = False,
    title: str | None = None,
) -> ReviewItem:
    page_id = f"{page:032x}"
    return ReviewItem(
        page_id=page_id,
        page_url=f"https://www.notion.so/{page_id}",
        title=title or f"Item {page}",
        type=type_,
        domain=domain,
        shopping_kind=shopping_kind,
        purchase_focus=focused,
        due=due,
        snoozed_until=snoozed,
        confidence=Confidence.HIGH,
        surface_context=context,
        created_at=datetime(created.year, created.month, created.day, tzinfo=UTC),
        last_surfaced=surfaced,
        location=None,
    )


async def plan(
    window: ReviewWindow,
    items: list[ReviewItem],
    *,
    policy: ReviewPolicy | None = None,
):
    repository = FakeReviewRepository(items)
    result = await ResurfacingService(repository=repository, policy=policy).build_plan(
        request=ReviewRequest(window=window, reference_time=REFERENCE)
    )
    assert repository.criteria == [
        ReviewCandidateCriteria(window=window, reference_date=date(2026, 8, 20))
    ]
    return result


@pytest.mark.parametrize(
    ("candidate", "eligible"),
    [
        (item(1), True),
        (item(2, context=SurfaceContext.AFTER_WORK), False),
        (item(3, context=SurfaceContext.EVENING), False),
        (item(4, type_=CaptureType.IDEA), False),
        (item(5, type_=CaptureType.THOUGHT), False),
        (item(6, type_=CaptureType.REFERENCE), False),
        (
            item(
                7,
                domain=Domain.SHOPPING,
                context=SurfaceContext.EVENING,
                shopping_kind=ShoppingKind.PLANNED,
            ),
            False,
        ),
        (
            item(
                8,
                domain=Domain.SHOPPING,
                context=SurfaceContext.AFTER_WORK,
                shopping_kind=ShoppingKind.ROUTINE,
            ),
            False,
        ),
    ],
)
async def test_morning_is_strictly_morning_tasks(
    candidate: ReviewItem, eligible: bool
) -> None:
    result = await plan(ReviewWindow.MORNING, [candidate])
    assert bool(result.entries) is eligible


async def test_morning_limit_snooze_and_fewer_than_limit() -> None:
    items = [item(number) for number in range(1, 5)]
    items.append(item(5, snoozed=date(2026, 8, 21)))
    result = await plan(ReviewWindow.MORNING, items)
    assert len(result.entries) == 3
    result = await plan(ReviewWindow.MORNING, [item(1)])
    assert len(result.entries) == 1


async def test_after_work_tasks_and_grouped_routine_shopping() -> None:
    candidates = [
        item(1, context=SurfaceContext.AFTER_WORK),
        item(2, context=SurfaceContext.MORNING),
        item(
            3,
            domain=Domain.SHOPPING,
            context=SurfaceContext.AFTER_WORK,
            shopping_kind=ShoppingKind.ROUTINE,
            title="Coffee",
        ),
        item(
            4,
            domain=Domain.SHOPPING,
            context=SurfaceContext.EVENING,
            shopping_kind=ShoppingKind.PLANNED,
        ),
        item(
            5,
            domain=Domain.SHOPPING,
            context=SurfaceContext.AFTER_WORK,
            shopping_kind=ShoppingKind.ROUTINE,
            snoozed=date(2026, 8, 21),
        ),
    ]
    result = await plan(ReviewWindow.AFTER_WORK, candidates)
    assert [entry.item.page_id for entry in result.entries] == [f"{1:032x}"]
    assert result.routine_shopping is not None
    assert [value.title for value in result.routine_shopping.items] == ["Coffee"]


async def test_after_work_task_and_shopping_limits_and_stable_group_order() -> None:
    tasks = [item(number, context=SurfaceContext.AFTER_WORK) for number in range(1, 5)]
    shopping = [
        item(
            10 + number,
            domain=Domain.SHOPPING,
            context=SurfaceContext.AFTER_WORK,
            shopping_kind=ShoppingKind.ROUTINE,
            created=date(2026, 7, number + 1),
            title=f"Shop {number}",
        )
        for number in range(4)
    ]
    policy = ReviewPolicy(routine_shopping_limit=2)
    result = await plan(ReviewWindow.AFTER_WORK, [*tasks, *reversed(shopping)], policy=policy)
    assert len(result.entries) == 3
    assert result.routine_shopping is not None
    assert [value.title for value in result.routine_shopping.items] == ["Shop 0", "Shop 1"]
    assert result.routine_shopping.additional_count == 2


async def test_new_routine_purchase_is_not_subject_to_idea_minimum_age() -> None:
    routine = item(
        1,
        type_=CaptureType.IDEA,
        domain=Domain.SHOPPING,
        context=SurfaceContext.AFTER_WORK,
        shopping_kind=ShoppingKind.ROUTINE,
        created=date(2026, 8, 20),
    )
    result = await plan(ReviewWindow.AFTER_WORK, [routine])
    assert result.routine_shopping is not None
    assert result.routine_shopping.items == (routine,)


async def test_evening_categories_caps_and_minimum_ages() -> None:
    candidates = [
        item(1, context=SurfaceContext.EVENING),
        item(2, context=SurfaceContext.ANYTIME),
        item(3, type_=CaptureType.IDEA, domain=Domain.PORTFOLIO, context=SurfaceContext.EVENING),
        item(4, type_=CaptureType.IDEA, domain=Domain.TECH, context=SurfaceContext.EVENING),
        item(
            5,
            domain=Domain.SHOPPING,
            context=SurfaceContext.EVENING,
            shopping_kind=ShoppingKind.PLANNED,
            focused=True,
        ),
        item(
            6,
            domain=Domain.SHOPPING,
            context=SurfaceContext.EVENING,
            shopping_kind=ShoppingKind.PLANNED,
            focused=True,
        ),
        item(7, type_=CaptureType.THOUGHT, context=SurfaceContext.EVENING),
        item(
            8,
            type_=CaptureType.THOUGHT,
            context=SurfaceContext.EVENING,
            created=date(2026, 8, 1),
        ),
        item(9, type_=CaptureType.REFERENCE, context=SurfaceContext.ON_DEMAND),
    ]
    result = await plan(ReviewWindow.EVENING, candidates)
    assert len(result.entries) == 3
    assert sum(entry.item.is_planned_purchase for entry in result.entries) <= 1
    assert sum(entry.item.type is CaptureType.THOUGHT for entry in result.entries) <= 1
    assert all(entry.item.page_id != f"{8:032x}" for entry in result.entries)
    assert all(entry.item.page_id != f"{9:032x}" for entry in result.entries)


async def test_weekend_categories_diversity_and_limit() -> None:
    candidates = [
        item(
            number,
            type_=CaptureType.IDEA,
            domain=Domain.PORTFOLIO,
            context=SurfaceContext.WEEKEND,
        )
        for number in range(1, 8)
    ]
    candidates.extend(
        [
            item(10, context=SurfaceContext.WEEKEND, domain=Domain.PERSONAL),
            item(11, type_=CaptureType.IDEA, context=SurfaceContext.WEEKEND, domain=Domain.DATING),
            item(12, type_=CaptureType.IDEA, context=SurfaceContext.WEEKEND, domain=Domain.TRAVEL),
            item(13, type_=CaptureType.THOUGHT, context=SurfaceContext.WEEKEND),
            item(
                14,
                domain=Domain.SHOPPING,
                context=SurfaceContext.WEEKEND,
                shopping_kind=ShoppingKind.PLANNED,
                focused=True,
            ),
        ]
    )
    result = await plan(ReviewWindow.WEEKEND, candidates)
    assert len(result.entries) == 8
    assert len({entry.item.domain for entry in result.entries[:5]}) > 1
    assert sum(entry.item.is_planned_purchase for entry in result.entries) <= 1
    assert sum(entry.item.type is CaptureType.THOUGHT for entry in result.entries) <= 1


@pytest.mark.parametrize(
    ("snoozed", "eligible"),
    [
        (date(2026, 8, 21), False),
        (date(2026, 8, 20), True),
        (date(2026, 8, 19), True),
    ],
)
async def test_snooze_expiry_boundary(snoozed: date, eligible: bool) -> None:
    result = await plan(ReviewWindow.MORNING, [item(1, snoozed=snoozed)])
    assert bool(result.entries) is eligible


@pytest.mark.parametrize("window", list(ReviewWindow))
async def test_empty_review_is_successful(window: ReviewWindow) -> None:
    result = await plan(window, [])
    assert result.is_empty is True
