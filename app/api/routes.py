"""HTTP routes for health, Telegram updates, and scheduled reviews."""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError

from app.integrations.telegram import TelegramDeliveryError
from app.models.scheduling import (
    ScheduledReviewRequest,
    SchedulerExecutionIdentity,
    SchedulerRunResponse,
)
from app.models.telegram import TelegramUpdate, WebhookResponse
from app.repositories.captures import CapturePersistenceError
from app.repositories.reviews import ReviewPersistenceError
from app.services.scheduler import InvalidSchedulerExecution
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


async def require_valid_scheduler_secret(
    request: Request,
    supplied_secret: Annotated[
        str | None,
        Header(alias="X-Brain-Dump-Scheduler-Secret"),
    ] = None,
) -> None:
    expected_secret = request.app.state.settings.scheduler_secret.get_secret_value()
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
    except CapturePersistenceError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Capture could not be saved",
        ) from error
    except TelegramDeliveryError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Telegram acknowledgement failed",
        ) from error
    return WebhookResponse(status=outcome.value)


@router.post("/internal/reviews/run", response_model=SchedulerRunResponse)
async def run_scheduled_review(
    request: Request,
    _: Annotated[None, Depends(require_valid_scheduler_secret)],
    job_name: Annotated[
        str | None,
        Header(alias="X-CloudScheduler-JobName"),
    ] = None,
    schedule_time: Annotated[
        str | None,
        Header(alias="X-CloudScheduler-ScheduleTime"),
    ] = None,
) -> SchedulerRunResponse:
    try:
        payload = ScheduledReviewRequest.model_validate_json(await request.body())
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid scheduler request",
        ) from error
    try:
        identity = SchedulerExecutionIdentity(
            job_name=job_name or "",
            schedule_time=schedule_time,
        )
        return await request.app.state.scheduled_review_service.run(
            slot=payload.slot,
            identity=identity,
        )
    except (InvalidSchedulerExecution, ValidationError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except ReviewPersistenceError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Review could not be generated",
        ) from error
    except TelegramDeliveryError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Review could not be delivered",
        ) from error
