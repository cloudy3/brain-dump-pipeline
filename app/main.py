"""FastAPI application entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from app.api.routes import router
from app.core.config import Settings
from app.core.logging import configure_logging
from app.integrations.telegram import TelegramBotAPIClient, TelegramClient
from app.services.telegram_updates import TelegramUpdateService

logger = logging.getLogger(__name__)


def create_app(
    *,
    settings: Settings | None = None,
    telegram_client: TelegramClient | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        effective_settings = settings or Settings()  # type: ignore[call-arg]
        configure_logging(effective_settings.log_level)

        effective_client = telegram_client or TelegramBotAPIClient(
            bot_token=effective_settings.telegram_bot_token,
            api_base_url=str(effective_settings.telegram_api_base_url),
            timeout_seconds=effective_settings.telegram_request_timeout_seconds,
        )
        application.state.settings = effective_settings
        application.state.telegram_update_service = TelegramUpdateService(
            telegram_client=effective_client,
            allowed_user_id=effective_settings.telegram_allowed_user_id,
            allowed_chat_id=effective_settings.telegram_allowed_chat_id,
        )

        yield

        if isinstance(effective_client, TelegramBotAPIClient):
            await effective_client.aclose()

    application = FastAPI(
        title="Brain Dump Pipeline",
        version="0.1.0",
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
