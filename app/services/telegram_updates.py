"""Business-level handling for incoming Telegram updates."""

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime
from enum import StrEnum

from app.core.time import SINGAPORE_TIMEZONE
from app.integrations.telegram import TelegramClient, TelegramDeliveryError
from app.models.actions import ActionCallback, BrainDumpItem, CallbackAction
from app.models.capture import CaptureInput
from app.models.classification import CaptureType, Confidence
from app.models.telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    TelegramCallbackQuery,
    TelegramUpdate,
)
from app.repositories.captures import CaptureRepository
from app.repositories.items import ItemPersistenceError
from app.services.classification import ClassificationService
from app.services.item_actions import (
    ActionResultStatus,
    ItemActionResult,
    ItemActionService,
)

logger = logging.getLogger(__name__)


class UpdateOutcome(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    IGNORED = "ignored"


class UnauthorizedTelegramUpdate(RuntimeError):
    """Raised when an update is not owned by the configured Telegram user/chat."""


class TelegramUpdateService:
    def __init__(
        self,
        *,
        telegram_client: TelegramClient,
        capture_repository: CaptureRepository,
        classifier: ClassificationService,
        item_action_service: ItemActionService,
        allowed_user_id: int,
        allowed_chat_id: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._telegram_client = telegram_client
        self._capture_repository = capture_repository
        self._classifier = classifier
        self._item_action_service = item_action_service
        self._allowed_user_id = allowed_user_id
        self._allowed_chat_id = allowed_chat_id
        self._clock = clock or (lambda: datetime.now(SINGAPORE_TIMEZONE))
        self._capture_lock = asyncio.Lock()

    async def handle(self, update: TelegramUpdate) -> UpdateOutcome:
        if update.callback_query is not None:
            return await self._handle_callback(update, update.callback_query)

        message = update.message
        if message is None:
            self._log_outcome(update, UpdateOutcome.IGNORED)
            return UpdateOutcome.IGNORED

        if (
            message.from_ is None
            or message.from_.id != self._allowed_user_id
            or message.chat.id != self._allowed_chat_id
        ):
            logger.warning(
                "telegram_update_rejected",
                extra={
                    "operation": "telegram_webhook",
                    "update_id": update.update_id,
                    "message_id": message.message_id,
                    "state": "unauthorized",
                },
            )
            raise UnauthorizedTelegramUpdate

        if message.text is None or not message.text.strip():
            self._log_outcome(update, UpdateOutcome.IGNORED)
            return UpdateOutcome.IGNORED

        async with self._capture_lock:
            summary = await self._capture_repository.find_by_telegram_identity(
                telegram_update_id=update.update_id,
                telegram_message_id=message.message_id,
            )
            persistence_status = "existing"
            if summary is None:
                classification_outcome = await self._classifier.classify(
                    original_input=message.text,
                    reference_datetime=self._clock(),
                )
                save_result = await self._capture_repository.save_if_new(
                    CaptureInput(
                        original_input=message.text,
                        telegram_update_id=update.update_id,
                        telegram_message_id=message.message_id,
                        classification=classification_outcome.classification,
                    )
                )
                summary = save_result.summary
                persistence_status = save_result.status.value

        try:
            await self._telegram_client.send_message(
                chat_id=message.chat.id,
                text=self._acknowledgement(summary),
                reply_markup=self._action_keyboard(summary),
            )
        except TelegramDeliveryError:
            logger.error(
                "telegram_acknowledgement_failed",
                extra={
                    "operation": "telegram_webhook",
                    "update_id": update.update_id,
                    "message_id": message.message_id,
                    "state": "failure",
                    "error_type": TelegramDeliveryError.__name__,
                },
            )
            raise

        self._log_outcome(
            update,
            UpdateOutcome.ACKNOWLEDGED,
            persistence_status=persistence_status,
        )
        return UpdateOutcome.ACKNOWLEDGED

    @staticmethod
    def _acknowledgement(summary: BrainDumpItem) -> str:
        heading = f"Saved · {summary.type.value} · {summary.domain.value}"
        if summary.confidence is Confidence.LOW:
            heading += " · Low confidence"
        return f"{heading}\n{summary.title}"

    async def _handle_callback(
        self,
        update: TelegramUpdate,
        query: TelegramCallbackQuery,
    ) -> UpdateOutcome:
        message = query.message
        if message is None or query.from_ is None:
            self._log_outcome(update, UpdateOutcome.IGNORED)
            return UpdateOutcome.IGNORED
        if query.from_.id != self._allowed_user_id or message.chat.id != self._allowed_chat_id:
            logger.warning(
                "telegram_callback_rejected",
                extra={
                    "operation": "telegram_callback",
                    "update_id": update.update_id,
                    "state": "unauthorized",
                },
            )
            raise UnauthorizedTelegramUpdate

        try:
            callback = ActionCallback.decode(query.data or "")
        except ValueError:
            await self._telegram_client.answer_callback_query(
                callback_query_id=query.id,
                text="Invalid action.",
                show_alert=True,
            )
            self._log_outcome(update, UpdateOutcome.ACKNOWLEDGED)
            return UpdateOutcome.ACKNOWLEDGED

        await self._telegram_client.answer_callback_query(callback_query_id=query.id)
        try:
            result = await self._item_action_service.execute(
                callback=callback,
                reference_datetime=self._clock(),
            )
        except ItemPersistenceError as error:
            logger.error(
                "telegram_item_action_failed",
                extra={
                    "operation": "telegram_callback",
                    "update_id": update.update_id,
                    "message_id": message.message_id,
                    "state": "failure",
                    "error_type": type(error).__name__,
                },
            )
            await self._send_callback_failure(message.chat.id)
            self._log_outcome(update, UpdateOutcome.ACKNOWLEDGED)
            return UpdateOutcome.ACKNOWLEDGED

        text, reply_markup = self._view_for_result(result, message.text)
        await self._edit_callback_message(
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=text,
            reply_markup=reply_markup,
        )
        self._log_outcome(
            update,
            UpdateOutcome.ACKNOWLEDGED,
            persistence_status=result.status.value,
        )
        return UpdateOutcome.ACKNOWLEDGED

    async def _send_callback_failure(self, chat_id: int) -> None:
        try:
            await self._telegram_client.send_message(
                chat_id=chat_id,
                text="Couldn't update that item. Please try again.",
            )
        except TelegramDeliveryError:
            logger.error(
                "telegram_callback_failure_notice_failed",
                extra={
                    "operation": "telegram_callback",
                    "state": "failure",
                    "error_type": TelegramDeliveryError.__name__,
                },
            )

    async def _edit_callback_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None,
    ) -> None:
        try:
            await self._telegram_client.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
            )
        except TelegramDeliveryError:
            logger.error(
                "telegram_callback_message_edit_failed",
                extra={
                    "operation": "telegram_callback",
                    "message_id": message_id,
                    "state": "failure",
                    "error_type": TelegramDeliveryError.__name__,
                },
            )
            try:
                await self._telegram_client.send_message(chat_id=chat_id, text=text)
            except TelegramDeliveryError:
                logger.error(
                    "telegram_callback_fallback_notice_failed",
                    extra={
                        "operation": "telegram_callback",
                        "message_id": message_id,
                        "state": "failure",
                        "error_type": TelegramDeliveryError.__name__,
                    },
                )

    @classmethod
    def _view_for_result(
        cls,
        result: ItemActionResult,
        existing_text: str | None,
    ) -> tuple[str, InlineKeyboardMarkup | None]:
        if result.status is ActionResultStatus.STALE:
            return "Item no longer exists.", None
        item = result.item
        if item is None:
            return "Item no longer exists.", None
        if result.status is ActionResultStatus.UNAVAILABLE:
            return f"Action unavailable\n{item.title}", cls._action_keyboard(item)
        if result.status is ActionResultStatus.DISPLAY:
            if result.action is CallbackAction.SNOOZE_MENU:
                return existing_text or cls._acknowledgement(item), cls._snooze_keyboard(item)
            return cls._acknowledgement(item), cls._action_keyboard(item)
        if result.action is CallbackAction.DONE:
            return f"Done\n{item.title}", None
        if result.action is CallbackAction.BOUGHT:
            return f"Bought\n{item.title}", None
        if result.action is CallbackAction.DELETE:
            return f"Deleted\n{item.title}", None
        if result.action is CallbackAction.KEEP:
            return (
                f"Kept until {cls._format_date(result.effective_date)}\n{item.title}",
                cls._action_keyboard(item),
            )
        if result.action in {
            CallbackAction.SNOOZE_TOMORROW,
            CallbackAction.SNOOZE_NEXT_WEEK,
            CallbackAction.SNOOZE_TWO_WEEKS,
            CallbackAction.SNOOZE_ONE_MONTH,
        }:
            return (
                f"Snoozed until {cls._format_date(result.effective_date)}\n{item.title}",
                cls._action_keyboard(item),
            )
        if result.action is CallbackAction.FOCUS:
            return f"Focused purchase\n{item.title}", cls._action_keyboard(item)
        return cls._acknowledgement(item), cls._action_keyboard(item)

    @classmethod
    def _action_keyboard(cls, item: BrainDumpItem) -> InlineKeyboardMarkup:
        if item.is_planned_purchase:
            rows = [
                [
                    cls._button("Focus", CallbackAction.FOCUS, item),
                    cls._button("Bought", CallbackAction.BOUGHT, item),
                ],
                [
                    cls._button("Keep", CallbackAction.KEEP, item),
                    cls._button("Delete", CallbackAction.DELETE, item),
                ],
            ]
        elif item.is_routine_purchase:
            rows = [
                [
                    cls._button("Bought", CallbackAction.BOUGHT, item),
                    cls._button("Snooze", CallbackAction.SNOOZE_MENU, item),
                ],
                [cls._button("Delete", CallbackAction.DELETE, item)],
            ]
        elif item.type is CaptureType.TASK:
            rows = [
                [
                    cls._button("Done", CallbackAction.DONE, item),
                    cls._button("Snooze", CallbackAction.SNOOZE_MENU, item),
                ],
                [
                    cls._button("Keep", CallbackAction.KEEP, item),
                    cls._button("Delete", CallbackAction.DELETE, item),
                ],
            ]
        elif item.type in {CaptureType.IDEA, CaptureType.THOUGHT}:
            rows = [
                [
                    cls._button("Keep", CallbackAction.KEEP, item),
                    cls._button("Delete", CallbackAction.DELETE, item),
                ]
            ]
        else:
            rows = [[cls._button("Delete", CallbackAction.DELETE, item)]]
        rows.append([InlineKeyboardButton(text="Open", url=item.page_url)])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @classmethod
    def _snooze_keyboard(cls, item: BrainDumpItem) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    cls._button("Tomorrow", CallbackAction.SNOOZE_TOMORROW, item),
                    cls._button("Next week", CallbackAction.SNOOZE_NEXT_WEEK, item),
                ],
                [
                    cls._button("2 weeks", CallbackAction.SNOOZE_TWO_WEEKS, item),
                    cls._button("1 month", CallbackAction.SNOOZE_ONE_MONTH, item),
                ],
                [cls._button("Back", CallbackAction.BACK, item)],
            ]
        )

    @staticmethod
    def _button(
        text: str,
        action: CallbackAction,
        item: BrainDumpItem,
    ) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=text,
            callback_data=ActionCallback(action=action, page_id=item.page_id).encode(),
        )

    @staticmethod
    def _format_date(value: date | None) -> str:
        if value is None:
            return "unknown date"
        return f"{value.day} {value:%b}"

    @staticmethod
    def _log_outcome(
        update: TelegramUpdate,
        outcome: UpdateOutcome,
        *,
        persistence_status: str | None = None,
    ) -> None:
        callback_message = (
            update.callback_query.message if update.callback_query is not None else None
        )
        message_id = (
            update.message.message_id
            if update.message is not None
            else callback_message.message_id
            if callback_message is not None
            else None
        )
        logger.info(
            "telegram_update_handled",
            extra={
                "operation": "telegram_webhook",
                "update_id": update.update_id,
                "message_id": message_id,
                "state": outcome.value,
                "persistence_status": persistence_status,
            },
        )
