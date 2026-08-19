from datetime import UTC, date, datetime, timedelta

import pytest

from app.models.classification import (
    CaptureType,
    Confidence,
    Domain,
    ShoppingKind,
    SurfaceContext,
)
from app.models.reviews import (
    DeadlineUrgency,
    ReviewCandidateCriteria,
    ReviewItem,
    ReviewPolicy,
    ReviewRequest,
    ReviewWindow,
)
from app.services.resurfacing import ResurfacingService, deadline_urgency, review_score

TODAY = date(2026, 8, 20)
REFERENCE = datetime(2026, 8, 20, 11, tzinfo=UTC)


class Repository:
    def __init__(self, items: list[ReviewItem]) -> None:
        self.items = items

    async def list_candidates(
        self, *, criteria: ReviewCandidateCriteria
    ) -> list[ReviewItem]:
        return self.items


def item(
    page: int,
    *,
    type_: CaptureType = CaptureType.TASK,
    domain: Domain = Domain.PERSONAL,
    context: SurfaceContext = SurfaceContext.EVENING,
    created_days_ago: int = 60,
    due: date | None = None,
    surfaced_days_ago: int | None = None,
    snoozed_until: date | None = None,
    kind: ShoppingKind = ShoppingKind.NONE,
    focused: bool = False,
) -> ReviewItem:
    page_id = f"{page:032x}"
    created = TODAY - timedelta(days=created_days_ago)
    return ReviewItem(
        page_id=page_id,
        page_url=f"https://www.notion.so/{page_id}",
        title=f"Item {page}",
        type=type_,
        domain=domain,
        shopping_kind=kind,
        purchase_focus=focused,
        due=due,
        snoozed_until=snoozed_until,
        confidence=Confidence.HIGH,
        surface_context=context,
        created_at=datetime(created.year, created.month, created.day, tzinfo=UTC),
        last_surfaced=(
            TODAY - timedelta(days=surfaced_days_ago)
            if surfaced_days_ago is not None
            else None
        ),
        location=None,
    )


async def build(items: list[ReviewItem], window: ReviewWindow = ReviewWindow.EVENING):
    return await ResurfacingService(repository=Repository(items)).build_plan(
        request=ReviewRequest(window=window, reference_time=REFERENCE)
    )


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (8, DeadlineUrgency.LATER),
        (7, DeadlineUrgency.MODERATE),
        (3, DeadlineUrgency.MODERATE),
        (2, DeadlineUrgency.STRONG),
        (1, DeadlineUrgency.STRONG),
        (0, DeadlineUrgency.TODAY),
        (-1, DeadlineUrgency.OVERDUE),
    ],
)
def test_deadline_urgency_boundaries(days: int, expected: DeadlineUrgency) -> None:
    assert deadline_urgency(item(1, due=TODAY + timedelta(days=days)), TODAY) is expected


def test_deadline_year_boundary_and_non_task_due() -> None:
    reference = date(2026, 12, 31)
    assert (
        deadline_urgency(item(1, due=date(2027, 1, 1)), reference)
        is DeadlineUrgency.STRONG
    )
    assert (
        deadline_urgency(item(2, type_=CaptureType.IDEA, due=reference), reference)
        is DeadlineUrgency.NONE
    )


async def test_due_task_outranks_old_idea_and_thought_stays_low() -> None:
    result = await build(
        [
            item(1, due=TODAY + timedelta(days=1), created_days_ago=2),
            item(2, type_=CaptureType.IDEA, domain=Domain.PORTFOLIO, created_days_ago=200),
            item(3, type_=CaptureType.THOUGHT, created_days_ago=200),
        ]
    )
    assert [entry.item.page_id for entry in result.entries] == [
        f"{1:032x}",
        f"{2:032x}",
        f"{3:032x}",
    ]


async def test_recent_suppression_exact_intervals_and_never_bonus() -> None:
    recent_task = item(1, surfaced_days_ago=1)
    recent_idea = item(2, type_=CaptureType.IDEA, surfaced_days_ago=6)
    recent_thought = item(3, type_=CaptureType.THOUGHT, surfaced_days_ago=29)
    recent_planned = item(
        4,
        domain=Domain.SHOPPING,
        kind=ShoppingKind.PLANNED,
        surfaced_days_ago=13,
    )
    eligible = [
        item(5, surfaced_days_ago=2),
        item(6, type_=CaptureType.IDEA, surfaced_days_ago=7),
        item(7, type_=CaptureType.THOUGHT, surfaced_days_ago=30),
        item(
            8,
            domain=Domain.SHOPPING,
            kind=ShoppingKind.PLANNED,
            surfaced_days_ago=14,
        ),
        item(9),
    ]
    result = await build([recent_task, recent_idea, recent_thought, recent_planned, *eligible])
    selected_ids = {entry.item.page_id for entry in result.entries}
    assert not selected_ids & {f"{value:032x}" for value in range(1, 5)}
    assert f"{9:032x}" in selected_ids


async def test_same_day_suppression_and_urgent_prior_day_override() -> None:
    result = await build(
        [
            item(1, due=TODAY, surfaced_days_ago=0),
            item(2, due=TODAY, surfaced_days_ago=1),
            item(3, due=TODAY - timedelta(days=1), surfaced_days_ago=1),
        ]
    )
    assert [entry.item.page_id for entry in result.entries] == [f"{3:032x}", f"{2:032x}"]


async def test_old_idea_outranks_recently_eligible_idea() -> None:
    result = await build(
        [
            item(1, type_=CaptureType.IDEA, created_days_ago=100, surfaced_days_ago=20),
            item(2, type_=CaptureType.IDEA, created_days_ago=8, surfaced_days_ago=7),
        ]
    )
    assert result.entries[0].item.page_id == f"{1:032x}"


async def test_focused_purchase_ranks_higher_but_snooze_still_excludes() -> None:
    focused = item(
        1,
        domain=Domain.SHOPPING,
        kind=ShoppingKind.PLANNED,
        focused=True,
    )
    ordinary = item(2, domain=Domain.SHOPPING, kind=ShoppingKind.PLANNED)
    result = await build([ordinary, focused])
    assert [entry.item.page_id for entry in result.entries] == [f"{1:032x}"]

    focused = focused.model_copy(update={"snoozed_until": TODAY + timedelta(days=30)})
    result = await build([focused, ordinary])
    assert [entry.item.page_id for entry in result.entries] == [f"{2:032x}"]


async def test_planned_purchase_deadline_does_not_override_passive_spacing() -> None:
    purchase = item(
        1,
        domain=Domain.SHOPPING,
        kind=ShoppingKind.PLANNED,
        due=TODAY,
        surfaced_days_ago=1,
        focused=True,
    )
    result = await build([purchase])
    assert result.is_empty


def test_exact_score_components_are_centralized() -> None:
    policy = ReviewPolicy()
    candidate = item(1, due=TODAY, created_days_ago=100, focused=False)
    score = review_score(
        item=candidate,
        window=ReviewWindow.EVENING,
        reference_date=TODAY,
        urgency=DeadlineUrgency.TODAY,
        cooldown_overridden=False,
        policy=policy,
    )
    assert score == 20 + 30 + 60 + 18 + 12


async def test_ties_use_creation_time_then_page_id() -> None:
    candidates = [item(3), item(1), item(2)]
    result = await build(candidates)
    assert [entry.item.page_id for entry in result.entries] == [
        f"{1:032x}",
        f"{2:032x}",
        f"{3:032x}",
    ]


async def test_context_is_never_overridden_by_deadline() -> None:
    morning_due = item(1, context=SurfaceContext.MORNING, due=TODAY)
    result = await build([morning_due], ReviewWindow.EVENING)
    assert result.is_empty


async def test_limit_override_only_reduces_normal_window_limit() -> None:
    service = ResurfacingService(repository=Repository([item(1), item(2), item(3)]))
    result = await service.build_plan(
        request=ReviewRequest(
            window=ReviewWindow.EVENING,
            reference_time=REFERENCE,
            limit_override=1,
        )
    )
    assert len(result.entries) == 1
