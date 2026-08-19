"""Read-only live preview of one deterministic proactive review plan."""

import argparse
import asyncio
from datetime import datetime

from app.core.config import Settings
from app.core.time import SINGAPORE_TIMEZONE
from app.integrations.notion import NotionSDKGateway
from app.models.reviews import ReviewRequest, ReviewWindow
from app.repositories.notion import NotionCaptureRepository
from app.services.resurfacing import ResurfacingService

_WINDOWS = {window.value.casefold(): window for window in ReviewWindow}


def parse_reference_time(value: str | None) -> datetime:
    if value is None:
        return datetime.now(SINGAPORE_TIMEZONE)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("--at must be an ISO 8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--at must include a UTC offset")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview a Phase 6 ReviewPlan without delivery or mutation."
    )
    parser.add_argument("--window", required=True, choices=sorted(_WINDOWS))
    parser.add_argument("--at", help="Aware ISO 8601 reference timestamp")
    return parser


async def preview(*, window: ReviewWindow, reference_time: datetime) -> str:
    settings = Settings()  # type: ignore[call-arg]
    gateway = NotionSDKGateway(
        token=settings.notion_api_token,
        timeout_seconds=settings.notion_request_timeout_seconds,
        notion_version=settings.notion_api_version,
    )
    try:
        repository = NotionCaptureRepository(
            gateway=gateway,
            database_id=settings.notion_brain_dump_database_id,
            data_source_id=settings.notion_brain_dump_data_source_id,
        )
        plan = await ResurfacingService(
            repository=repository,
            policy=settings.review_policy(),
        ).build_plan(
            request=ReviewRequest(window=window, reference_time=reference_time)
        )
        return plan.model_dump_json(indent=2)
    finally:
        await gateway.aclose()


def main() -> None:
    args = build_parser().parse_args()
    reference_time = parse_reference_time(args.at)
    print(
        asyncio.run(
            preview(
                window=_WINDOWS[args.window],
                reference_time=reference_time,
            )
        )
    )


if __name__ == "__main__":
    main()
