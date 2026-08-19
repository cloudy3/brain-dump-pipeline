import pytest

from app.models.classification import CaptureType, Domain, ShoppingKind
from app.models.queries import DueFilter
from app.services.queries import detect_query_request


@pytest.mark.parametrize(
    "text",
    [
        "show portfolio ideas",
        "where to chill at Somerset",
        "what do I need to do today",
        "find my travel ideas",
        "list my tasks",
        "do I have ramen places",
        "which purchases are planned",
        "search saved interview questions",
        "give me my date ideas",
        "what groceries do I need",
        "tasks this week",
    ],
)
def test_obvious_retrieval_messages_are_query_candidates(text: str) -> None:
    request = detect_query_request(text)
    assert request is not None
    assert request.text == " ".join(text.split())


@pytest.mark.parametrize(
    "text",
    [
        "add dark mode to my portfolio",
        "maybe buy a monitor",
        "I should find somewhere to eat sometime",
        "Things I should buy eventually",
        "Remember Orchard dessert places",
        "findability matters in this design",
    ],
)
def test_capture_and_ambiguous_messages_are_not_query_candidates(text: str) -> None:
    assert detect_query_request(text) is None


def test_forced_query_command_preserves_query_text() -> None:
    request = detect_query_request("/query@brain_dump_bot Things I should buy eventually")
    assert request is not None
    assert request.text == "Things I should buy eventually"


@pytest.mark.parametrize(
    ("text", "types", "domains", "shopping_kind", "due_filter"),
    [
        ("Today", [CaptureType.TASK], [], None, DueFilter.TODAY),
        ("/tasks", [CaptureType.TASK], [], None, DueFilter.ANY),
        ("/ideas@brain_dump_bot", [CaptureType.IDEA], [], None, DueFilter.ANY),
        ("Portfolio", [], [Domain.PORTFOLIO], None, DueFilter.ANY),
        ("Shopping", [], [Domain.SHOPPING], None, DueFilter.ANY),
        (
            "/planned_purchases",
            [],
            [Domain.SHOPPING],
            ShoppingKind.PLANNED,
            DueFilter.ANY,
        ),
        ("Places", [], [Domain.PLACES], None, DueFilter.ANY),
        ("Date Ideas", [], [Domain.DATING], None, DueFilter.ANY),
        ("Travel", [], [Domain.TRAVEL], None, DueFilter.ANY),
    ],
)
def test_shortcuts_build_deterministic_plans(
    text: str,
    types: list[CaptureType],
    domains: list[Domain],
    shopping_kind: ShoppingKind | None,
    due_filter: DueFilter,
) -> None:
    request = detect_query_request(text)
    assert request is not None and request.plan is not None
    assert request.plan.types == types
    assert request.plan.domains == domains
    assert request.plan.shopping_kind is shopping_kind
    assert request.plan.due_filter is due_filter


def test_unknown_telegram_command_is_not_persisted_as_capture() -> None:
    request = detect_query_request("/surprise")
    assert request is not None
    assert request.text == ""
