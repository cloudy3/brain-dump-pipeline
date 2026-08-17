from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.actions import (
    ActionCallback,
    ActionPolicy,
    BrainDumpItem,
    CallbackAction,
)
from app.models.classification import (
    CaptureType,
    Confidence,
    Domain,
    ShoppingKind,
)
from app.repositories.items import ItemPersistenceError
from app.services.item_actions import ActionResultStatus, ItemActionService

SGT = ZoneInfo("Asia/Singapore")
REFERENCE = datetime(2026, 8, 31, 23, 30, tzinfo=SGT)


class FakeItemRepository:
    def __init__(self, *items: BrainDumpItem) -> None:
        self.items = {item.page_id: item for item in items}
        self.operations: list[tuple[object, ...]] = []
        self.fail_operation: tuple[str, str] | None = None

    async def get_by_id(self, *, page_id: str) -> BrainDumpItem | None:
        self._fail("get", page_id)
        return self.items.get(page_id)

    async def trash(self, *, page_id: str) -> bool:
        self._fail("trash", page_id)
        self.operations.append(("trash", page_id))
        return self.items.pop(page_id, None) is not None

    async def set_snoozed_until(self, *, page_id: str, value: date) -> bool:
        self._fail("snooze", page_id)
        self.operations.append(("snooze", page_id, value))
        item = self.items.get(page_id)
        if item is None:
            return False
        self.items[page_id] = item.model_copy(update={"snoozed_until": value})
        return True

    async def list_planned_purchases(self) -> list[BrainDumpItem]:
        self.operations.append(("list_planned",))
        return [item for item in self.items.values() if item.is_planned_purchase]

    async def set_purchase_focus(self, *, page_id: str, focused: bool) -> bool:
        self._fail("focus", page_id)
        self.operations.append(("focus", page_id, focused))
        item = self.items.get(page_id)
        if item is None:
            return False
        self.items[page_id] = item.model_copy(update={"purchase_focus": focused})
        return True

    async def update_planned_purchase_state(
        self,
        *,
        page_id: str,
        snoozed_until: date,
        focused: bool,
    ) -> bool:
        self._fail("planned_state", page_id)
        self.operations.append(("planned_state", page_id, snoozed_until, focused))
        item = self.items.get(page_id)
        if item is None:
            return False
        self.items[page_id] = item.model_copy(
            update={"snoozed_until": snoozed_until, "purchase_focus": focused}
        )
        return True

    def _fail(self, operation: str, page_id: str) -> None:
        if self.fail_operation == (operation, page_id):
            raise ItemPersistenceError("safe fake failure")


def item(
    number: int = 1,
    *,
    type_: CaptureType = CaptureType.TASK,
    domain: Domain = Domain.PERSONAL,
    shopping_kind: ShoppingKind = ShoppingKind.NONE,
    focused: bool = False,
    snoozed_until: date | None = None,
) -> BrainDumpItem:
    page_id = f"{number:032x}"
    return BrainDumpItem(
        page_id=page_id,
        page_url=f"https://www.notion.so/{page_id}",
        title=f"Item {number}",
        type=type_,
        domain=domain,
        shopping_kind=shopping_kind,
        purchase_focus=focused,
        due=date(2026, 10, 1),
        snoozed_until=snoozed_until,
        confidence=Confidence.HIGH,
    )


async def execute(
    repository: FakeItemRepository,
    target: BrainDumpItem,
    action: CallbackAction,
    *,
    reference: datetime = REFERENCE,
):
    service = ItemActionService(repository=repository, policy=ActionPolicy())
    return await service.execute(
        callback=ActionCallback(action=action, page_id=target.page_id),
        reference_datetime=reference,
    )


async def test_done_deletes_task_and_repeated_done_is_stale() -> None:
    target = item()
    repository = FakeItemRepository(target)

    first = await execute(repository, target, CallbackAction.DONE)
    second = await execute(repository, target, CallbackAction.DONE)

    assert first.status is ActionResultStatus.APPLIED
    assert second.status is ActionResultStatus.STALE
    assert repository.operations == [("trash", target.page_id)]


@pytest.mark.parametrize(
    "target",
    [
        item(type_=CaptureType.IDEA),
        item(domain=Domain.SHOPPING, shopping_kind=ShoppingKind.ROUTINE),
    ],
)
async def test_done_rejects_non_task_or_shopping_task(target: BrainDumpItem) -> None:
    repository = FakeItemRepository(target)

    result = await execute(repository, target, CallbackAction.DONE)

    assert result.status is ActionResultStatus.UNAVAILABLE
    assert repository.operations == []


async def test_done_propagates_repository_failure_without_deleting() -> None:
    target = item()
    repository = FakeItemRepository(target)
    repository.fail_operation = ("trash", target.page_id)

    with pytest.raises(ItemPersistenceError):
        await execute(repository, target, CallbackAction.DONE)
    assert target.page_id in repository.items


async def test_delete_propagates_repository_failure_without_false_success() -> None:
    target = item(type_=CaptureType.IDEA)
    repository = FakeItemRepository(target)
    repository.fail_operation = ("trash", target.page_id)

    with pytest.raises(ItemPersistenceError):
        await execute(repository, target, CallbackAction.DELETE)
    assert target.page_id in repository.items


async def test_routine_and_nonfocused_planned_bought_only_delete_selected() -> None:
    routine = item(1, domain=Domain.SHOPPING, shopping_kind=ShoppingKind.ROUTINE)
    planned = item(2, domain=Domain.SHOPPING, shopping_kind=ShoppingKind.PLANNED)
    other = item(3, domain=Domain.SHOPPING, shopping_kind=ShoppingKind.PLANNED)
    repository = FakeItemRepository(routine, planned, other)

    await execute(repository, routine, CallbackAction.BOUGHT)
    await execute(repository, planned, CallbackAction.BOUGHT)

    assert repository.operations == [
        ("trash", routine.page_id),
        ("trash", planned.page_id),
    ]
    assert other.page_id in repository.items


async def test_bought_propagates_repository_failure_without_false_success() -> None:
    target = item(domain=Domain.SHOPPING, shopping_kind=ShoppingKind.ROUTINE)
    repository = FakeItemRepository(target)
    repository.fail_operation = ("trash", target.page_id)

    with pytest.raises(ItemPersistenceError):
        await execute(repository, target, CallbackAction.BOUGHT)
    assert target.page_id in repository.items


async def test_focused_planned_bought_applies_cooldown_before_delete() -> None:
    focused = item(
        1,
        domain=Domain.SHOPPING,
        shopping_kind=ShoppingKind.PLANNED,
        focused=True,
    )
    early = item(
        2,
        domain=Domain.SHOPPING,
        shopping_kind=ShoppingKind.PLANNED,
        focused=True,
        snoozed_until=date(2026, 9, 10),
    )
    later = item(
        3,
        domain=Domain.SHOPPING,
        shopping_kind=ShoppingKind.PLANNED,
        snoozed_until=date(2026, 10, 15),
    )
    routine = item(4, domain=Domain.SHOPPING, shopping_kind=ShoppingKind.ROUTINE)
    repository = FakeItemRepository(focused, early, later, routine)

    result = await execute(repository, focused, CallbackAction.BOUGHT)

    assert result.status is ActionResultStatus.APPLIED
    assert repository.items[early.page_id].snoozed_until == date(2026, 9, 30)
    assert repository.items[early.page_id].purchase_focus is False
    assert repository.items[later.page_id].snoozed_until == date(2026, 10, 15)
    assert repository.items[routine.page_id].snoozed_until is None
    assert repository.operations[-1] == ("trash", focused.page_id)


@pytest.mark.parametrize("type_", list(CaptureType))
async def test_delete_accepts_every_item_type(type_: CaptureType) -> None:
    target = item(type_=type_)
    repository = FakeItemRepository(target)

    result = await execute(repository, target, CallbackAction.DELETE)

    assert result.status is ActionResultStatus.APPLIED
    assert target.page_id not in repository.items


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (item(type_=CaptureType.TASK), date(2026, 9, 7)),
        (item(type_=CaptureType.IDEA), date(2026, 9, 14)),
        (item(type_=CaptureType.THOUGHT), date(2026, 9, 30)),
        (item(type_=CaptureType.REFERENCE), date(2026, 9, 30)),
        (
            item(domain=Domain.SHOPPING, shopping_kind=ShoppingKind.PLANNED),
            date(2026, 9, 30),
        ),
    ],
)
async def test_keep_uses_type_specific_duration_without_changing_due(
    target: BrainDumpItem,
    expected: date,
) -> None:
    original_due = target.due
    repository = FakeItemRepository(target)

    result = await execute(repository, target, CallbackAction.KEEP)

    assert result.effective_date == expected
    assert repository.items[target.page_id].snoozed_until == expected
    assert repository.items[target.page_id].due == original_due


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (CallbackAction.SNOOZE_TOMORROW, date(2026, 9, 1)),
        (CallbackAction.SNOOZE_NEXT_WEEK, date(2026, 9, 7)),
        (CallbackAction.SNOOZE_TWO_WEEKS, date(2026, 9, 14)),
        (CallbackAction.SNOOZE_ONE_MONTH, date(2026, 9, 30)),
    ],
)
async def test_snooze_choices_use_singapore_calendar(
    action: CallbackAction,
    expected: date,
) -> None:
    target = item()
    repository = FakeItemRepository(target)

    first = await execute(repository, target, action)
    second = await execute(repository, target, action)

    assert first.effective_date == second.effective_date == expected


@pytest.mark.parametrize(
    ("reference", "action", "expected"),
    [
        (
            datetime(2026, 12, 31, 20, tzinfo=SGT),
            CallbackAction.SNOOZE_TOMORROW,
            date(2027, 1, 1),
        ),
        (
            datetime(2024, 1, 31, 20, tzinfo=SGT),
            CallbackAction.SNOOZE_ONE_MONTH,
            date(2024, 2, 29),
        ),
    ],
)
async def test_snooze_handles_year_and_leap_month_boundaries(
    reference: datetime,
    action: CallbackAction,
    expected: date,
) -> None:
    target = item()
    repository = FakeItemRepository(target)

    result = await execute(repository, target, action, reference=reference)

    assert result.effective_date == expected


async def test_focus_replaces_existing_focus_and_keeps_snoozes() -> None:
    old = item(
        1,
        domain=Domain.SHOPPING,
        shopping_kind=ShoppingKind.PLANNED,
        focused=True,
        snoozed_until=date(2026, 10, 1),
    )
    selected = item(2, domain=Domain.SHOPPING, shopping_kind=ShoppingKind.PLANNED)
    repository = FakeItemRepository(old, selected)

    result = await execute(repository, selected, CallbackAction.FOCUS)

    assert result.status is ActionResultStatus.APPLIED
    assert repository.items[old.page_id].purchase_focus is False
    assert repository.items[selected.page_id].purchase_focus is True
    assert repository.items[old.page_id].snoozed_until == date(2026, 10, 1)
    assert repository.operations[-2:] == [
        ("focus", old.page_id, False),
        ("focus", selected.page_id, True),
    ]


@pytest.mark.parametrize(
    "target",
    [
        item(domain=Domain.SHOPPING, shopping_kind=ShoppingKind.ROUTINE),
        item(type_=CaptureType.IDEA),
    ],
)
async def test_focus_rejects_non_planned_items(target: BrainDumpItem) -> None:
    repository = FakeItemRepository(target)

    result = await execute(repository, target, CallbackAction.FOCUS)

    assert result.status is ActionResultStatus.UNAVAILABLE
    assert repository.operations == []


async def test_focus_failure_after_clear_leaves_recovery_safe_no_focus_state() -> None:
    old = item(
        1,
        domain=Domain.SHOPPING,
        shopping_kind=ShoppingKind.PLANNED,
        focused=True,
    )
    selected = item(2, domain=Domain.SHOPPING, shopping_kind=ShoppingKind.PLANNED)
    repository = FakeItemRepository(old, selected)
    repository.fail_operation = ("focus", selected.page_id)

    with pytest.raises(ItemPersistenceError):
        await execute(repository, selected, CallbackAction.FOCUS)

    assert repository.items[old.page_id].purchase_focus is False
    assert repository.items[selected.page_id].purchase_focus is False


async def test_back_only_returns_current_item_without_mutation() -> None:
    target = item()
    repository = FakeItemRepository(target)

    result = await execute(repository, target, CallbackAction.BACK)

    assert result.status is ActionResultStatus.DISPLAY
    assert repository.operations == []
