from app.models.classification import CaptureType, Domain
from app.models.queries import DueFilter, QuerySort
from app.tools.evaluate_queries import load_cases


def test_manual_query_evaluation_fixture_is_valid_and_complete() -> None:
    cases = load_cases()
    inputs = {case.input for case in cases}

    assert len(cases) == 14
    assert "show portfolio ideas" in inputs
    assert "what do I need to do today" in inputs
    assert "what groceries do I need" in inputs
    assert "where to chill and have dessert at Somerset" in inputs
    assert all(case.reference_datetime.utcoffset() is not None for case in cases)


def test_evaluation_fixture_covers_key_structured_interpretations() -> None:
    cases = {case.name: case.expected for case in load_cases()}

    assert cases["portfolio_ideas"].types == [CaptureType.IDEA]
    assert cases["portfolio_ideas"].domains == [Domain.PORTFOLIO]
    assert cases["old_portfolio_ideas"].sort is QuerySort.OLDEST
    assert cases["tasks_today"].due_filter is DueFilter.TODAY
    assert cases["somerset_dessert"].location == "Somerset"
    assert cases["somerset_dessert"].keywords == ["chill", "dessert"]
