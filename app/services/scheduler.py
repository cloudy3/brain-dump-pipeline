"""Scheduler slot resolution and process-local duplicate suppression."""

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Protocol, TypeVar

from app.core.time import SINGAPORE_TIMEZONE
from app.models.reviews import ReviewWindow
from app.models.scheduling import (
    ReviewDeliveryResult,
    SchedulerExecutionIdentity,
    SchedulerRunResponse,
    SchedulerRunStatus,
    SchedulerSlot,
)

T = TypeVar("T")
logger = logging.getLogger(__name__)


class InvalidSchedulerExecution(ValueError):
    """Raised when a slot is not valid at the execution time."""


class ReviewDeliverer(Protocol):
    async def deliver(
        self,
        *,
        window: ReviewWindow,
        execution_time: datetime,
    ) -> ReviewDeliveryResult: ...


class ScheduledReviewRunner(Protocol):
    async def run(
        self,
        *,
        slot: SchedulerSlot,
        identity: SchedulerExecutionIdentity,
    ) -> SchedulerRunResponse: ...


@dataclass
class _RunEntry[T]:
    task: asyncio.Task[T]
    completed_at: float | None = None


class RecentRunGuard:
    """Coalesce in-flight runs and retain a small set of successful run keys."""

    def __init__(
        self,
        *,
        retention_seconds: float = 72 * 60 * 60,
        maximum_completed_runs: int = 100,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        self._retention_seconds = retention_seconds
        self._maximum_completed_runs = maximum_completed_runs
        self._timer = timer
        self._entries: OrderedDict[str, _RunEntry[object]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def execute_once(
        self,
        *,
        run_key: str,
        operation: Callable[[], Awaitable[T]],
    ) -> tuple[T, bool]:
        async with self._lock:
            self._prune()
            existing = self._entries.get(run_key)
            duplicate = existing is not None
            if existing is None:
                task = asyncio.create_task(operation())
                entry: _RunEntry[object] = _RunEntry(task=task)  # type: ignore[arg-type]
                self._entries[run_key] = entry
                task.add_done_callback(
                    lambda completed: asyncio.create_task(
                        self._settle(run_key, completed)  # type: ignore[arg-type]
                    )
                )
            else:
                task = existing.task  # type: ignore[assignment]

        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._settle(run_key, task)  # type: ignore[arg-type]
            raise

        await self._settle(run_key, task)  # type: ignore[arg-type]
        return result, duplicate

    async def _settle(self, run_key: str, task: asyncio.Task[object]) -> None:
        async with self._lock:
            current = self._entries.get(run_key)
            if current is None or current.task is not task:
                return
            if task.cancelled() or task.exception() is not None:
                self._entries.pop(run_key, None)
                return
            if current.completed_at is None:
                current.completed_at = self._timer()
                self._entries.move_to_end(run_key)
                self._prune()

    def _prune(self) -> None:
        now = self._timer()
        expired = [
            key
            for key, entry in self._entries.items()
            if entry.completed_at is not None
            and now - entry.completed_at >= self._retention_seconds
        ]
        for key in expired:
            self._entries.pop(key, None)

        completed = [key for key, value in self._entries.items() if value.completed_at is not None]
        for key in completed[: max(0, len(completed) - self._maximum_completed_runs)]:
            self._entries.pop(key, None)


class ScheduledReviewService:
    def __init__(
        self,
        *,
        deliverer: ReviewDeliverer,
        guard: RecentRunGuard | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._deliverer = deliverer
        self._guard = guard or RecentRunGuard()
        self._clock = clock or (lambda: datetime.now(SINGAPORE_TIMEZONE))

    async def run(
        self,
        *,
        slot: SchedulerSlot,
        identity: SchedulerExecutionIdentity,
    ) -> SchedulerRunResponse:
        execution_time = self._clock()
        if execution_time.tzinfo is None or execution_time.utcoffset() is None:
            raise ValueError("scheduler clock must return a timezone-aware datetime")
        window = resolve_review_window(slot, execution_time)

        async def deliver() -> ReviewDeliveryResult:
            return await self._deliverer.deliver(
                window=window,
                execution_time=execution_time,
            )

        result, duplicate = await self._guard.execute_once(
            run_key=identity.run_key,
            operation=deliver,
        )
        response = SchedulerRunResponse(
            status=SchedulerRunStatus.DUPLICATE if duplicate else result.status,
            window=result.window,
            item_count=result.item_count,
            last_surfaced_recorded=result.last_surfaced_recorded,
        )
        logger.info(
            "scheduled_review_completed",
            extra={
                "operation": "scheduled_review",
                "state": "success",
                "window": response.window.value,
                "delivery_status": response.status.value,
                "item_count": response.item_count,
                "last_surfaced_recorded": response.last_surfaced_recorded,
            },
        )
        return response


def resolve_review_window(slot: SchedulerSlot, execution_time: datetime) -> ReviewWindow:
    local_time = execution_time.astimezone(SINGAPORE_TIMEZONE)
    weekend = local_time.weekday() >= 5
    if slot is SchedulerSlot.EVENING:
        return ReviewWindow.WEEKEND if weekend else ReviewWindow.EVENING
    if weekend:
        raise InvalidSchedulerExecution(f"{slot.value} is not valid on weekends")
    if slot is SchedulerSlot.MORNING:
        return ReviewWindow.MORNING
    return ReviewWindow.AFTER_WORK
