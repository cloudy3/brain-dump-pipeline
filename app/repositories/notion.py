"""Notion-backed capture persistence and schema validation."""

import asyncio
import logging
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from app.integrations.notion import (
    NotionGateway,
    NotionIntegrationError,
    NotionObjectNotFoundError,
    NotionResponse,
)
from app.models.actions import BrainDumpItem, normalize_page_id
from app.models.capture import (
    CaptureInput,
    CaptureSaveResult,
    CaptureSaveStatus,
    CaptureSummary,
)
from app.models.classification import (
    CaptureType,
    Confidence,
    Domain,
    ShoppingKind,
    SurfaceContext,
)
from app.models.queries import DueFilter, QueryCriteria, QueryItem
from app.models.reviews import ReviewCandidateCriteria, ReviewItem, ReviewWindow
from app.repositories.captures import CapturePersistenceError
from app.repositories.items import ItemPersistenceError
from app.repositories.queries import QueryPersistenceError
from app.repositories.reviews import ReviewPersistenceError

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
                database = await self._gateway.retrieve_database(database_id=self._database_id)
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

    async def find_by_telegram_identity(
        self,
        *,
        telegram_update_id: int,
        telegram_message_id: int,
    ) -> CaptureSummary | None:
        try:
            await self.validate()
            return await self._find(
                telegram_update_id=telegram_update_id,
                telegram_message_id=telegram_message_id,
            )
        except CapturePersistenceError:
            raise
        except NotionIntegrationError as error:
            self._log_failure("notion_capture_lookup")
            raise CapturePersistenceError("Notion capture lookup failed") from error
        except Exception as error:
            self._log_failure("notion_capture_lookup")
            raise CapturePersistenceError("Notion capture lookup failed") from error

    async def save_if_new(self, capture: CaptureInput) -> CaptureSaveResult:
        try:
            await self.validate()
            async with self._save_lock:
                existing = await self._find(
                    telegram_update_id=capture.telegram_update_id,
                    telegram_message_id=capture.telegram_message_id,
                )
                if existing is not None:
                    return CaptureSaveResult(
                        status=CaptureSaveStatus.EXISTING,
                        summary=existing,
                    )
                created_page = await self._gateway.create_page(
                    data_source_id=self._data_source_id,
                    properties=self._properties_for(capture),
                )
                summary = self._summary_for(capture, created_page)
        except CapturePersistenceError:
            raise
        except NotionIntegrationError as error:
            self._log_failure("notion_capture_save")
            raise CapturePersistenceError("Notion capture persistence failed") from error
        except Exception as error:
            self._log_failure("notion_capture_save")
            raise CapturePersistenceError("Notion capture persistence failed") from error
        return CaptureSaveResult(
            status=CaptureSaveStatus.CREATED,
            summary=summary,
        )

    async def get_by_id(self, *, page_id: str) -> BrainDumpItem | None:
        try:
            await self.validate()
            page = await self._retrieve_active_page(page_id)
            return self._item_from_page(page) if page is not None else None
        except ItemPersistenceError:
            raise
        except (CapturePersistenceError, NotionIntegrationError) as error:
            self._log_failure("notion_item_lookup")
            raise ItemPersistenceError("Notion item lookup failed") from error
        except Exception as error:
            self._log_failure("notion_item_lookup")
            raise ItemPersistenceError("Notion item lookup failed") from error

    async def trash(self, *, page_id: str) -> bool:
        try:
            await self.validate()
            if await self._retrieve_active_page(page_id) is None:
                return False
            try:
                await self._gateway.update_page(page_id=page_id, in_trash=True)
            except NotionObjectNotFoundError:
                return False
            return True
        except ItemPersistenceError:
            raise
        except (CapturePersistenceError, NotionIntegrationError) as error:
            self._log_failure("notion_item_trash")
            raise ItemPersistenceError("Notion item trash failed") from error
        except Exception as error:
            self._log_failure("notion_item_trash")
            raise ItemPersistenceError("Notion item trash failed") from error

    async def set_snoozed_until(self, *, page_id: str, value: date) -> bool:
        return await self._update_active_item(
            page_id=page_id,
            properties={"SnoozedUntil": {"date": {"start": value.isoformat()}}},
            operation="notion_item_snooze",
        )

    async def list_planned_purchases(self) -> list[BrainDumpItem]:
        try:
            await self.validate()
            items: list[BrainDumpItem] = []
            cursor: str | None = None
            while True:
                response = await self._gateway.query_data_source(
                    data_source_id=self._data_source_id,
                    filter_={
                        "and": [
                            {"property": "Domain", "select": {"equals": "Shopping"}},
                            {
                                "property": "ShoppingKind",
                                "select": {"equals": "Planned"},
                            },
                        ]
                    },
                    page_size=100,
                    start_cursor=cursor,
                )
                results = response.get("results")
                if not isinstance(results, list):
                    raise ItemPersistenceError("Notion returned an invalid item list")
                for page in results:
                    if not isinstance(page, Mapping):
                        raise ItemPersistenceError("Notion returned an invalid item page")
                    if page.get("in_trash") is not True:
                        items.append(self._item_from_page(page))
                if response.get("has_more") is not True:
                    break
                next_cursor = response.get("next_cursor")
                if not isinstance(next_cursor, str) or not next_cursor:
                    raise ItemPersistenceError("Notion returned an invalid pagination cursor")
                cursor = next_cursor
            return items
        except ItemPersistenceError:
            raise
        except (CapturePersistenceError, NotionIntegrationError) as error:
            self._log_failure("notion_planned_purchase_list")
            raise ItemPersistenceError("Notion planned purchase lookup failed") from error
        except Exception as error:
            self._log_failure("notion_planned_purchase_list")
            raise ItemPersistenceError("Notion planned purchase lookup failed") from error

    async def search(self, *, criteria: QueryCriteria) -> list[QueryItem]:
        """Return active v2 items after coarse structured Notion filtering."""
        try:
            await self.validate()
            items: list[QueryItem] = []
            cursor: str | None = None
            filter_ = self._query_filter(criteria)
            while True:
                response = await self._gateway.query_data_source(
                    data_source_id=self._data_source_id,
                    filter_=filter_,
                    page_size=100,
                    start_cursor=cursor,
                )
                results = response.get("results")
                if not isinstance(results, list):
                    raise QueryPersistenceError("Notion returned an invalid query result")
                for page in results:
                    if not isinstance(page, Mapping):
                        raise QueryPersistenceError("Notion returned an invalid query page")
                    if page.get("in_trash") is not True:
                        parent = page.get("parent")
                        if not isinstance(parent, Mapping) or not self._same_id(
                            parent.get("data_source_id"), self._data_source_id
                        ):
                            raise QueryPersistenceError(
                                "Query result is outside Brain Dump v2"
                            )
                        items.append(self._query_item_from_page(page))
                if response.get("has_more") is not True:
                    break
                next_cursor = response.get("next_cursor")
                if not isinstance(next_cursor, str) or not next_cursor:
                    raise QueryPersistenceError("Notion returned an invalid pagination cursor")
                cursor = next_cursor
            return items
        except QueryPersistenceError:
            raise
        except (CapturePersistenceError, NotionIntegrationError) as error:
            self._log_failure("notion_manual_query")
            raise QueryPersistenceError("Notion manual query failed") from error
        except Exception as error:
            self._log_failure("notion_manual_query")
            raise QueryPersistenceError("Notion manual query failed") from error

    async def list_candidates(
        self,
        *,
        criteria: ReviewCandidateCriteria,
    ) -> list[ReviewItem]:
        """Return active v2 candidates after coarse proactive-review filtering."""
        try:
            await self.validate()
            items: list[ReviewItem] = []
            cursor: str | None = None
            filter_ = self._review_filter(criteria)
            while True:
                response = await self._gateway.query_data_source(
                    data_source_id=self._data_source_id,
                    filter_=filter_,
                    page_size=100,
                    start_cursor=cursor,
                )
                results = response.get("results")
                if not isinstance(results, list):
                    raise ReviewPersistenceError("Notion returned an invalid review result")
                for page in results:
                    if not isinstance(page, Mapping):
                        raise ReviewPersistenceError("Notion returned an invalid review page")
                    if page.get("in_trash") is True:
                        continue
                    parent = page.get("parent")
                    if not isinstance(parent, Mapping) or not self._same_id(
                        parent.get("data_source_id"), self._data_source_id
                    ):
                        raise ReviewPersistenceError(
                            "Review candidate is outside Brain Dump v2"
                        )
                    items.append(self._review_item_from_page(page))
                if response.get("has_more") is not True:
                    break
                next_cursor = response.get("next_cursor")
                if not isinstance(next_cursor, str) or not next_cursor:
                    raise ReviewPersistenceError(
                        "Notion returned an invalid review pagination cursor"
                    )
                cursor = next_cursor
            return items
        except ReviewPersistenceError:
            raise
        except (CapturePersistenceError, NotionIntegrationError) as error:
            self._log_failure("notion_proactive_review")
            raise ReviewPersistenceError("Notion proactive review failed") from error
        except Exception as error:
            self._log_failure("notion_proactive_review")
            raise ReviewPersistenceError("Notion proactive review failed") from error

    async def record_last_surfaced(
        self,
        *,
        page_ids: tuple[str, ...],
        surfaced_on: date,
    ) -> None:
        """Idempotently update delivered active items in the guarded v2 target."""
        try:
            await self.validate()
            for page_id in dict.fromkeys(page_ids):
                page = await self._retrieve_active_page(page_id)
                if page is None:
                    logger.warning(
                        "notion_last_surfaced_item_unavailable",
                        extra={
                            "operation": "notion_last_surfaced_update",
                            "state": "skipped",
                        },
                    )
                    continue
                try:
                    await self._gateway.update_page(
                        page_id=normalize_page_id(page_id),
                        properties={
                            "LastSurfaced": {
                                "date": {"start": surfaced_on.isoformat()}
                            }
                        },
                    )
                except NotionObjectNotFoundError:
                    logger.warning(
                        "notion_last_surfaced_item_disappeared",
                        extra={
                            "operation": "notion_last_surfaced_update",
                            "state": "skipped",
                        },
                    )
        except ReviewPersistenceError:
            raise
        except (CapturePersistenceError, ItemPersistenceError, NotionIntegrationError) as error:
            self._log_failure("notion_last_surfaced_update")
            raise ReviewPersistenceError("Notion LastSurfaced update failed") from error
        except Exception as error:
            self._log_failure("notion_last_surfaced_update")
            raise ReviewPersistenceError("Notion LastSurfaced update failed") from error

    async def set_purchase_focus(self, *, page_id: str, focused: bool) -> bool:
        item = await self.get_by_id(page_id=page_id)
        if item is None:
            return False
        if not item.is_planned_purchase:
            raise ItemPersistenceError("PurchaseFocus requires a Planned shopping item")
        return await self._update_active_item(
            page_id=page_id,
            properties={"PurchaseFocus": {"checkbox": focused}},
            operation="notion_purchase_focus",
        )

    async def update_planned_purchase_state(
        self,
        *,
        page_id: str,
        snoozed_until: date,
        focused: bool,
    ) -> bool:
        item = await self.get_by_id(page_id=page_id)
        if item is None:
            return False
        if not item.is_planned_purchase:
            raise ItemPersistenceError("Planned purchase state requires a Planned item")
        return await self._update_active_item(
            page_id=page_id,
            properties={
                "SnoozedUntil": {"date": {"start": snoozed_until.isoformat()}},
                "PurchaseFocus": {"checkbox": focused},
            },
            operation="notion_planned_purchase_update",
        )

    async def _retrieve_active_page(self, page_id: str) -> NotionResponse | None:
        normalized_page_id = normalize_page_id(page_id)
        try:
            page = await self._gateway.retrieve_page(page_id=normalized_page_id)
        except NotionObjectNotFoundError:
            return None
        if page.get("in_trash") is True:
            return None
        parent = page.get("parent")
        if not isinstance(parent, Mapping) or not self._same_id(
            parent.get("data_source_id"), self._data_source_id
        ):
            raise ItemPersistenceError("Referenced page is outside Brain Dump v2")
        return page

    async def _update_active_item(
        self,
        *,
        page_id: str,
        properties: Mapping[str, Any],
        operation: str,
    ) -> bool:
        try:
            await self.validate()
            if await self._retrieve_active_page(page_id) is None:
                return False
            try:
                await self._gateway.update_page(
                    page_id=normalize_page_id(page_id),
                    properties=properties,
                )
            except NotionObjectNotFoundError:
                return False
            return True
        except ItemPersistenceError:
            raise
        except (CapturePersistenceError, NotionIntegrationError) as error:
            self._log_failure(operation)
            raise ItemPersistenceError("Notion item update failed") from error
        except Exception as error:
            self._log_failure(operation)
            raise ItemPersistenceError("Notion item update failed") from error

    async def _find(
        self,
        *,
        telegram_update_id: int,
        telegram_message_id: int,
    ) -> CaptureSummary | None:
        response = await self._gateway.query_data_source(
            data_source_id=self._data_source_id,
            filter_={
                "or": [
                    {
                        "property": "TelegramUpdateId",
                        "number": {"equals": telegram_update_id},
                    },
                    {
                        "property": "TelegramMessageId",
                        "number": {"equals": telegram_message_id},
                    },
                ]
            },
            page_size=1,
        )
        results = response.get("results")
        if not isinstance(results, list):
            raise CapturePersistenceError("Notion returned an invalid query response")
        if not results:
            return None
        page = results[0]
        if not isinstance(page, Mapping):
            raise CapturePersistenceError("Notion returned an invalid capture page")
        return self._item_from_page(page)

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
            isinstance(item, Mapping) and self._same_id(item.get("id"), self._data_source_id)
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
        classification = capture.classification
        properties: dict[str, Any] = {
            "Title": {"title": _rich_text(classification.title)},
            "Type": {"select": {"name": classification.type.value}},
            "Domain": {"select": {"name": classification.domain.value}},
            "OriginalInput": {"rich_text": _rich_text(capture.original_input)},
            "SurfaceContext": {"select": {"name": classification.surface_context.value}},
            "PurchaseFocus": {"checkbox": False},
            "Confidence": {"select": {"name": classification.confidence.value}},
            "TelegramMessageId": {"number": capture.telegram_message_id},
            "TelegramUpdateId": {"number": capture.telegram_update_id},
        }
        if classification.location is not None:
            properties["Location"] = {"rich_text": _rich_text(classification.location)}
        if classification.due is not None:
            properties["Due"] = {"date": {"start": classification.due.isoformat()}}
        if classification.shopping_kind is not ShoppingKind.NONE:
            properties["ShoppingKind"] = {"select": {"name": classification.shopping_kind.value}}
        return properties

    @staticmethod
    def _summary_for(
        capture: CaptureInput,
        page: NotionResponse,
    ) -> CaptureSummary:
        classification = capture.classification
        return CaptureSummary(
            page_id=normalize_page_id(_required_string(page, "id")),
            page_url=_required_https_url(page),
            title=classification.title,
            type=classification.type,
            domain=classification.domain,
            shopping_kind=classification.shopping_kind,
            purchase_focus=False,
            due=classification.due,
            snoozed_until=None,
            confidence=classification.confidence,
        )

    @classmethod
    def _item_from_page(cls, page: Mapping[str, Any]) -> BrainDumpItem:
        properties = page.get("properties")
        if not isinstance(properties, Mapping):
            raise CapturePersistenceError("Stored Notion capture has no readable properties")
        title_property = properties.get("Title")
        title_value = title_property.get("title") if isinstance(title_property, Mapping) else None
        title = cls._plain_text(title_value)
        type_name = cls._select_name(properties.get("Type"))
        domain_name = cls._select_name(properties.get("Domain"))
        confidence_name = cls._select_name(properties.get("Confidence"))
        shopping_kind_name = cls._select_name(properties.get("ShoppingKind"))
        try:
            return BrainDumpItem(
                page_id=normalize_page_id(_required_string(page, "id")),
                page_url=_required_https_url(page),
                title=title,
                type=CaptureType(type_name),
                domain=Domain(domain_name),
                shopping_kind=(
                    ShoppingKind(shopping_kind_name) if shopping_kind_name else ShoppingKind.NONE
                ),
                purchase_focus=cls._checkbox(properties.get("PurchaseFocus")),
                due=cls._date_value(properties.get("Due")),
                snoozed_until=cls._date_value(properties.get("SnoozedUntil")),
                confidence=Confidence(confidence_name),
            )
        except (ValueError, TypeError) as error:
            raise CapturePersistenceError(
                "Stored Notion capture has invalid item properties"
            ) from error

    @classmethod
    def _query_item_from_page(cls, page: Mapping[str, Any]) -> QueryItem:
        item = cls._item_from_page(page)
        properties = page.get("properties")
        if not isinstance(properties, Mapping):
            raise QueryPersistenceError("Stored Notion item has no readable properties")
        location_property = properties.get("Location")
        original_property = properties.get("OriginalInput")
        created_property = properties.get("Created")
        location_value = (
            location_property.get("rich_text") if isinstance(location_property, Mapping) else None
        )
        original_value = (
            original_property.get("rich_text") if isinstance(original_property, Mapping) else None
        )
        created_value = (
            created_property.get("created_time") if isinstance(created_property, Mapping) else None
        )
        if not isinstance(created_value, str):
            created_value = page.get("created_time")
        try:
            created_at = datetime.fromisoformat(created_value.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as error:
            raise QueryPersistenceError("Stored Notion item has invalid Created data") from error
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise QueryPersistenceError("Stored Notion item Created timestamp must be aware")
        location = cls._plain_text(location_value) or None
        return QueryItem(
            **item.model_dump(),
            location=location,
            original_input=cls._plain_text(original_value),
            created_at=created_at,
        )

    @classmethod
    def _review_item_from_page(cls, page: Mapping[str, Any]) -> ReviewItem:
        item = cls._item_from_page(page)
        properties = page.get("properties")
        if not isinstance(properties, Mapping):
            raise ReviewPersistenceError("Stored Notion item has no readable properties")
        created_property = properties.get("Created")
        created_value = (
            created_property.get("created_time") if isinstance(created_property, Mapping) else None
        )
        if not isinstance(created_value, str):
            created_value = page.get("created_time")
        try:
            created_at = datetime.fromisoformat(created_value.replace("Z", "+00:00"))
            surface_context = SurfaceContext(cls._select_name(properties.get("SurfaceContext")))
            last_surfaced = cls._date_value(properties.get("LastSurfaced"))
        except (AttributeError, TypeError, ValueError) as error:
            raise ReviewPersistenceError("Stored Notion review data is invalid") from error
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ReviewPersistenceError("Stored Notion Created timestamp must be aware")
        location_property = properties.get("Location")
        location_value = (
            location_property.get("rich_text") if isinstance(location_property, Mapping) else None
        )
        return ReviewItem(
            **item.model_dump(),
            surface_context=surface_context,
            created_at=created_at,
            last_surfaced=last_surfaced,
            location=cls._plain_text(location_value) or None,
        )

    @classmethod
    def _query_filter(cls, criteria: QueryCriteria) -> dict[str, Any] | None:
        conditions: list[dict[str, Any]] = []
        if criteria.types:
            conditions.append(cls._select_filter("Type", [value.value for value in criteria.types]))
        if criteria.domains:
            conditions.append(
                cls._select_filter("Domain", [value.value for value in criteria.domains])
            )
        if criteria.shopping_kind is not None:
            conditions.append(
                {
                    "property": "ShoppingKind",
                    "select": {"equals": criteria.shopping_kind.value},
                }
            )
        if criteria.location:
            conditions.append(
                {
                    "or": [
                        {
                            "property": "Location",
                            "rich_text": {"contains": criteria.location},
                        },
                        {"property": "Title", "title": {"contains": criteria.location}},
                        {
                            "property": "OriginalInput",
                            "rich_text": {"contains": criteria.location},
                        },
                    ]
                }
            )
        due = cls._due_filter(criteria)
        if due is not None:
            conditions.append(due)
        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"and": conditions}

    @classmethod
    def _review_filter(cls, criteria: ReviewCandidateCriteria) -> dict[str, Any]:
        window_branches: list[list[dict[str, Any]]]
        if criteria.window is ReviewWindow.MORNING:
            window_branches = [
                [
                    {"property": "Type", "select": {"equals": "Task"}},
                    {"property": "SurfaceContext", "select": {"equals": "Morning"}},
                ]
            ]
        elif criteria.window is ReviewWindow.AFTER_WORK:
            window_branches = [
                [
                    {"property": "Type", "select": {"equals": "Task"}},
                    {
                        "property": "SurfaceContext",
                        "select": {"equals": "AfterWork"},
                    },
                ],
                [
                    {"property": "Domain", "select": {"equals": "Shopping"}},
                    {
                        "property": "ShoppingKind",
                        "select": {"equals": "Routine"},
                    },
                ]
            ]
        else:
            contexts = [criteria.window.value, SurfaceContext.ANYTIME.value]
            window_branches = [
                [{"property": "SurfaceContext", "select": {"equals": context}}]
                for context in contexts
            ]
        snooze_conditions = [
            {"property": "SnoozedUntil", "date": {"is_empty": True}},
            {
                "property": "SnoozedUntil",
                "date": {"on_or_before": criteria.reference_date.isoformat()},
            },
        ]
        # DNF keeps compound filters at Notion's maximum nesting depth of two.
        return {
            "or": [
                {"and": [*window_branch, snooze_condition]}
                for window_branch in window_branches
                for snooze_condition in snooze_conditions
            ]
        }

    @staticmethod
    def _select_filter(property_name: str, values: list[str]) -> dict[str, Any]:
        filters = [{"property": property_name, "select": {"equals": value}} for value in values]
        return filters[0] if len(filters) == 1 else {"or": filters}

    @staticmethod
    def _due_filter(criteria: QueryCriteria) -> dict[str, Any] | None:
        today = criteria.reference_date
        property_name = "Due"
        if criteria.due_filter is DueFilter.ANY:
            return None
        if criteria.due_filter is DueFilter.TODAY:
            return {"property": property_name, "date": {"equals": today.isoformat()}}
        if criteria.due_filter is DueFilter.OVERDUE:
            return {"property": property_name, "date": {"before": today.isoformat()}}
        if criteria.due_filter is DueFilter.UPCOMING:
            return {"property": property_name, "date": {"after": today.isoformat()}}
        if criteria.due_filter is DueFilter.NO_DUE_DATE:
            return {"property": property_name, "date": {"is_empty": True}}
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        return {
            "and": [
                {"property": property_name, "date": {"on_or_after": monday.isoformat()}},
                {"property": property_name, "date": {"on_or_before": sunday.isoformat()}},
            ]
        }

    @staticmethod
    def _checkbox(value: object) -> bool:
        if not isinstance(value, Mapping):
            return False
        checkbox = value.get("checkbox")
        return checkbox if isinstance(checkbox, bool) else False

    @staticmethod
    def _date_value(value: object) -> date | None:
        if not isinstance(value, Mapping):
            return None
        date_property = value.get("date")
        if date_property is None:
            return None
        if not isinstance(date_property, Mapping):
            raise ValueError("invalid date property")
        start = date_property.get("start")
        if not isinstance(start, str):
            raise ValueError("invalid date start")
        return date.fromisoformat(start[:10])

    @staticmethod
    def _select_name(value: object) -> str:
        if not isinstance(value, Mapping):
            return ""
        select = value.get("select")
        if not isinstance(select, Mapping):
            return ""
        name = select.get("name")
        return name if isinstance(name, str) else ""

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
        return (
            isinstance(first, str)
            and isinstance(second, str)
            and (first.replace("-", "").lower() == second.replace("-", "").lower())
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


def _required_string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"Notion page has no valid {key}")
    return result


def _required_https_url(page: Mapping[str, Any]) -> str:
    url = _required_string(page, "url")
    if not url.startswith("https://"):
        raise ValueError("Notion page URL must use HTTPS")
    return url
