"""Deterministic business rules for Brain Dump item actions."""

import asyncio
from datetime import date, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.core.time import add_calendar_month, singapore_date
from app.models.actions import (
    ActionCallback,
    ActionPolicy,
    BrainDumpItem,
    CallbackAction,
)
from app.models.classification import CaptureType, Domain
from app.repositories.items import ItemRepository


class ActionResultStatus(StrEnum):
    APPLIED = "applied"
    DISPLAY = "display"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class ItemActionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: ActionResultStatus
    action: CallbackAction
    item: BrainDumpItem | None = None
    effective_date: date | None = None


class ItemActionService:
    """Apply one action at a time without AI or Telegram-specific behavior."""

    def __init__(self, *, repository: ItemRepository, policy: ActionPolicy) -> None:
        self._repository = repository
        self._policy = policy
        self._lock = asyncio.Lock()

    async def execute(
        self,
        *,
        callback: ActionCallback,
        reference_datetime: datetime,
    ) -> ItemActionResult:
        async with self._lock:
            item = await self._repository.get_by_id(page_id=callback.page_id)
            if item is None:
                return ItemActionResult(
                    status=ActionResultStatus.STALE,
                    action=callback.action,
                )

            if callback.action in {CallbackAction.SNOOZE_MENU, CallbackAction.BACK}:
                return ItemActionResult(
                    status=ActionResultStatus.DISPLAY,
                    action=callback.action,
                    item=item,
                )
            if callback.action is CallbackAction.DONE:
                return await self._done(item, callback.action)
            if callback.action is CallbackAction.BOUGHT:
                return await self._bought(item, callback.action, reference_datetime)
            if callback.action is CallbackAction.DELETE:
                return await self._trash(item, callback.action)
            if callback.action is CallbackAction.KEEP:
                return await self._keep(item, callback.action, reference_datetime)
            if callback.action in {
                CallbackAction.SNOOZE_TOMORROW,
                CallbackAction.SNOOZE_NEXT_WEEK,
                CallbackAction.SNOOZE_TWO_WEEKS,
                CallbackAction.SNOOZE_ONE_MONTH,
            }:
                return await self._snooze(item, callback.action, reference_datetime)
            if callback.action is CallbackAction.FOCUS:
                return await self._focus(item, callback.action)
            return self._unavailable(item, callback.action)

    async def _done(
        self,
        item: BrainDumpItem,
        action: CallbackAction,
    ) -> ItemActionResult:
        if item.type is not CaptureType.TASK or item.domain is Domain.SHOPPING:
            return self._unavailable(item, action)
        return await self._trash(item, action)

    async def _bought(
        self,
        item: BrainDumpItem,
        action: CallbackAction,
        reference_datetime: datetime,
    ) -> ItemActionResult:
        if not (item.is_routine_purchase or item.is_planned_purchase):
            return self._unavailable(item, action)
        if item.is_planned_purchase and item.purchase_focus:
            cooldown_date = singapore_date(reference_datetime) + timedelta(
                days=self._policy.planned_purchase_post_bought_cooldown_days
            )
            planned_purchases = await self._repository.list_planned_purchases()
            for purchase in planned_purchases:
                if purchase.page_id == item.page_id:
                    continue
                effective_date = max(
                    purchase.snoozed_until or cooldown_date,
                    cooldown_date,
                )
                await self._repository.update_planned_purchase_state(
                    page_id=purchase.page_id,
                    snoozed_until=effective_date,
                    focused=False,
                )
        return await self._trash(item, action)

    async def _trash(
        self,
        item: BrainDumpItem,
        action: CallbackAction,
    ) -> ItemActionResult:
        if not await self._repository.trash(page_id=item.page_id):
            return ItemActionResult(status=ActionResultStatus.STALE, action=action)
        return ItemActionResult(
            status=ActionResultStatus.APPLIED,
            action=action,
            item=item,
        )

    async def _keep(
        self,
        item: BrainDumpItem,
        action: CallbackAction,
        reference_datetime: datetime,
    ) -> ItemActionResult:
        duration = self._keep_duration(item)
        target_date = singapore_date(reference_datetime) + timedelta(days=duration)
        if not await self._repository.set_snoozed_until(
            page_id=item.page_id,
            value=target_date,
        ):
            return ItemActionResult(status=ActionResultStatus.STALE, action=action)
        updated_item = item.model_copy(update={"snoozed_until": target_date})
        return ItemActionResult(
            status=ActionResultStatus.APPLIED,
            action=action,
            item=updated_item,
            effective_date=target_date,
        )

    async def _snooze(
        self,
        item: BrainDumpItem,
        action: CallbackAction,
        reference_datetime: datetime,
    ) -> ItemActionResult:
        current_date = singapore_date(reference_datetime)
        if action is CallbackAction.SNOOZE_TOMORROW:
            target_date = current_date + timedelta(days=1)
        elif action is CallbackAction.SNOOZE_NEXT_WEEK:
            target_date = current_date + timedelta(days=7)
        elif action is CallbackAction.SNOOZE_TWO_WEEKS:
            target_date = current_date + timedelta(days=14)
        else:
            target_date = add_calendar_month(current_date)
        if not await self._repository.set_snoozed_until(
            page_id=item.page_id,
            value=target_date,
        ):
            return ItemActionResult(status=ActionResultStatus.STALE, action=action)
        updated_item = item.model_copy(update={"snoozed_until": target_date})
        return ItemActionResult(
            status=ActionResultStatus.APPLIED,
            action=action,
            item=updated_item,
            effective_date=target_date,
        )

    async def _focus(
        self,
        item: BrainDumpItem,
        action: CallbackAction,
    ) -> ItemActionResult:
        if not item.is_planned_purchase:
            return self._unavailable(item, action)
        planned_purchases = await self._repository.list_planned_purchases()
        for purchase in planned_purchases:
            if purchase.page_id != item.page_id and purchase.purchase_focus:
                await self._repository.set_purchase_focus(
                    page_id=purchase.page_id,
                    focused=False,
                )
        if not await self._repository.set_purchase_focus(
            page_id=item.page_id,
            focused=True,
        ):
            return ItemActionResult(status=ActionResultStatus.STALE, action=action)
        return ItemActionResult(
            status=ActionResultStatus.APPLIED,
            action=action,
            item=item.model_copy(update={"purchase_focus": True}),
        )

    def _keep_duration(self, item: BrainDumpItem) -> int:
        if item.is_planned_purchase:
            return self._policy.keep_planned_purchase_days
        return {
            CaptureType.TASK: self._policy.keep_task_days,
            CaptureType.IDEA: self._policy.keep_idea_days,
            CaptureType.THOUGHT: self._policy.keep_thought_days,
            CaptureType.REFERENCE: self._policy.keep_reference_days,
        }[item.type]

    @staticmethod
    def _unavailable(
        item: BrainDumpItem,
        action: CallbackAction,
    ) -> ItemActionResult:
        return ItemActionResult(
            status=ActionResultStatus.UNAVAILABLE,
            action=action,
            item=item,
        )
