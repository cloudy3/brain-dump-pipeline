"""Phase 1 HTTP routes."""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.integrations.telegram import TelegramDeliveryError
from app.models.telegram import TelegramUpdate, WebhookResponse
from app.services.telegram_updates import UnauthorizedTelegramUpdate

router = APIRouter()


async def require_valid_webhook_secret(
    request: Request,
    supplied_secret: Annotated[
        str | None,
        Header(alias="X-Telegram-Bot-Api-Secret-Token"),
    ] = None,
) -> None:
    expected_secret = request.app.state.settings.telegram_webhook_secret.get_secret_value()
    supplied_bytes = (supplied_secret or "").encode()
    expected_bytes = expected_secret.encode()
    if not secrets.compare_digest(supplied_bytes, expected_bytes):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/webhooks/telegram", response_model=WebhookResponse)
async def telegram_webhook(
    update: TelegramUpdate,
    request: Request,
    _: Annotated[None, Depends(require_valid_webhook_secret)],
) -> WebhookResponse:
    try:
        outcome = await request.app.state.telegram_update_service.handle(update)
    except UnauthorizedTelegramUpdate as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from error
    except TelegramDeliveryError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Telegram acknowledgement failed",
        ) from error
    return WebhookResponse(status=outcome.value)

