"""Read-only preflight validation for the configured Brain Dump v2 target."""

import asyncio

from app.core.config import Settings
from app.integrations.notion import NotionSDKGateway
from app.repositories.notion import EXPECTED_DATABASE_NAME, NotionCaptureRepository


async def _validate() -> None:
    settings = Settings()  # type: ignore[call-arg]
    gateway = NotionSDKGateway(
        token=settings.notion_api_token,
        timeout_seconds=settings.notion_request_timeout_seconds,
        notion_version=settings.notion_api_version,
    )
    repository = NotionCaptureRepository(
        gateway=gateway,
        database_id=settings.notion_brain_dump_database_id,
        data_source_id=settings.notion_brain_dump_data_source_id,
    )
    try:
        await repository.validate()
    finally:
        await gateway.aclose()
    print(f"Validated isolated Notion target: {EXPECTED_DATABASE_NAME}")


if __name__ == "__main__":
    asyncio.run(_validate())
