"""Business-level handling for incoming Telegram updates."""

import logging
from enum import StrEnum

from app.integrations.telegram import TelegramClient, TelegramDeliveryError
from app.models.capture import CaptureInput
from app.models.telegram import TelegramUpdate
from app.repositories.captures import CaptureRepository

PERSISTED_ACKNOWLEDGEMENT = "Saved"

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
        allowed_user_id: int,
        allowed_chat_id: int,
    ) -> None:
        self._telegram_client = telegram_client
        self._capture_repository = capture_repository
        self._allowed_user_id = allowed_user_id
        self._allowed_chat_id = allowed_chat_id

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

        save_status = await self._capture_repository.save_if_new(
            CaptureInput(
                original_input=message.text,
                telegram_update_id=update.update_id,
                telegram_message_id=message.message_id,
            )
        )

        try:
            await self._telegram_client.send_message(
                chat_id=message.chat.id,
                text=PERSISTED_ACKNOWLEDGEMENT,
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
            persistence_status=save_status.value,
        )
        return UpdateOutcome.ACKNOWLEDGED

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
