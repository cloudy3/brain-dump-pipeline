import asyncio
from datetime import UTC, datetime

import pytest

from app.models.reviews import ReviewWindow
from app.models.scheduling import (
    ReviewDeliveryResult,
    SchedulerExecutionIdentity,
    SchedulerRunStatus,
    SchedulerSlot,
)
from app.services.scheduler import (
    InvalidSchedulerExecution,
    RecentRunGuard,
    ScheduledReviewService,
    resolve_review_window,
)


@pytest.mark.parametrize(
    ("when", "slot", "expected"),
    [
        (datetime(2026, 8, 17, 8, tzinfo=UTC), SchedulerSlot.MORNING, ReviewWindow.MORNING),
        (
            datetime(2026, 8, 17, 10, tzinfo=UTC),
            SchedulerSlot.AFTER_WORK,
            ReviewWindow.AFTER_WORK,
        ),
        (datetime(2026, 8, 17, 13, tzinfo=UTC), SchedulerSlot.EVENING, ReviewWindow.EVENING),
        (datetime(2026, 8, 22, 13, tzinfo=UTC), SchedulerSlot.EVENING, ReviewWindow.WEEKEND),
        (datetime(2026, 8, 23, 13, tzinfo=UTC), SchedulerSlot.EVENING, ReviewWindow.WEEKEND),
    ],
)
def test_slot_mapping(when: datetime, slot: SchedulerSlot, expected: ReviewWindow) -> None:
    assert resolve_review_window(slot, when) is expected


@pytest.mark.parametrize("slot", [SchedulerSlot.MORNING, SchedulerSlot.AFTER_WORK])
def test_weekend_rejects_non_evening_slots(slot: SchedulerSlot) -> None:
    with pytest.raises(InvalidSchedulerExecution):
        resolve_review_window(slot, datetime(2026, 8, 22, 8, tzinfo=UTC))


async def test_guard_coalesces_concurrent_and_completed_duplicates() -> None:
    guard = RecentRunGuard()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "sent"

    first = asyncio.create_task(guard.execute_once(run_key="same", operation=operation))
    await started.wait()
    second = asyncio.create_task(guard.execute_once(run_key="same", operation=operation))
    release.set()

    assert await first == ("sent", False)
    assert await second == ("sent", True)
    assert await guard.execute_once(run_key="same", operation=operation) == ("sent", True)
    assert calls == 1


async def test_guard_releases_failed_run_and_accepts_distinct_keys() -> None:
    guard = RecentRunGuard()
    calls = 0

    async def operation() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("delivery failed")
        return calls

    with pytest.raises(RuntimeError):
        await guard.execute_once(run_key="one", operation=operation)
    assert await guard.execute_once(run_key="one", operation=operation) == (2, False)
    assert await guard.execute_once(run_key="two", operation=operation) == (3, False)


async def test_guard_expires_and_bounds_completed_keys() -> None:
    now = 0.0
    guard = RecentRunGuard(
        retention_seconds=10,
        maximum_completed_runs=1,
        timer=lambda: now,
    )
    calls = 0

    async def operation() -> int:
        nonlocal calls
        calls += 1
        return calls

    await guard.execute_once(run_key="old", operation=operation)
    await guard.execute_once(run_key="new", operation=operation)
    assert await guard.execute_once(run_key="old", operation=operation) == (3, False)
    now = 11
    assert await guard.execute_once(run_key="old", operation=operation) == (4, False)


class FakeDeliverer:
    def __init__(self) -> None:
        self.calls: list[tuple[ReviewWindow, datetime]] = []

    async def deliver(
        self, *, window: ReviewWindow, execution_time: datetime
    ) -> ReviewDeliveryResult:
        self.calls.append((window, execution_time))
        return ReviewDeliveryResult(
            window=window,
            item_count=2,
            last_surfaced_recorded=True,
        )


async def test_scheduled_service_uses_identity_only_for_deduplication() -> None:
    execution = datetime(2026, 8, 22, 21, tzinfo=UTC)
    deliverer = FakeDeliverer()
    service = ScheduledReviewService(deliverer=deliverer, clock=lambda: execution)
    identity = SchedulerExecutionIdentity(
        job_name="projects/p/locations/l/jobs/evening",
        schedule_time=datetime(2025, 1, 1, tzinfo=UTC),
    )

    first = await service.run(slot=SchedulerSlot.EVENING, identity=identity)
    second = await service.run(slot=SchedulerSlot.EVENING, identity=identity)

    assert first.status is SchedulerRunStatus.DELIVERED
    assert second.status is SchedulerRunStatus.DUPLICATE
    assert first.window is ReviewWindow.WEEKEND
    assert deliverer.calls == [(ReviewWindow.WEEKEND, execution)]
