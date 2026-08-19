import pytest
from pydantic import ValidationError

from app.models.classification import CaptureType, Confidence, Domain, ShoppingKind
from app.models.queries import DueFilter, QueryPlan, QuerySort


def test_query_plan_accepts_supported_structured_fields() -> None:
    plan = QueryPlan.model_validate(
        {
            "types": ["Task", "Idea"],
            "domains": ["Portfolio", "Tech"],
            "location": "Somerset",
            "keywords": ["chill", "dessert"],
            "shopping_kind": None,
            "due_filter": "ThisWeek",
            "sort": "Oldest",
            "limit": 20,
            "confidence": "High",
        }
    )

    assert plan.types == [CaptureType.TASK, CaptureType.IDEA]
    assert plan.domains == [Domain.PORTFOLIO, Domain.TECH]
    assert plan.due_filter is DueFilter.THIS_WEEK
    assert plan.sort is QuerySort.OLDEST
    assert plan.confidence is Confidence.HIGH


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("types", ["Reminder"]),
        ("domains", ["Food"]),
        ("shopping_kind", "None"),
        ("due_filter", "Tomorrow"),
        ("sort", "Random"),
        ("confidence", "Certain"),
        ("limit", 0),
        ("limit", 21),
    ],
)
def test_query_plan_rejects_invalid_values(field: str, value: object) -> None:
    payload: dict[str, object] = {"confidence": "High", field: value}
    with pytest.raises(ValidationError):
        QueryPlan.model_validate(payload)


def test_query_plan_supports_optional_shopping_kind_and_normalizes_keywords() -> None:
    routine = QueryPlan(
        shopping_kind=ShoppingKind.ROUTINE,
        keywords=["  Coffee   Beans ", "coffee beans"],
        confidence=Confidence.MEDIUM,
    )
    unspecified = QueryPlan(confidence=Confidence.HIGH)

    assert routine.shopping_kind is ShoppingKind.ROUTINE
    assert routine.keywords == ["Coffee Beans"]
    assert unspecified.shopping_kind is None


def test_query_plan_forbids_extra_fields_and_duplicate_enums() -> None:
    with pytest.raises(ValidationError):
        QueryPlan.model_validate({"confidence": "High", "priority": "urgent"})
    with pytest.raises(ValidationError):
        QueryPlan(types=[CaptureType.TASK, CaptureType.TASK], confidence=Confidence.HIGH)
