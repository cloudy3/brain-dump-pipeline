from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.models.classification import (
    CaptureType,
    Confidence,
    Domain,
    ShoppingKind,
    SurfaceContext,
)
from app.models.reviews import (
    ReviewItem,
    ReviewPlan,
    ReviewRequest,
    ReviewWindow,
    RoutineShoppingGroup,
)
from app.services.item_views import ItemActionViewBuilder


def review_item(*, routine: bool = False) -> ReviewItem:
    return ReviewItem(
        page_id="1" * 32,
        page_url=f"https://www.notion.so/{'1' * 32}",
        title="Milk" if routine else "Pack passport",
        type=CaptureType.TASK,
        domain=Domain.SHOPPING if routine else Domain.PERSONAL,
        shopping_kind=ShoppingKind.ROUTINE if routine else ShoppingKind.NONE,
        purchase_focus=False,
        due=None,
        snoozed_until=None,
        confidence=Confidence.HIGH,
        surface_context=(SurfaceContext.AFTER_WORK if routine else SurfaceContext.MORNING),
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        last_surfaced=None,
        location=None,
    )


def test_review_request_requires_an_aware_reference_and_bounded_override() -> None:
    request = ReviewRequest(
        window=ReviewWindow.MORNING,
        reference_time=datetime(2026, 8, 19, 8, tzinfo=UTC),
        limit_override=2,
    )
    assert request.limit_override == 2
    with pytest.raises(ValidationError):
        ReviewRequest(window=ReviewWindow.MORNING, reference_time=datetime(2026, 8, 19, 8))
    with pytest.raises(ValidationError):
        ReviewRequest(
            window=ReviewWindow.MORNING,
            reference_time=datetime(2026, 8, 19, 8, tzinfo=UTC),
            limit_override=9,
        )


def test_empty_review_is_valid_and_explicit() -> None:
    plan = ReviewPlan(
        window=ReviewWindow.EVENING,
        generated_at=datetime(2026, 8, 19, 11, tzinfo=UTC),
    )
    assert plan.is_empty is True


def test_routine_group_reports_overflow_and_is_after_work_only() -> None:
    item = review_item(routine=True)
    group = RoutineShoppingGroup(items=(item,), total_eligible_count=3)
    assert group.additional_count == 2
    plan = ReviewPlan(
        window=ReviewWindow.AFTER_WORK,
        generated_at=datetime(2026, 8, 19, 10, tzinfo=UTC),
        routine_shopping=group,
    )
    assert plan.is_empty is False
    with pytest.raises(ValidationError):
        ReviewPlan(
            window=ReviewWindow.MORNING,
            generated_at=datetime(2026, 8, 19, 10, tzinfo=UTC),
            routine_shopping=group,
        )


def test_review_item_retains_phase_four_action_fields() -> None:
    item = review_item()
    assert item.page_id == "1" * 32
    assert item.snoozed_until is None
    assert item.last_surfaced is None
    assert item.created_at.date() == date(2026, 7, 1)
    keyboard = ItemActionViewBuilder.action_keyboard(item)
    assert [[button.text for button in row] for row in keyboard.inline_keyboard] == [
        ["Done", "Snooze"],
        ["Keep", "Delete"],
        ["Open"],
    ]
