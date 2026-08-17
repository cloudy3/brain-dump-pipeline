import pytest
from pydantic import ValidationError

from app.models.actions import (
    TELEGRAM_CALLBACK_DATA_LIMIT,
    ActionCallback,
    CallbackAction,
)
from app.models.telegram import InlineKeyboardButton

PAGE_ID = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize("action", list(CallbackAction))
def test_callback_round_trip_is_compact(action: CallbackAction) -> None:
    callback = ActionCallback(action=action, page_id=PAGE_ID)

    encoded = callback.encode()

    assert len(encoded.encode()) <= TELEGRAM_CALLBACK_DATA_LIMIT
    assert ActionCallback.decode(encoded) == callback


@pytest.mark.parametrize(
    "payload",
    [
        "",
        f"wrong:d:{PAGE_ID}",
        f"bd1:unknown:{PAGE_ID}",
        "bd1:d:not-a-page",
        f"bd1:d:{PAGE_ID}:extra",
    ],
)
def test_malformed_callback_is_rejected(payload: str) -> None:
    with pytest.raises(ValueError):
        ActionCallback.decode(payload)


def test_callback_normalizes_hyphenated_page_id() -> None:
    callback = ActionCallback.decode("bd1:d:01234567-89ab-cdef-0123-456789abcdef")

    assert callback.page_id == PAGE_ID


@pytest.mark.parametrize(
    "kwargs",
    [
        {"text": "Broken"},
        {"text": "Broken", "url": "https://notion.so/x", "callback_data": "x"},
    ],
)
def test_inline_button_requires_exactly_one_action(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        InlineKeyboardButton(**kwargs)
