import asyncio
from collections.abc import Mapping
from copy import deepcopy
from datetime import date
from typing import Any

import pytest

from app.integrations.notion import NotionIntegrationError, NotionResponse
from app.models.capture import CaptureInput, CaptureSaveStatus
from app.models.classification import (
    CaptureClassification,
    CaptureType,
    Confidence,
    Domain,
    ShoppingKind,
    SurfaceContext,
)
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
                matches.append({"id": "existing-page", "properties": properties})
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
        classification=CaptureClassification(
            title="Preserve this exactly",
            type=CaptureType.TASK,
            domain=Domain.PERSONAL,
            location="Singapore",
            due=date(2026, 8, 20),
            surface_context=SurfaceContext.MORNING,
            shopping_kind=ShoppingKind.NONE,
            confidence=Confidence.HIGH,
        ),
    )


async def test_successful_persistence_uses_classification_and_exact_input(
    repository: NotionCaptureRepository,
    gateway: FakeNotionGateway,
    capture: CaptureInput,
) -> None:
    result = await repository.save_if_new(capture)

    assert result.status is CaptureSaveStatus.CREATED
    assert len(gateway.created_properties) == 1
    properties = gateway.created_properties[0]
    title = "".join(item["text"]["content"] for item in properties["Title"]["title"])
    original = "".join(
        item["text"]["content"] for item in properties["OriginalInput"]["rich_text"]
    )
    assert title == "Preserve this exactly"
    assert original == capture.original_input
    assert properties["Type"] == {"select": {"name": "Task"}}
    assert properties["Domain"] == {"select": {"name": "Personal"}}
    assert properties["Location"] == {
        "rich_text": [{"type": "text", "text": {"content": "Singapore"}}]
    }
    assert properties["Due"] == {"date": {"start": "2026-08-20"}}
    assert properties["SurfaceContext"] == {"select": {"name": "Morning"}}
    assert "ShoppingKind" not in properties
    assert properties["Confidence"] == {"select": {"name": "High"}}
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

    assert first.status is CaptureSaveStatus.CREATED
    assert second.status is CaptureSaveStatus.EXISTING
    assert second.summary == first.summary
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

    assert {result.status for result in results} == {
        CaptureSaveStatus.CREATED,
        CaptureSaveStatus.EXISTING,
    }
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
        classification=CaptureClassification(
            title="Long capture",
            type=CaptureType.THOUGHT,
            domain=Domain.PERSONAL,
            location=None,
            due=None,
            surface_context=SurfaceContext.ANYTIME,
            shopping_kind=ShoppingKind.NONE,
            confidence=Confidence.LOW,
        ),
    )

    await repository.save_if_new(capture)

    rich_text = gateway.created_properties[0]["OriginalInput"]["rich_text"]
    assert len(rich_text) == 3
    assert "".join(item["text"]["content"] for item in rich_text) == original


async def test_routine_shopping_properties_are_persisted(
    repository: NotionCaptureRepository,
    gateway: FakeNotionGateway,
) -> None:
    capture = CaptureInput(
        original_input="Need milk and eggs",
        telegram_update_id=9003,
        telegram_message_id=44,
        classification=CaptureClassification(
            title="Buy milk and eggs",
            type=CaptureType.TASK,
            domain=Domain.SHOPPING,
            location=None,
            due=None,
            surface_context=SurfaceContext.AFTER_WORK,
            shopping_kind=ShoppingKind.ROUTINE,
            confidence=Confidence.HIGH,
        ),
    )

    await repository.save_if_new(capture)

    properties = gateway.created_properties[0]
    assert properties["ShoppingKind"] == {"select": {"name": "Routine"}}
    assert "Location" not in properties
    assert "Due" not in properties


async def test_lookup_returns_stored_confirmation_without_creating(
    repository: NotionCaptureRepository,
    capture: CaptureInput,
) -> None:
    created = await repository.save_if_new(capture)

    found = await repository.find_by_telegram_identity(
        telegram_update_id=capture.telegram_update_id,
        telegram_message_id=capture.telegram_message_id,
    )

    assert found == created.summary
