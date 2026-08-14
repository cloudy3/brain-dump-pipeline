"""Notion-backed capture persistence and schema validation."""

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from app.integrations.notion import NotionGateway, NotionIntegrationError, NotionResponse
from app.models.capture import CaptureInput, CaptureSaveStatus
from app.repositories.captures import CapturePersistenceError

logger = logging.getLogger(__name__)

EXPECTED_DATABASE_NAME = "Brain Dump v2"
RICH_TEXT_CHUNK_SIZE = 2_000

EXPECTED_PROPERTY_TYPES = {
    "Title": "title",
    "Type": "select",
    "Domain": "select",
    "Location": "rich_text",
    "Due": "date",
    "Created": "created_time",
    "OriginalInput": "rich_text",
    "SurfaceContext": "select",
    "ShoppingKind": "select",
    "PurchaseFocus": "checkbox",
    "LastSurfaced": "date",
    "SnoozedUntil": "date",
    "Confidence": "select",
    "TelegramMessageId": "number",
    "TelegramUpdateId": "number",
}

EXPECTED_SELECT_OPTIONS = {
    "Type": {"Task", "Idea", "Reference", "Thought"},
    "Domain": {
        "Personal",
        "Portfolio",
        "Tech",
        "Shopping",
        "Places",
        "Dating",
        "Travel",
        "Career",
        "Reservist",
    },
    "SurfaceContext": {
        "Morning",
        "AfterWork",
        "Evening",
        "Weekend",
        "OnDemand",
        "Anytime",
    },
    "ShoppingKind": {"Routine", "Planned"},
    "Confidence": {"High", "Medium", "Low"},
}


class NotionCaptureRepository:
    """Persist captures only after confirming the isolated v2 target and schema."""

    def __init__(
        self,
        *,
        gateway: NotionGateway,
        database_id: str,
        data_source_id: str,
    ) -> None:
        self._gateway = gateway
        self._database_id = database_id
        self._data_source_id = data_source_id
        self._validated = False
        self._validation_lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()

    async def validate(self) -> None:
        if self._validated:
            return

        async with self._validation_lock:
            if self._validated:
                return
            try:
                database = await self._gateway.retrieve_database(
                    database_id=self._database_id
                )
                data_source = await self._gateway.retrieve_data_source(
                    data_source_id=self._data_source_id
                )
                self._validate_target(database, data_source)
            except (NotionIntegrationError, CapturePersistenceError):
                self._log_failure("notion_schema_validation")
                raise
            except Exception as error:
                self._log_failure("notion_schema_validation")
                raise CapturePersistenceError("Notion schema validation failed") from error
            self._validated = True

    async def save_if_new(self, capture: CaptureInput) -> CaptureSaveStatus:
        try:
            await self.validate()
            async with self._save_lock:
                if await self._exists(capture):
                    return CaptureSaveStatus.EXISTING
                await self._gateway.create_page(
                    data_source_id=self._data_source_id,
                    properties=self._properties_for(capture),
                )
        except CapturePersistenceError:
            raise
        except NotionIntegrationError as error:
            self._log_failure("notion_capture_save")
            raise CapturePersistenceError("Notion capture persistence failed") from error
        except Exception as error:
            self._log_failure("notion_capture_save")
            raise CapturePersistenceError("Notion capture persistence failed") from error
        return CaptureSaveStatus.CREATED

    async def _exists(self, capture: CaptureInput) -> bool:
        response = await self._gateway.query_data_source(
            data_source_id=self._data_source_id,
            filter_={
                "or": [
                    {
                        "property": "TelegramUpdateId",
                        "number": {"equals": capture.telegram_update_id},
                    },
                    {
                        "property": "TelegramMessageId",
                        "number": {"equals": capture.telegram_message_id},
                    },
                ]
            },
            page_size=1,
        )
        results = response.get("results")
        if not isinstance(results, list):
            raise CapturePersistenceError("Notion returned an invalid query response")
        return bool(results)

    def _validate_target(
        self,
        database: NotionResponse,
        data_source: NotionResponse,
    ) -> None:
        if self._plain_text(database.get("title")) != EXPECTED_DATABASE_NAME:
            raise CapturePersistenceError(
                f"Configured Notion database must be named {EXPECTED_DATABASE_NAME!r}"
            )

        database_data_sources = database.get("data_sources")
        if not isinstance(database_data_sources, list) or not any(
            isinstance(item, Mapping)
            and self._same_id(item.get("id"), self._data_source_id)
            for item in database_data_sources
        ):
            raise CapturePersistenceError(
                "Configured Notion data source does not belong to Brain Dump v2"
            )

        parent = data_source.get("parent")
        if not isinstance(parent, Mapping) or not self._same_id(
            parent.get("database_id"), self._database_id
        ):
            raise CapturePersistenceError(
                "Configured Notion data source parent does not match Brain Dump v2"
            )

        properties = data_source.get("properties")
        if not isinstance(properties, Mapping):
            raise CapturePersistenceError("Notion data source has no readable schema")

        errors: list[str] = []
        for name, expected_type in EXPECTED_PROPERTY_TYPES.items():
            value = properties.get(name)
            if not isinstance(value, Mapping):
                errors.append(f"missing {name}")
            elif value.get("type") != expected_type:
                errors.append(f"{name} must be {expected_type}")

        for name, expected_options in EXPECTED_SELECT_OPTIONS.items():
            value = properties.get(name)
            select = value.get("select") if isinstance(value, Mapping) else None
            options = select.get("options") if isinstance(select, Mapping) else None
            if not isinstance(options, list):
                errors.append(f"{name} options are not readable")
                continue
            actual_options = {
                option.get("name")
                for option in options
                if isinstance(option, Mapping) and isinstance(option.get("name"), str)
            }
            if actual_options != expected_options:
                errors.append(f"{name} options do not match")

        if errors:
            raise CapturePersistenceError("Invalid Brain Dump v2 schema: " + "; ".join(errors))

    @staticmethod
    def _properties_for(capture: CaptureInput) -> dict[str, Any]:
        original_text = _rich_text(capture.original_input)
        return {
            "Title": {"title": original_text},
            "Type": {"select": {"name": "Thought"}},
            "Domain": {"select": {"name": "Personal"}},
            "OriginalInput": {"rich_text": original_text},
            "SurfaceContext": {"select": {"name": "Anytime"}},
            "PurchaseFocus": {"checkbox": False},
            "Confidence": {"select": {"name": "Low"}},
            "TelegramMessageId": {"number": capture.telegram_message_id},
            "TelegramUpdateId": {"number": capture.telegram_update_id},
        }

    @staticmethod
    def _plain_text(value: object) -> str:
        if not isinstance(value, list):
            return ""
        parts: list[str] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            plain_text = item.get("plain_text")
            if isinstance(plain_text, str):
                parts.append(plain_text)
                continue
            text = item.get("text")
            content = text.get("content") if isinstance(text, Mapping) else None
            if isinstance(content, str):
                parts.append(content)
        return "".join(parts)

    @staticmethod
    def _same_id(first: object, second: object) -> bool:
        return isinstance(first, str) and isinstance(second, str) and (
            first.replace("-", "").lower() == second.replace("-", "").lower()
        )

    @staticmethod
    def _log_failure(operation: str) -> None:
        logger.error(
            "notion_operation_failed",
            extra={"operation": operation, "state": "failure"},
        )


def _rich_text(value: str) -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": {"content": value[index : index + RICH_TEXT_CHUNK_SIZE]}}
        for index in range(0, len(value), RICH_TEXT_CHUNK_SIZE)
    ]
