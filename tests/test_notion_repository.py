import asyncio
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

import pytest

from app.integrations.notion import NotionIntegrationError, NotionResponse
from app.models.capture import CaptureInput, CaptureSaveStatus
from app.repositories.captures import CapturePersistenceError
from app.repositories.notion import (
    EXPECTED_PROPERTY_TYPES,
    EXPECTED_SELECT_OPTIONS,
    NotionCaptureRepository,
)
from tests.conftest import NOTION_DATA_SOURCE_ID, NOTION_DATABASE_ID


def _database_response() -> dict[str, Any]:
    return {
        "id": NOTION_DATABASE_ID,
        "title": [{"plain_text": "Brain Dump v2"}],
        "data_sources": [{"id": NOTION_DATA_SOURCE_ID, "name": "Brain Dump v2"}],
    }


def _data_source_response() -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for name, property_type in EXPECTED_PROPERTY_TYPES.items():
        property_schema: dict[str, Any] = {"type": property_type, property_type: {}}
        if name in EXPECTED_SELECT_OPTIONS:
            property_schema["select"] = {
                "options": [
                    {"name": option} for option in sorted(EXPECTED_SELECT_OPTIONS[name])
                ]
            }
        properties[name] = property_schema
    return {
        "id": NOTION_DATA_SOURCE_ID,
        "parent": {"type": "database_id", "database_id": NOTION_DATABASE_ID},
        "properties": properties,
    }


class FakeNotionGateway:
    def __init__(self) -> None:
        self.database = _database_response()
        self.data_source = _data_source_response()
        self.created_properties: list[Mapping[str, Any]] = []
        self.query_count = 0
        self.fail_operation: str | None = None

    async def retrieve_database(self, *, database_id: str) -> NotionResponse:
        self._fail_if("retrieve_database")
        assert database_id == NOTION_DATABASE_ID
        return self.database

    async def retrieve_data_source(self, *, data_source_id: str) -> NotionResponse:
        self._fail_if("retrieve_data_source")
        assert data_source_id == NOTION_DATA_SOURCE_ID
        return self.data_source

    async def query_data_source(
        self,
        *,
        data_source_id: str,
        filter_: Mapping[str, Any],
        page_size: int,
    ) -> NotionResponse:
        self._fail_if("query")
        assert data_source_id == NOTION_DATA_SOURCE_ID
        assert page_size == 1
        self.query_count += 1
        await asyncio.sleep(0)
        requested_ids = {
            condition["number"]["equals"]
            for condition in filter_["or"]
            if isinstance(condition, Mapping)
        }
        matches = []
        for properties in self.created_properties:
            stored_ids = {
                properties["TelegramUpdateId"]["number"],
                properties["TelegramMessageId"]["number"],
            }
            if requested_ids & stored_ids:
                matches.append({"id": "existing-page"})
        return {"results": matches[:1]}

    async def create_page(
        self,
        *,
        data_source_id: str,
        properties: Mapping[str, Any],
    ) -> NotionResponse:
        self._fail_if("create")
        assert data_source_id == NOTION_DATA_SOURCE_ID
        await asyncio.sleep(0)
        self.created_properties.append(properties)
        return {"id": "created-page"}

    def _fail_if(self, operation: str) -> None:
        if self.fail_operation == operation:
            raise NotionIntegrationError("safe fake failure")


@pytest.fixture
def gateway() -> FakeNotionGateway:
    return FakeNotionGateway()


@pytest.fixture
def repository(gateway: FakeNotionGateway) -> NotionCaptureRepository:
    return NotionCaptureRepository(
        gateway=gateway,
        database_id=NOTION_DATABASE_ID,
        data_source_id=NOTION_DATA_SOURCE_ID,
    )


@pytest.fixture
def capture() -> CaptureInput:
    return CaptureInput(
        original_input="  Preserve this exactly\nincluding whitespace  ",
        telegram_update_id=9001,
        telegram_message_id=42,
    )


async def test_successful_persistence_uses_safe_defaults_and_exact_input(
    repository: NotionCaptureRepository,
    gateway: FakeNotionGateway,
    capture: CaptureInput,
) -> None:
    result = await repository.save_if_new(capture)

    assert result is CaptureSaveStatus.CREATED
    assert len(gateway.created_properties) == 1
    properties = gateway.created_properties[0]
    title = "".join(item["text"]["content"] for item in properties["Title"]["title"])
    original = "".join(
        item["text"]["content"] for item in properties["OriginalInput"]["rich_text"]
    )
    assert title == original == capture.original_input
    assert properties["Type"] == {"select": {"name": "Thought"}}
    assert properties["Domain"] == {"select": {"name": "Personal"}}
    assert properties["SurfaceContext"] == {"select": {"name": "Anytime"}}
    assert properties["Confidence"] == {"select": {"name": "Low"}}
    assert properties["PurchaseFocus"] == {"checkbox": False}
    assert properties["TelegramUpdateId"] == {"number": 9001}
    assert properties["TelegramMessageId"] == {"number": 42}


async def test_duplicate_capture_is_not_created_again(
    repository: NotionCaptureRepository,
    gateway: FakeNotionGateway,
    capture: CaptureInput,
) -> None:
    first = await repository.save_if_new(capture)
    second = await repository.save_if_new(capture)

    assert first is CaptureSaveStatus.CREATED
    assert second is CaptureSaveStatus.EXISTING
    assert len(gateway.created_properties) == 1
    assert gateway.query_count == 2


async def test_concurrent_duplicate_capture_is_serialized(
    repository: NotionCaptureRepository,
    gateway: FakeNotionGateway,
    capture: CaptureInput,
) -> None:
    results = await asyncio.gather(
        repository.save_if_new(capture),
        repository.save_if_new(capture),
    )

    assert sorted(results) == [CaptureSaveStatus.CREATED, CaptureSaveStatus.EXISTING]
    assert len(gateway.created_properties) == 1


@pytest.mark.parametrize(
    "operation",
    ["retrieve_database", "retrieve_data_source", "query", "create"],
)
async def test_notion_failure_becomes_capture_persistence_error(
    gateway: FakeNotionGateway,
    capture: CaptureInput,
    operation: str,
) -> None:
    gateway.fail_operation = operation
    repository = NotionCaptureRepository(
        gateway=gateway,
        database_id=NOTION_DATABASE_ID,
        data_source_id=NOTION_DATA_SOURCE_ID,
    )

    with pytest.raises(CapturePersistenceError):
        await repository.save_if_new(capture)

    assert gateway.created_properties == []


async def test_schema_validation_rejects_wrong_database_name(
    gateway: FakeNotionGateway,
) -> None:
    gateway.database["title"] = [{"plain_text": "Brain Dump"}]
    repository = NotionCaptureRepository(
        gateway=gateway,
        database_id=NOTION_DATABASE_ID,
        data_source_id=NOTION_DATA_SOURCE_ID,
    )

    with pytest.raises(CapturePersistenceError, match="Brain Dump v2"):
        await repository.validate()
    assert gateway.created_properties == []


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_property",
        "wrong_property_type",
        "wrong_select_options",
        "wrong_data_source_parent",
    ],
)
async def test_schema_validation_rejects_unsafe_target(
    gateway: FakeNotionGateway,
    mutation: str,
) -> None:
    gateway.data_source = deepcopy(gateway.data_source)
    if mutation == "missing_property":
        del gateway.data_source["properties"]["OriginalInput"]
    elif mutation == "wrong_property_type":
        gateway.data_source["properties"]["TelegramMessageId"]["type"] = "rich_text"
    elif mutation == "wrong_select_options":
        gateway.data_source["properties"]["Type"]["select"]["options"] = [
            {"name": "Task"}
        ]
    else:
        gateway.data_source["parent"]["database_id"] = "wrong-database-id"

    repository = NotionCaptureRepository(
        gateway=gateway,
        database_id=NOTION_DATABASE_ID,
        data_source_id=NOTION_DATA_SOURCE_ID,
    )
    with pytest.raises(CapturePersistenceError):
        await repository.validate()


async def test_long_input_is_chunked_without_losing_text(
    repository: NotionCaptureRepository,
    gateway: FakeNotionGateway,
) -> None:
    original = "a" * 2_001 + "\n" + "b" * 2_095
    capture = CaptureInput(
        original_input=original,
        telegram_update_id=9002,
        telegram_message_id=43,
    )

    await repository.save_if_new(capture)

    rich_text = gateway.created_properties[0]["OriginalInput"]["rich_text"]
    assert len(rich_text) == 3
    assert "".join(item["text"]["content"] for item in rich_text) == original
