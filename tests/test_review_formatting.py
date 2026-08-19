from datetime import UTC, date, datetime

import pytest

from app.models.classification import CaptureType, Domain, ShoppingKind
from app.models.reviews import DeadlineUrgency, ReviewEntry, ReviewPlan, ReviewWindow
from app.services.review_delivery import format_action_item, format_review
from tests.test_review_delivery import item


@pytest.mark.parametrize(
    ("window", "heading"),
    [
        (ReviewWindow.MORNING, "Morning Brain Dump"),
        (ReviewWindow.AFTER_WORK, "After work"),
        (ReviewWindow.EVENING, "Worth revisiting tonight"),
        (ReviewWindow.WEEKEND, "Weekend Brain Dump"),
    ],
)
def test_review_window_headings(window: ReviewWindow, heading: str) -> None:
    task = item(1)
    plan = ReviewPlan(
        window=window,
        generated_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
        entries=(ReviewEntry(item=task, score=50, urgency=DeadlineUrgency.NONE),),
    )
    assert format_review(plan).startswith(heading)


@pytest.mark.parametrize(
    ("due", "urgency", "expected"),
    [
        (date(2026, 8, 18), DeadlineUrgency.OVERDUE, "Overdue · 18 Aug"),
        (date(2026, 8, 19), DeadlineUrgency.TODAY, "Due today"),
        (date(2026, 8, 20), DeadlineUrgency.STRONG, "Due tomorrow"),
        (date(2026, 8, 25), DeadlineUrgency.MODERATE, "Due 25 Aug"),
    ],
)
def test_task_deadline_formatting(
    due: date, urgency: DeadlineUrgency, expected: str
) -> None:
    task = item(1, due=due)
    plan = ReviewPlan(
        window=ReviewWindow.EVENING,
        generated_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
        entries=(ReviewEntry(item=task, score=50, urgency=urgency),),
    )
    assert expected in format_review(plan)


def test_planned_purchase_and_place_metadata() -> None:
    purchase = item(
        1,
        title="New monitor",
        type_=CaptureType.IDEA,
        domain=Domain.SHOPPING,
        shopping_kind=ShoppingKind.PLANNED,
        focused=True,
    )
    place = item(
        2,
        title="Dessert cafe",
        type_=CaptureType.IDEA,
        domain=Domain.PLACES,
        location="Somerset",
    )
    plan = ReviewPlan(
        window=ReviewWindow.WEEKEND,
        generated_at=datetime(2026, 8, 22, 12, tzinfo=UTC),
        entries=(
            ReviewEntry(item=purchase, score=50, urgency=DeadlineUrgency.NONE),
            ReviewEntry(item=place, score=40, urgency=DeadlineUrgency.NONE),
        ),
    )
    text = format_review(plan)
    assert "Focused planned purchase" in text
    assert "Somerset" in text
    assert "Focused planned purchase" in format_action_item(1, purchase)


def test_long_titles_are_compact_and_unicode_safe() -> None:
    thought = item(
        1,
        title="  café   " + "idea " * 100,
        type_=CaptureType.THOUGHT,
        domain=Domain.PERSONAL,
    )
    text = format_action_item(1, thought)
    assert "  " not in text
    assert "café" in text
    assert "…" in text
