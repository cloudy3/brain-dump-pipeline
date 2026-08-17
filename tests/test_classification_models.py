from datetime import date

import pytest
from pydantic import ValidationError

from app.models.classification import (
    CaptureClassification,
    CaptureType,
    Confidence,
    Domain,
    ShoppingKind,
    SurfaceContext,
)


def _valid_data() -> dict[str, object]:
    return {
        "title": "Bring power bank to work",
        "type": "Task",
        "domain": "Personal",
        "location": None,
        "due": "2026-08-17",
        "surface_context": "Morning",
        "shopping_kind": "None",
        "confidence": "High",
    }


def test_valid_classification_is_typed() -> None:
    classification = CaptureClassification.model_validate(_valid_data())

    assert classification.type is CaptureType.TASK
    assert classification.domain is Domain.PERSONAL
    assert classification.due == date(2026, 8, 17)
    assert classification.surface_context is SurfaceContext.MORNING
    assert classification.shopping_kind is ShoppingKind.NONE
    assert classification.confidence is Confidence.HIGH


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "Reminder"),
        ("domain", "Finance"),
        ("surface_context", "Lunch"),
        ("shopping_kind", "Maybe"),
        ("confidence", "0.87"),
    ],
)
def test_invalid_enum_value_is_rejected(field: str, value: str) -> None:
    data = _valid_data()
    data[field] = value

    with pytest.raises(ValidationError):
        CaptureClassification.model_validate(data)


@pytest.mark.parametrize(
    "updates",
    [
        {"domain": "Shopping", "shopping_kind": "None"},
        {"domain": "Personal", "shopping_kind": "Routine"},
        {
            "domain": "Shopping",
            "shopping_kind": "Routine",
            "surface_context": "Evening",
        },
        {
            "domain": "Shopping",
            "shopping_kind": "Planned",
            "surface_context": "AfterWork",
        },
        {"location": "   "},
    ],
)
def test_inconsistent_classification_is_rejected(updates: dict[str, object]) -> None:
    data = _valid_data() | updates

    with pytest.raises(ValidationError):
        CaptureClassification.model_validate(data)


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CaptureClassification.model_validate(_valid_data() | {"priority": "High"})
