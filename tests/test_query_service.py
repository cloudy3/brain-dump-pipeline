from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.classification import CaptureType, Confidence, Domain, ShoppingKind
from app.models.queries import DueFilter, QueryCriteria, QueryItem, QueryPlan, QuerySort
from app.services.queries import (
    ManualQueryService,
    QueryInterpretationError,
    QueryRequest,
    normalize_search_text,
    rank_query_items,
)

REFERENCE = datetime(2026, 8, 19, 10, 0, tzinfo=ZoneInfo("Asia/Singapore"))


def query_item(
    title: str,
    *,
    page: int,
    location: str | None = None,
    original: str = "",
    created_day: int = 1,
    due: date | None = None,
    snoozed_until: date | None = None,
) -> QueryItem:
    page_id = f"{page:032x}"
    return QueryItem(
        page_id=page_id,
        page_url=f"https://www.notion.so/{page_id}",
        title=title,
        type=CaptureType.REFERENCE,
        domain=Domain.PLACES,
        shopping_kind=ShoppingKind.NONE,
        purchase_focus=False,
        due=due,
        snoozed_until=snoozed_until,
        confidence=Confidence.HIGH,
        location=location,
        original_input=original,
        created_at=datetime(2026, 8, created_day, tzinfo=ZoneInfo("Asia/Singapore")),
    )


class FakeInterpreter:
    def __init__(self, plan: QueryPlan | None = None, error: Exception | None = None) -> None:
        self.plan = plan or QueryPlan(confidence=Confidence.HIGH)
        self.error = error
        self.calls: list[tuple[str, datetime]] = []

    async def interpret_query(
        self, *, original_input: str, reference_datetime: datetime
    ) -> QueryPlan:
        self.calls.append((original_input, reference_datetime))
        if self.error:
            raise self.error
        return self.plan


class FakeQueryRepository:
    def __init__(self, items: list[QueryItem] | None = None) -> None:
        self.items = items or []
        self.criteria: list[QueryCriteria] = []

    async def search(self, *, criteria: QueryCriteria) -> list[QueryItem]:
        self.criteria.append(criteria)
        return self.items


async def test_service_interprets_natural_query_and_builds_singapore_criteria() -> None:
    plan = QueryPlan(
        types=[CaptureType.TASK],
        domains=[Domain.PORTFOLIO],
        keywords=["diagram"],
        due_filter=DueFilter.THIS_WEEK,
        confidence=Confidence.HIGH,
    )
    interpreter = FakeInterpreter(plan)
    repository = FakeQueryRepository()
    service = ManualQueryService(interpreter=interpreter, repository=repository)

    result = await service.execute(
        request=QueryRequest(text="show portfolio tasks this week"),
        reference_datetime=REFERENCE,
    )

    assert result.plan == plan
    assert interpreter.calls == [("show portfolio tasks this week", REFERENCE)]
    assert repository.criteria == [
        QueryCriteria(
            types=(CaptureType.TASK,),
            domains=(Domain.PORTFOLIO,),
            due_filter=DueFilter.THIS_WEEK,
            reference_date=date(2026, 8, 19),
        )
    ]


async def test_shortcut_bypasses_interpreter_and_snoozed_item_remains_visible() -> None:
    item = query_item(
        "Submit report",
        page=1,
        due=date(2026, 8, 19),
        snoozed_until=date(2026, 9, 1),
    )
    interpreter = FakeInterpreter(error=TimeoutError())
    repository = FakeQueryRepository([item])
    service = ManualQueryService(interpreter=interpreter, repository=repository)
    plan = QueryPlan(
        types=[CaptureType.TASK],
        due_filter=DueFilter.TODAY,
        confidence=Confidence.HIGH,
    )

    result = await service.execute(
        request=QueryRequest(plan=plan, label="Tasks due today"),
        reference_datetime=REFERENCE,
    )

    assert result.items == [item]
    assert interpreter.calls == []
    assert repository.criteria[0].model_dump().keys() == {
        "types",
        "domains",
        "location",
        "shopping_kind",
        "due_filter",
        "reference_date",
    }


@pytest.mark.parametrize(
    "interpreter",
    [
        FakeInterpreter(error=TimeoutError()),
        FakeInterpreter(QueryPlan(confidence=Confidence.LOW)),
    ],
)
async def test_interpretation_failure_does_not_query_repository(
    interpreter: FakeInterpreter,
) -> None:
    repository = FakeQueryRepository()
    service = ManualQueryService(interpreter=interpreter, repository=repository)

    with pytest.raises(QueryInterpretationError):
        await service.execute(request=QueryRequest(text="show ideas"), reference_datetime=REFERENCE)

    assert repository.criteria == []


def test_unicode_normalization_is_case_insensitive_and_whitespace_safe() -> None:
    assert normalize_search_text("  ＲＡＭＥＮ\nPlace ") == "ramen place"


def test_relevance_prefers_more_keywords_then_title_location_and_original() -> None:
    items = [
        query_item("Cafe", page=1, location="Somerset", original="dessert chill"),
        query_item("Dessert chill cafe", page=2, location="Somerset"),
        query_item("Dessert shop", page=3, location="Somerset", original="quiet"),
        query_item("Chill space", page=4, location="Orchard", original="dessert"),
    ]
    plan = QueryPlan(
        location="somerset",
        keywords=["dessert", "chill"],
        confidence=Confidence.HIGH,
    )

    ranked = rank_query_items(items, plan)

    assert [item.page_id for item in ranked] == [f"{2:032x}", f"{1:032x}", f"{3:032x}"]


def test_no_keyword_match_is_removed_and_ties_are_stable() -> None:
    items = [
        query_item("Ramen", page=2, created_day=2),
        query_item("RAMEN", page=1, created_day=2),
        query_item("Dessert", page=3, created_day=3),
    ]
    plan = QueryPlan(keywords=["ramen"], confidence=Confidence.HIGH)

    assert [item.page_id for item in rank_query_items(items, plan)] == [f"{1:032x}", f"{2:032x}"]


def test_title_location_and_original_matches_use_documented_priority() -> None:
    items = [
        query_item("Ramen guide", page=1, created_day=1),
        query_item("Dinner guide", page=2, location="Ramen", created_day=1),
        query_item("Food guide", page=3, original="Try the ramen", created_day=1),
    ]
    plan = QueryPlan(keywords=["ramen"], confidence=Confidence.HIGH)

    assert [item.page_id for item in rank_query_items(items, plan)] == [
        f"{1:032x}",
        f"{2:032x}",
        f"{3:032x}",
    ]


@pytest.mark.parametrize(
    ("sort", "expected_pages"),
    [
        (QuerySort.NEWEST, [2, 1]),
        (QuerySort.OLDEST, [1, 2]),
        (QuerySort.DUE_SOON, [2, 1]),
    ],
)
def test_controlled_sorts_are_deterministic(sort: QuerySort, expected_pages: list[int]) -> None:
    items = [
        query_item("First", page=1, created_day=1),
        query_item("Second", page=2, created_day=2, due=date(2026, 8, 20)),
    ]
    plan = QueryPlan(sort=sort, confidence=Confidence.HIGH)

    assert [item.page_id for item in rank_query_items(items, plan)] == [
        f"{page:032x}" for page in expected_pages
    ]
