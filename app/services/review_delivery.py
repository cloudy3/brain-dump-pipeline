"""Scheduled Telegram delivery for deterministic Phase 6 review plans."""

import logging
from datetime import date, datetime, timedelta
from typing import Protocol

from app.core.time import SINGAPORE_TIMEZONE
from app.integrations.telegram import TelegramClient, TelegramDeliveryError
from app.models.classification import CaptureType, Domain
from app.models.reviews import DeadlineUrgency, ReviewItem, ReviewPlan, ReviewRequest, ReviewWindow
from app.models.scheduling import ReviewDeliveryResult
from app.repositories.reviews import ReviewPersistenceError, ReviewRepository
from app.services.item_views import ItemActionViewBuilder

logger = logging.getLogger(__name__)

_MAX_TITLE_LENGTH = 160


class ReviewPlanner(Protocol):
    async def build_plan(self, *, request: ReviewRequest) -> ReviewPlan: ...


class ReviewDeliveryService:
    """Generate and deliver one review without duplicating selection logic."""

    def __init__(
        self,
        *,
        planner: ReviewPlanner,
        repository: ReviewRepository,
        telegram_client: TelegramClient,
        chat_id: int,
    ) -> None:
        self._planner = planner
        self._repository = repository
        self._telegram_client = telegram_client
        self._chat_id = chat_id

    async def deliver(
        self,
        *,
        window: ReviewWindow,
        execution_time: datetime,
    ) -> ReviewDeliveryResult:
        plan = await self._planner.build_plan(
            request=ReviewRequest(window=window, reference_time=execution_time)
        )
        if plan.is_empty:
            return ReviewDeliveryResult(
                window=window,
                item_count=0,
                last_surfaced_recorded=True,
            )

        delivered_items = _delivered_items(plan)
        try:
            await self._telegram_client.send_message(
                chat_id=self._chat_id,
                text=format_review(plan),
            )
        except TelegramDeliveryError as error:
            logger.error(
                "review_primary_delivery_failed",
                extra={
                    "operation": "scheduled_review_delivery",
                    "state": "failure",
                    "error_type": type(error).__name__,
                    "window": window.value,
                    "item_count": len(delivered_items),
                },
            )
            raise

        surfaced_recorded = await self._record_last_surfaced(
            delivered_items,
            execution_time.astimezone(SINGAPORE_TIMEZONE).date(),
        )
        await self._send_action_messages(delivered_items)
        return ReviewDeliveryResult(
            window=window,
            item_count=len(delivered_items),
            last_surfaced_recorded=surfaced_recorded,
        )

    async def _record_last_surfaced(
        self,
        items: tuple[ReviewItem, ...],
        surfaced_on: date,
    ) -> bool:
        page_ids = tuple(dict.fromkeys(item.page_id for item in items))
        for attempt in range(2):
            try:
                await self._repository.record_last_surfaced(
                    page_ids=page_ids,
                    surfaced_on=surfaced_on,
                )
            except ReviewPersistenceError as error:
                if attempt == 0:
                    logger.warning(
                        "review_last_surfaced_retry",
                        extra={
                            "operation": "scheduled_review_persistence",
                            "state": "retry",
                            "error_type": type(error).__name__,
                            "item_count": len(page_ids),
                        },
                    )
                    continue
                logger.error(
                    "review_last_surfaced_failed",
                    extra={
                        "operation": "scheduled_review_persistence",
                        "state": "failure",
                        "error_type": type(error).__name__,
                        "item_count": len(page_ids),
                    },
                )
                return False
            else:
                return True
        return False

    async def _send_action_messages(self, items: tuple[ReviewItem, ...]) -> None:
        for index, item in enumerate(items, start=1):
            try:
                await self._telegram_client.send_message(
                    chat_id=self._chat_id,
                    text=format_action_item(index, item),
                    reply_markup=ItemActionViewBuilder.action_keyboard(item),
                    disable_notification=True,
                )
            except TelegramDeliveryError as error:
                logger.error(
                    "review_action_message_failed",
                    extra={
                        "operation": "scheduled_review_action_message",
                        "state": "failure",
                        "error_type": type(error).__name__,
                        "item_index": index,
                    },
                )


def format_review(plan: ReviewPlan) -> str:
    """Render a complete compact primary review notification."""
    lines = [_heading(plan.window), ""]
    for index, entry in enumerate(plan.entries, start=1):
        lines.append(f"{index}. {_title(entry.item.title)}")
        metadata = _metadata(
            entry.item,
            entry.urgency,
            plan.generated_at.astimezone(SINGAPORE_TIMEZONE).date(),
        )
        if metadata:
            lines.append(f"   {metadata}")

    group = plan.routine_shopping
    if group is not None:
        if plan.entries:
            lines.append("")
        lines.append("Shopping")
        lines.extend(f"• {_title(item.title)}" for item in group.items)
        if group.additional_count:
            lines.append(f"+ {group.additional_count} more shopping items")
    return "\n".join(lines).rstrip()


def format_action_item(index: int, item: ReviewItem) -> str:
    metadata = _metadata(item, None, None)
    text = f"{index}. {_title(item.title)}"
    return f"{text}\n{metadata}" if metadata else text


def _delivered_items(plan: ReviewPlan) -> tuple[ReviewItem, ...]:
    normal = tuple(entry.item for entry in plan.entries)
    routine = plan.routine_shopping.items if plan.routine_shopping is not None else ()
    return normal + routine


def _heading(window: ReviewWindow) -> str:
    return {
        ReviewWindow.MORNING: "Morning Brain Dump",
        ReviewWindow.AFTER_WORK: "After work",
        ReviewWindow.EVENING: "Worth revisiting tonight",
        ReviewWindow.WEEKEND: "Weekend Brain Dump",
    }[window]


def _metadata(
    item: ReviewItem,
    urgency: DeadlineUrgency | None,
    reference_date: date | None,
) -> str | None:
    if item.type is CaptureType.TASK and item.due is not None:
        if urgency is DeadlineUrgency.OVERDUE:
            return f"Overdue · {item.due.day} {item.due:%b}"
        if urgency is DeadlineUrgency.TODAY:
            return "Due today"
        if reference_date is not None and item.due == reference_date + timedelta(days=1):
            return "Due tomorrow"
        return f"Due {item.due.day} {item.due:%b}"
    if item.is_planned_purchase:
        return "Focused planned purchase" if item.purchase_focus else "Planned purchase"
    if item.is_routine_purchase:
        return "Routine shopping"
    if item.domain is Domain.PLACES and item.location:
        return item.location
    if item.type is CaptureType.IDEA:
        return f"{item.domain.value} idea"
    return item.type.value


def _title(value: str) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= _MAX_TITLE_LENGTH:
        return normalized
    return f"{normalized[: _MAX_TITLE_LENGTH - 1].rstrip()}…"
