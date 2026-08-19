"""FastAPI application entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from typing import cast

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from app.api.routes import router
from app.core.config import Settings
from app.core.logging import configure_logging
from app.integrations.gemini import GeminiSDKClassificationGateway
from app.integrations.notion import NotionSDKGateway
from app.integrations.telegram import TelegramBotAPIClient, TelegramClient
from app.repositories.captures import CaptureRepository
from app.repositories.items import ItemRepository
from app.repositories.notion import NotionCaptureRepository
from app.repositories.queries import QueryRepository
from app.repositories.reviews import ReviewRepository
from app.services.classification import (
    CaptureClassifier,
    ClassificationService,
)
from app.services.item_actions import ItemActionService
from app.services.queries import ManualQueryService, QueryInterpreter
from app.services.resurfacing import ResurfacingService
from app.services.review_delivery import ReviewDeliveryService
from app.services.scheduler import ScheduledReviewRunner, ScheduledReviewService
from app.services.telegram_updates import TelegramUpdateService

logger = logging.getLogger(__name__)


def create_app(
    *,
    settings: Settings | None = None,
    telegram_client: TelegramClient | None = None,
    capture_repository: CaptureRepository | None = None,
    item_repository: ItemRepository | None = None,
    classifier: ClassificationService | None = None,
    query_interpreter: QueryInterpreter | None = None,
    query_repository: QueryRepository | None = None,
    review_repository: ReviewRepository | None = None,
    scheduled_review_service: ScheduledReviewRunner | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        effective_settings = settings or Settings()  # type: ignore[call-arg]
        configure_logging(effective_settings.log_level)

        effective_telegram_client = telegram_client or TelegramBotAPIClient(
            bot_token=effective_settings.telegram_bot_token,
            api_base_url=str(effective_settings.telegram_api_base_url),
            timeout_seconds=effective_settings.telegram_request_timeout_seconds,
        )
        notion_gateway = None
        gemini_gateway = None
        effective_capture_repository = capture_repository
        effective_item_repository = item_repository
        effective_query_repository = query_repository
        effective_review_repository = review_repository
        if effective_capture_repository is None:
            notion_gateway = NotionSDKGateway(
                token=effective_settings.notion_api_token,
                timeout_seconds=effective_settings.notion_request_timeout_seconds,
                notion_version=effective_settings.notion_api_version,
            )
            effective_capture_repository = NotionCaptureRepository(
                gateway=notion_gateway,
                database_id=effective_settings.notion_brain_dump_database_id,
                data_source_id=effective_settings.notion_brain_dump_data_source_id,
            )
            effective_item_repository = effective_capture_repository
            effective_query_repository = effective_capture_repository
            effective_review_repository = effective_capture_repository
        elif effective_item_repository is None:
            effective_item_repository = cast(ItemRepository, effective_capture_repository)
        if effective_query_repository is None:
            effective_query_repository = cast(QueryRepository, effective_capture_repository)
        if effective_review_repository is None:
            effective_review_repository = cast(ReviewRepository, effective_capture_repository)
        effective_classifier = classifier
        effective_query_interpreter = query_interpreter
        if effective_classifier is None or effective_query_interpreter is None:
            gemini_gateway = GeminiSDKClassificationGateway(
                api_key=effective_settings.gemini_api_key,
                model=effective_settings.gemini_model,
                timeout_seconds=effective_settings.gemini_request_timeout_seconds,
            )
            if effective_classifier is None:
                effective_classifier = CaptureClassifier(gateway=gemini_gateway)
            if effective_query_interpreter is None:
                effective_query_interpreter = gemini_gateway
        application.state.settings = effective_settings
        application.state.telegram_update_service = TelegramUpdateService(
            telegram_client=effective_telegram_client,
            capture_repository=effective_capture_repository,
            classifier=effective_classifier,
            item_action_service=ItemActionService(
                repository=effective_item_repository,
                policy=effective_settings.action_policy(),
            ),
            query_service=ManualQueryService(
                interpreter=effective_query_interpreter,
                repository=effective_query_repository,
            ),
            allowed_user_id=effective_settings.telegram_allowed_user_id,
            allowed_chat_id=effective_settings.telegram_allowed_chat_id,
            query_result_limit=effective_settings.telegram_query_result_limit,
        )
        application.state.scheduled_review_service = (
            scheduled_review_service
            or ScheduledReviewService(
                deliverer=ReviewDeliveryService(
                    planner=ResurfacingService(
                        repository=effective_review_repository,
                        policy=effective_settings.review_policy(),
                    ),
                    repository=effective_review_repository,
                    telegram_client=effective_telegram_client,
                    chat_id=effective_settings.telegram_allowed_chat_id,
                )
            )
        )

        try:
            yield
        finally:
            if isinstance(effective_telegram_client, TelegramBotAPIClient):
                await effective_telegram_client.aclose()
            if notion_gateway is not None:
                await notion_gateway.aclose()
            if gemini_gateway is not None:
                await gemini_gateway.aclose()

    application = FastAPI(
        title="Brain Dump Pipeline",
        version="0.7.0",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def log_request(request: Request, call_next: RequestResponseEndpoint) -> Response:
        started_at = perf_counter()
        response = await call_next(request)
        logger.info(
            "http_request_completed",
            extra={
                "operation": "http_request",
                "request_id": request.headers.get("X-Request-ID"),
                "state": "success" if response.status_code < 500 else "failure",
                "status_code": response.status_code,
                "latency_ms": round((perf_counter() - started_at) * 1000, 2),
            },
        )
        return response

    application.include_router(router)
    return application


app = create_app()
