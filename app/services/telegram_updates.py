"""Business-level handling for incoming Telegram updates."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.integrations.telegram import TelegramClient, TelegramDeliveryError
from app.models.capture import CaptureInput, CaptureSummary
from app.models.classification import Confidence
from app.models.telegram import TelegramUpdate
from app.repositories.captures import CaptureRepository
from app.services.classification import ClassificationService

SINGAPORE_TIMEZONE = ZoneInfo("Asia/Singapore")

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
        allowed_user_id: int,
        allowed_chat_id: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._telegram_client = telegram_client
        self._capture_repository = capture_repository
        self._classifier = classifier
        self._allowed_user_id = allowed_user_id
        self._allowed_chat_id = allowed_chat_id
        self._clock = clock or (lambda: datetime.now(SINGAPORE_TIMEZONE))
        self._capture_lock = asyncio.Lock()

    async def handle(self, update: TelegramUpdate) -> UpdateOutcome:
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
    def _acknowledgement(summary: CaptureSummary) -> str:
        heading = f"Saved · {summary.type.value} · {summary.domain.value}"
        if summary.confidence is Confidence.LOW:
            heading += " · Low confidence"
        return f"{heading}\n{summary.title}"

    @staticmethod
    def _log_outcome(
        update: TelegramUpdate,
        outcome: UpdateOutcome,
        *,
        persistence_status: str | None = None,
    ) -> None:
        message_id = update.message.message_id if update.message is not None else None
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
