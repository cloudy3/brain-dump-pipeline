"""Business-level handling for incoming Telegram updates."""

import logging
from enum import StrEnum

from app.integrations.telegram import TelegramClient, TelegramDeliveryError
from app.models.telegram import TelegramUpdate

TEMPORARY_ACKNOWLEDGEMENT = "Received — temporary acknowledgement; storage is not enabled yet."

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
        allowed_user_id: int,
        allowed_chat_id: int,
    ) -> None:
        self._telegram_client = telegram_client
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

        try:
            await self._telegram_client.send_message(
                chat_id=message.chat.id,
                text=TEMPORARY_ACKNOWLEDGEMENT,
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

        self._log_outcome(update, UpdateOutcome.ACKNOWLEDGED)
        return UpdateOutcome.ACKNOWLEDGED

    @staticmethod
    def _log_outcome(update: TelegramUpdate, outcome: UpdateOutcome) -> None:
        message_id = update.message.message_id if update.message is not None else None
        logger.info(
            "telegram_update_handled",
            extra={
                "operation": "telegram_webhook",
                "update_id": update.update_id,
                "message_id": message_id,
                "state": outcome.value,
            },
        )

