from collections.abc import Mapping
from datetime import date
from typing import Any

import pytest

from app.models.reviews import ReviewCandidateCriteria, ReviewWindow
from app.repositories.notion import (
    EXPECTED_PROPERTY_TYPES,
    EXPECTED_SELECT_OPTIONS,
    NotionCaptureRepository,
)
from app.repositories.reviews import ReviewPersistenceError
from tests.conftest import NOTION_DATA_SOURCE_ID, NOTION_DATABASE_ID


def stored_page(page: int, *, trashed: bool = False) -> dict[str, Any]:
    page_id = f"{page:032x}"
    return {
        "id": page_id,
        "url": f"https://www.notion.so/{page_id}",
        "parent": {"type": "data_source_id", "data_source_id": NOTION_DATA_SOURCE_ID},
        "in_trash": trashed,
        "created_time": "2026-07-01T04:00:00.000Z",
        "properties": {
            "Title": {"title": [{"plain_text": f"Review item {page}"}]},
            "Type": {"select": {"name": "Task"}},
            "Domain": {"select": {"name": "Personal"}},
            "Location": {"rich_text": [{"plain_text": "Orchard"}]},
            "Due": {"date": {"start": "2026-08-20"}},
            "Created": {"created_time": "2026-07-01T04:00:00.000Z"},
            "OriginalInput": {"rich_text": []},
            "SurfaceContext": {"select": {"name": "Evening"}},
            "ShoppingKind": {"select": None},
            "PurchaseFocus": {"checkbox": False},
            "LastSurfaced": {"date": {"start": "2026-08-12"}},
            "SnoozedUntil": {"date": None},
            "Confidence": {"select": {"name": "High"}},
        },
    }


class ReviewGateway:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def retrieve_database(self, *, database_id: str) -> Mapping[str, Any]:
        return {
            "id": database_id,
            "title": [{"plain_text": "Brain Dump v2"}],
            "data_sources": [{"id": NOTION_DATA_SOURCE_ID}],
        }

    async def retrieve_data_source(self, *, data_source_id: str) -> Mapping[str, Any]:
        properties: dict[str, Any] = {}
        for name, property_type in EXPECTED_PROPERTY_TYPES.items():
            value: dict[str, Any] = {"type": property_type, property_type: {}}
            if name in EXPECTED_SELECT_OPTIONS:
                value["select"] = {
                    "options": [
                        {"name": option} for option in sorted(EXPECTED_SELECT_OPTIONS[name])
                    ]
                }
            properties[name] = value
        return {
            "id": data_source_id,
            "parent": {"database_id": NOTION_DATABASE_ID},
            "properties": properties,
        }

    async def query_data_source(
        self,
        *,
        data_source_id: str,
        filter_: Mapping[str, Any] | None,
        page_size: int,
        start_cursor: str | None = None,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "data_source_id": data_source_id,
                "filter": filter_,
                "page_size": page_size,
                "start_cursor": start_cursor,
            }
        )
        return self.responses[len(self.calls) - 1]


def repository(gateway: ReviewGateway) -> NotionCaptureRepository:
    return NotionCaptureRepository(
        gateway=gateway,  # type: ignore[arg-type]
        database_id=NOTION_DATABASE_ID,
        data_source_id=NOTION_DATA_SOURCE_ID,
    )


async def test_review_query_paginates_projects_fields_and_excludes_trash() -> None:
    gateway = ReviewGateway(
        [
            {
                "results": [stored_page(1), stored_page(9, trashed=True)],
                "has_more": True,
                "next_cursor": "review-next",
            },
            {"results": [stored_page(2)], "has_more": False},
        ]
    )
    items = await repository(gateway).list_candidates(
        criteria=ReviewCandidateCriteria(
            window=ReviewWindow.EVENING,
            reference_date=date(2026, 8, 20),
        )
    )
    assert [value.page_id for value in items] == [f"{1:032x}", f"{2:032x}"]
    assert items[0].surface_context.value == "Evening"
    assert items[0].last_surfaced == date(2026, 8, 12)
    assert items[0].location == "Orchard"
    assert [call["start_cursor"] for call in gateway.calls] == [None, "review-next"]
    assert all(call["page_size"] == 100 for call in gateway.calls)


@pytest.mark.parametrize(
    ("window", "expected_window_filter"),
    [
        (
            ReviewWindow.MORNING,
            {
                "and": [
                    {"property": "Type", "select": {"equals": "Task"}},
                    {"property": "SurfaceContext", "select": {"equals": "Morning"}},
                ]
            },
        ),
        (
            ReviewWindow.AFTER_WORK,
            {
                "or": [
                    {
                        "and": [
                            {"property": "Type", "select": {"equals": "Task"}},
                            {
                                "property": "SurfaceContext",
                                "select": {"equals": "AfterWork"},
                            },
                        ]
                    },
                    {
                        "and": [
                            {"property": "Domain", "select": {"equals": "Shopping"}},
                            {
                                "property": "ShoppingKind",
                                "select": {"equals": "Routine"},
                            },
                        ]
                    },
                ]
            },
        ),
        (
            ReviewWindow.EVENING,
            {
                "or": [
                    {"property": "SurfaceContext", "select": {"equals": "Evening"}},
                    {"property": "SurfaceContext", "select": {"equals": "Anytime"}},
                ]
            },
        ),
        (
            ReviewWindow.WEEKEND,
            {
                "or": [
                    {"property": "SurfaceContext", "select": {"equals": "Weekend"}},
                    {"property": "SurfaceContext", "select": {"equals": "Anytime"}},
                ]
            },
        ),
    ],
)
def test_review_filters_are_structured_and_snooze_aware(
    window: ReviewWindow,
    expected_window_filter: dict[str, Any],
) -> None:
    criteria = ReviewCandidateCriteria(window=window, reference_date=date(2026, 8, 20))
    assert NotionCaptureRepository._review_filter(criteria) == {
        "and": [
            expected_window_filter,
            {
                "or": [
                    {"property": "SnoozedUntil", "date": {"is_empty": True}},
                    {
                        "property": "SnoozedUntil",
                        "date": {"on_or_before": "2026-08-20"},
                    },
                ]
            },
        ]
    }


async def test_review_rejects_invalid_pagination_and_non_v2_parent() -> None:
    invalid_cursor = ReviewGateway([{"results": [], "has_more": True, "next_cursor": None}])
    with pytest.raises(ReviewPersistenceError):
        await repository(invalid_cursor).list_candidates(
            criteria=ReviewCandidateCriteria(
                window=ReviewWindow.MORNING,
                reference_date=date(2026, 8, 20),
            )
        )

    page = stored_page(1)
    page["parent"]["data_source_id"] = "old-brain-dump"
    outside = ReviewGateway([{"results": [page], "has_more": False}])
    with pytest.raises(ReviewPersistenceError, match="outside Brain Dump v2"):
        await repository(outside).list_candidates(
            criteria=ReviewCandidateCriteria(
                window=ReviewWindow.EVENING,
                reference_date=date(2026, 8, 20),
            )
        )
