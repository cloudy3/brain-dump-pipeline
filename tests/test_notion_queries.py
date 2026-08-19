from collections.abc import Mapping
from datetime import date
from typing import Any

import pytest

from app.models.classification import CaptureType, Domain, ShoppingKind
from app.models.queries import DueFilter, QueryCriteria
from app.repositories.notion import (
    EXPECTED_PROPERTY_TYPES,
    EXPECTED_SELECT_OPTIONS,
    NotionCaptureRepository,
)
from app.repositories.queries import QueryPersistenceError
from tests.conftest import NOTION_DATA_SOURCE_ID, NOTION_DATABASE_ID


def stored_page(page: int, *, trashed: bool = False) -> dict[str, Any]:
    page_id = f"{page:032x}"
    return {
        "id": page_id,
        "url": f"https://www.notion.so/{page_id}",
        "parent": {"type": "data_source_id", "data_source_id": NOTION_DATA_SOURCE_ID},
        "in_trash": trashed,
        "created_time": "2026-08-12T04:00:00.000Z",
        "properties": {
            "Title": {"title": [{"plain_text": "Somerset dessert cafe"}]},
            "Type": {"select": {"name": "Reference"}},
            "Domain": {"select": {"name": "Places"}},
            "Location": {"rich_text": [{"plain_text": "Somerset"}]},
            "Due": {"date": None},
            "Created": {"created_time": "2026-08-12T04:00:00.000Z"},
            "OriginalInput": {"rich_text": [{"plain_text": "Quiet chill dessert cafe"}]},
            "ShoppingKind": {"select": None},
            "PurchaseFocus": {"checkbox": False},
            "SnoozedUntil": {"date": {"start": "2026-09-01"}},
            "Confidence": {"select": {"name": "High"}},
        },
    }


class QueryGateway:
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


def repository_for(gateway: QueryGateway) -> NotionCaptureRepository:
    return NotionCaptureRepository(
        gateway=gateway,  # type: ignore[arg-type]
        database_id=NOTION_DATABASE_ID,
        data_source_id=NOTION_DATA_SOURCE_ID,
    )


async def test_search_builds_compound_filter_paginates_and_excludes_trash() -> None:
    gateway = QueryGateway(
        [
            {
                "results": [stored_page(1), stored_page(9, trashed=True)],
                "has_more": True,
                "next_cursor": "next-1",
            },
            {"results": [stored_page(2)], "has_more": False, "next_cursor": None},
        ]
    )
    repository = repository_for(gateway)
    criteria = QueryCriteria(
        types=(CaptureType.REFERENCE, CaptureType.IDEA),
        domains=(Domain.PLACES, Domain.DATING),
        location="Somerset",
        shopping_kind=ShoppingKind.PLANNED,
        due_filter=DueFilter.THIS_WEEK,
        reference_date=date(2026, 8, 19),
    )

    items = await repository.search(criteria=criteria)

    assert [item.page_id for item in items] == [f"{1:032x}", f"{2:032x}"]
    assert items[0].location == "Somerset"
    assert items[0].original_input == "Quiet chill dessert cafe"
    assert items[0].snoozed_until == date(2026, 9, 1)
    assert [call["start_cursor"] for call in gateway.calls] == [None, "next-1"]
    assert all(call["page_size"] == 100 for call in gateway.calls)
    assert gateway.calls[0]["filter"] == {
        "and": [
            {
                "or": [
                    {"property": "Type", "select": {"equals": "Reference"}},
                    {"property": "Type", "select": {"equals": "Idea"}},
                ]
            },
            {
                "or": [
                    {"property": "Domain", "select": {"equals": "Places"}},
                    {"property": "Domain", "select": {"equals": "Dating"}},
                ]
            },
            {"property": "ShoppingKind", "select": {"equals": "Planned"}},
            {
                "or": [
                    {"property": "Location", "rich_text": {"contains": "Somerset"}},
                    {"property": "Title", "title": {"contains": "Somerset"}},
                    {
                        "property": "OriginalInput",
                        "rich_text": {"contains": "Somerset"},
                    },
                ]
            },
            {
                "and": [
                    {"property": "Due", "date": {"on_or_after": "2026-08-17"}},
                    {"property": "Due", "date": {"on_or_before": "2026-08-23"}},
                ]
            },
        ]
    }


@pytest.mark.parametrize(
    ("due_filter", "expected"),
    [
        (DueFilter.ANY, None),
        (DueFilter.TODAY, {"property": "Due", "date": {"equals": "2026-12-31"}}),
        (DueFilter.OVERDUE, {"property": "Due", "date": {"before": "2026-12-31"}}),
        (DueFilter.UPCOMING, {"property": "Due", "date": {"after": "2026-12-31"}}),
        (DueFilter.NO_DUE_DATE, {"property": "Due", "date": {"is_empty": True}}),
        (
            DueFilter.THIS_WEEK,
            {
                "and": [
                    {"property": "Due", "date": {"on_or_after": "2026-12-28"}},
                    {"property": "Due", "date": {"on_or_before": "2027-01-03"}},
                ]
            },
        ),
    ],
)
def test_due_filters_use_fixed_singapore_calendar_boundaries(
    due_filter: DueFilter,
    expected: dict[str, Any] | None,
) -> None:
    criteria = QueryCriteria(reference_date=date(2026, 12, 31), due_filter=due_filter)
    assert NotionCaptureRepository._due_filter(criteria) == expected


async def test_unstructured_search_omits_notion_filter() -> None:
    gateway = QueryGateway([{"results": [], "has_more": False}])
    repository = repository_for(gateway)

    assert await repository.search(criteria=QueryCriteria(reference_date=date(2026, 8, 19))) == []
    assert gateway.calls[0]["filter"] is None


async def test_invalid_pagination_is_a_query_persistence_error() -> None:
    gateway = QueryGateway([{"results": [], "has_more": True, "next_cursor": None}])
    repository = repository_for(gateway)

    with pytest.raises(QueryPersistenceError):
        await repository.search(criteria=QueryCriteria(reference_date=date(2026, 8, 19)))


async def test_query_rejects_page_outside_configured_v2_data_source() -> None:
    page = stored_page(1)
    page["parent"]["data_source_id"] = "old-brain-dump-source"
    gateway = QueryGateway([{"results": [page], "has_more": False}])
    repository = repository_for(gateway)

    with pytest.raises(QueryPersistenceError, match="outside Brain Dump v2"):
        await repository.search(criteria=QueryCriteria(reference_date=date(2026, 8, 19)))
