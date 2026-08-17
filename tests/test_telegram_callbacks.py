from datetime import date

import httpx
import pytest

from app.core.config import Settings
from app.integrations.telegram import TelegramDeliveryError
from app.main import create_app
from app.models.actions import ActionCallback, BrainDumpItem, CallbackAction
from app.models.classification import (
    CaptureType,
    Confidence,
    Domain,
    ShoppingKind,
)
from app.services.telegram_updates import TelegramUpdateService
from tests.conftest import (
    ALLOWED_CHAT_ID,
    ALLOWED_USER_ID,
    FakeCaptureRepository,
    FakeClassifier,
    FakeTelegramClient,
)


def stored_item(
    *,
    type_: CaptureType = CaptureType.TASK,
    domain: Domain = Domain.PERSONAL,
    shopping_kind: ShoppingKind = ShoppingKind.NONE,
) -> BrainDumpItem:
    page_id = "dddddddddddddddddddddddddddddddd"
    return BrainDumpItem(
        page_id=page_id,
        page_url=f"https://www.notion.so/{page_id}",
        title="Bring power bank to work",
        type=type_,
        domain=domain,
        shopping_kind=shopping_kind,
        purchase_focus=False,
        due=date(2026, 8, 18),
        snoozed_until=None,
        confidence=Confidence.HIGH,
    )


def callback_update(
    item: BrainDumpItem,
    action: CallbackAction,
    *,
    user_id: int = ALLOWED_USER_ID,
    chat_id: int = ALLOWED_CHAT_ID,
) -> dict[str, object]:
    return {
        "update_id": 9100,
        "callback_query": {
            "id": "callback-1",
            "from": {"id": user_id},
            "message": {
                "message_id": 500,
                "from": {"id": 999},
                "chat": {"id": chat_id},
                "text": "Saved · Task · Personal\nBring power bank to work",
            },
            "data": ActionCallback(action=action, page_id=item.page_id).encode(),
        },
    }


class FailingCallbackAnswerTelegram(FakeTelegramClient):
    async def answer_callback_query(self, **_: object) -> None:
        raise TelegramDeliveryError("safe callback answer failure")


async def test_done_callback_acknowledges_then_edits_completed_message(
    client: httpx.AsyncClient,
    capture_repository: FakeCaptureRepository,
    telegram_client: FakeTelegramClient,
    webhook_headers: dict[str, str],
) -> None:
    target = stored_item()
    capture_repository.items[target.page_id] = target

    response = await client.post(
        "/webhooks/telegram",
        headers=webhook_headers,
        json=callback_update(target, CallbackAction.DONE),
    )

    assert response.status_code == 200
    assert response.json() == {"status": "acknowledged"}
    assert telegram_client.answered_callbacks == [("callback-1", None, False)]
    assert telegram_client.edited_messages == [
        (ALLOWED_CHAT_ID, 500, "Done\nBring power bank to work", None)
    ]
    assert capture_repository.trashed_ids == [target.page_id]


async def test_callback_acknowledgement_failure_aborts_before_mutation(
    settings: Settings,
    webhook_headers: dict[str, str],
) -> None:
    target = stored_item()
    repository = FakeCaptureRepository()
    repository.items[target.page_id] = target
    application = create_app(
        settings=settings,
        telegram_client=FailingCallbackAnswerTelegram(),
        capture_repository=repository,
        classifier=FakeClassifier(),
    )
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/telegram",
                headers=webhook_headers,
                json=callback_update(target, CallbackAction.DONE),
            )

    assert response.status_code == 502
    assert target.page_id in repository.items


@pytest.mark.parametrize(("field", "wrong_id"), [("from", 987), ("chat", -987)])
async def test_unauthorized_callback_cannot_mutate(
    client: httpx.AsyncClient,
    capture_repository: FakeCaptureRepository,
    telegram_client: FakeTelegramClient,
    webhook_headers: dict[str, str],
    field: str,
    wrong_id: int,
) -> None:
    target = stored_item()
    capture_repository.items[target.page_id] = target
    update = callback_update(target, CallbackAction.DELETE)
    if field == "from":
        update["callback_query"]["from"]["id"] = wrong_id  # type: ignore[index]
    else:
        update["callback_query"]["message"]["chat"]["id"] = wrong_id  # type: ignore[index]

    response = await client.post("/webhooks/telegram", headers=webhook_headers, json=update)

    assert response.status_code == 403
    assert target.page_id in capture_repository.items
    assert telegram_client.answered_callbacks == []


async def test_malformed_and_unknown_actions_are_safe_alerts(
    client: httpx.AsyncClient,
    capture_repository: FakeCaptureRepository,
    telegram_client: FakeTelegramClient,
    webhook_headers: dict[str, str],
) -> None:
    target = stored_item()
    capture_repository.items[target.page_id] = target
    update = callback_update(target, CallbackAction.DELETE)
    update["callback_query"]["data"] = f"bd1:unknown:{target.page_id}"  # type: ignore[index]

    response = await client.post("/webhooks/telegram", headers=webhook_headers, json=update)

    assert response.status_code == 200
    assert target.page_id in capture_repository.items
    assert telegram_client.answered_callbacks == [("callback-1", "Invalid action.", True)]


async def test_stale_and_repeated_delete_are_user_facing_noops(
    client: httpx.AsyncClient,
    capture_repository: FakeCaptureRepository,
    telegram_client: FakeTelegramClient,
    webhook_headers: dict[str, str],
) -> None:
    target = stored_item(type_=CaptureType.IDEA)
    capture_repository.items[target.page_id] = target
    update = callback_update(target, CallbackAction.DELETE)

    first = await client.post("/webhooks/telegram", headers=webhook_headers, json=update)
    second = await client.post("/webhooks/telegram", headers=webhook_headers, json=update)

    assert first.status_code == second.status_code == 200
    assert telegram_client.edited_messages[-1][2:] == ("Item no longer exists.", None)
    assert capture_repository.trashed_ids == [target.page_id]


async def test_snooze_menu_and_back_only_change_the_keyboard(
    client: httpx.AsyncClient,
    capture_repository: FakeCaptureRepository,
    telegram_client: FakeTelegramClient,
    webhook_headers: dict[str, str],
) -> None:
    target = stored_item()
    capture_repository.items[target.page_id] = target

    await client.post(
        "/webhooks/telegram",
        headers=webhook_headers,
        json=callback_update(target, CallbackAction.SNOOZE_MENU),
    )
    snooze_markup = telegram_client.edited_messages[-1][3]
    assert snooze_markup is not None
    assert [[button.text for button in row] for row in snooze_markup.inline_keyboard] == [
        ["Tomorrow", "Next week"],
        ["2 weeks", "1 month"],
        ["Back"],
    ]

    await client.post(
        "/webhooks/telegram",
        headers=webhook_headers,
        json=callback_update(target, CallbackAction.BACK),
    )
    assert telegram_client.edited_messages[-1][2].startswith("Saved · Task · Personal")
    assert capture_repository.items[target.page_id].snoozed_until is None


async def test_action_that_no_longer_matches_type_refreshes_buttons_without_mutation(
    client: httpx.AsyncClient,
    capture_repository: FakeCaptureRepository,
    telegram_client: FakeTelegramClient,
    webhook_headers: dict[str, str],
) -> None:
    target = stored_item(
        domain=Domain.SHOPPING,
        shopping_kind=ShoppingKind.ROUTINE,
    )
    capture_repository.items[target.page_id] = target

    response = await client.post(
        "/webhooks/telegram",
        headers=webhook_headers,
        json=callback_update(target, CallbackAction.FOCUS),
    )

    assert response.status_code == 200
    assert telegram_client.edited_messages[-1][2] == (
        "Action unavailable\nBring power bank to work"
    )
    assert target.page_id in capture_repository.items


async def test_notion_failure_is_neutral_and_leaves_original_message_usable(
    client: httpx.AsyncClient,
    capture_repository: FakeCaptureRepository,
    telegram_client: FakeTelegramClient,
    webhook_headers: dict[str, str],
) -> None:
    target = stored_item()
    capture_repository.items[target.page_id] = target
    capture_repository.fail_item_operations = True

    response = await client.post(
        "/webhooks/telegram",
        headers=webhook_headers,
        json=callback_update(target, CallbackAction.DONE),
    )

    assert response.status_code == 200
    assert target.page_id in capture_repository.items
    assert telegram_client.edited_messages == []
    assert telegram_client.sent_messages == [
        (ALLOWED_CHAT_ID, "Couldn't update that item. Please try again.")
    ]


@pytest.mark.parametrize(
    ("target", "expected_rows"),
    [
        (stored_item(), [["Done", "Snooze"], ["Keep", "Delete"], ["Open"]]),
        (
            stored_item(
                domain=Domain.SHOPPING,
                shopping_kind=ShoppingKind.ROUTINE,
            ),
            [["Bought", "Snooze"], ["Delete"], ["Open"]],
        ),
        (
            stored_item(
                domain=Domain.SHOPPING,
                shopping_kind=ShoppingKind.PLANNED,
            ),
            [["Focus", "Bought"], ["Keep", "Delete"], ["Open"]],
        ),
        (
            stored_item(type_=CaptureType.IDEA),
            [["Keep", "Delete"], ["Open"]],
        ),
        (
            stored_item(type_=CaptureType.REFERENCE),
            [["Delete"], ["Open"]],
        ),
    ],
)
def test_item_type_selects_compact_keyboard_with_direct_open_url(
    target: BrainDumpItem,
    expected_rows: list[list[str]],
) -> None:
    markup = TelegramUpdateService._action_keyboard(target)

    assert [[button.text for button in row] for row in markup.inline_keyboard] == expected_rows
    open_button = markup.inline_keyboard[-1][0]
    assert open_button.url == target.page_url
    assert open_button.callback_data is None
