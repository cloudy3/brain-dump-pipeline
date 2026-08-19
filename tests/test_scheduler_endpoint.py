from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest

from app.integrations.telegram import TelegramDeliveryError
from app.main import create_app
from app.models.reviews import ReviewWindow
from app.models.scheduling import SchedulerRunResponse, SchedulerRunStatus
from app.repositories.reviews import ReviewPersistenceError
from tests.conftest import (
    FakeCaptureRepository,
    FakeClassifier,
    FakeTelegramClient,
)

SCHEDULER_SECRET = "test-scheduler-secret-at-least-32-characters"
SCHEDULER_HEADERS = {
    "X-Brain-Dump-Scheduler-Secret": SCHEDULER_SECRET,
    "X-CloudScheduler-JobName": "projects/test/locations/test/jobs/morning",
    "X-CloudScheduler-ScheduleTime": "2026-08-19T00:00:00Z",
}


class FakeRunner:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[object] = []
        self.error = error

    async def run(self, **kwargs: object) -> SchedulerRunResponse:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SchedulerRunResponse(
            status=SchedulerRunStatus.DELIVERED,
            window=ReviewWindow.MORNING,
            item_count=2,
            last_surfaced_recorded=True,
        )


@asynccontextmanager
async def endpoint_client(settings: object, runner: FakeRunner) -> AsyncIterator[httpx.AsyncClient]:
    application = create_app(
        settings=settings,  # type: ignore[arg-type]
        telegram_client=FakeTelegramClient(),
        capture_repository=FakeCaptureRepository(),
        classifier=FakeClassifier(),
        scheduled_review_service=runner,
    )
    async with application.router.lifespan_context(application), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Brain-Dump-Scheduler-Secret": "x" * 40},
    ],
)
async def test_scheduler_auth_rejects_before_work(
    settings: object,
    headers: dict[str, str],
) -> None:
    runner = FakeRunner()
    async with endpoint_client(settings, runner) as client:
        response = await client.post(
            "/internal/reviews/run",
            headers=headers,
            json={"slot": "morning"},
        )
    assert response.status_code == 403
    assert runner.calls == []


async def test_scheduler_auth_precedes_invalid_request_body(settings: object) -> None:
    runner = FakeRunner()
    async with endpoint_client(settings, runner) as client:
        response = await client.post(
            "/internal/reviews/run",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 403
    assert runner.calls == []


async def test_valid_scheduler_request_calls_runner(settings: object) -> None:
    runner = FakeRunner()
    async with endpoint_client(settings, runner) as client:
        response = await client.post(
            "/internal/reviews/run",
            headers=SCHEDULER_HEADERS,
            json={"slot": "morning"},
        )
    assert response.status_code == 200
    assert response.json() == {
        "status": "delivered",
        "window": "Morning",
        "item_count": 2,
        "last_surfaced_recorded": True,
    }
    assert len(runner.calls) == 1


async def test_missing_execution_identity_is_bad_request_after_auth(settings: object) -> None:
    runner = FakeRunner()
    async with endpoint_client(settings, runner) as client:
        response = await client.post(
            "/internal/reviews/run",
            headers={"X-Brain-Dump-Scheduler-Secret": SCHEDULER_SECRET},
            json={"slot": "morning"},
        )
    assert response.status_code == 400
    assert runner.calls == []


async def test_scheduler_body_is_strict_after_auth(settings: object) -> None:
    runner = FakeRunner()
    async with endpoint_client(settings, runner) as client:
        response = await client.post(
            "/internal/reviews/run",
            headers=SCHEDULER_HEADERS,
            json={"slot": "evening", "window": "Weekend"},
        )
    assert response.status_code == 422
    assert runner.calls == []


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (TelegramDeliveryError("failed"), "Review could not be delivered"),
        (ReviewPersistenceError("failed"), "Review could not be generated"),
    ],
)
async def test_operational_failures_return_502(
    settings: object, error: Exception, detail: str
) -> None:
    runner = FakeRunner(error=error)
    async with endpoint_client(settings, runner) as client:
        response = await client.post(
            "/internal/reviews/run",
            headers=SCHEDULER_HEADERS,
            json={"slot": "morning"},
        )
    assert response.status_code == 502
    assert response.json() == {"detail": detail}


async def test_schedule_timestamp_is_parsed_but_not_used_as_execution_clock(
    settings: object,
) -> None:
    runner = FakeRunner()
    async with endpoint_client(settings, runner) as client:
        await client.post(
            "/internal/reviews/run",
            headers=SCHEDULER_HEADERS,
            json={"slot": "morning"},
        )
    identity = runner.calls[0]["identity"]  # type: ignore[index]
    assert identity.schedule_time == datetime(2026, 8, 19, tzinfo=UTC)
