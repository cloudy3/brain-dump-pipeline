"""Small asynchronous adapter around the Notion Python SDK."""

from collections.abc import Mapping
from typing import Any, Protocol

import httpx
from notion_client import AsyncClient
from notion_client.errors import APIResponseError
from pydantic import SecretStr

NotionResponse = Mapping[str, Any]


class NotionIntegrationError(RuntimeError):
    """Raised when a Notion API operation fails."""


class NotionObjectNotFoundError(NotionIntegrationError):
    """Raised when Notion reports that a page does not exist or is inaccessible."""


class NotionGateway(Protocol):
    async def retrieve_database(self, *, database_id: str) -> NotionResponse: ...

    async def retrieve_data_source(self, *, data_source_id: str) -> NotionResponse: ...

    async def query_data_source(
        self,
        *,
        data_source_id: str,
        filter_: Mapping[str, Any] | None,
        page_size: int,
        start_cursor: str | None = None,
    ) -> NotionResponse: ...

    async def create_page(
        self,
        *,
        data_source_id: str,
        properties: Mapping[str, Any],
    ) -> NotionResponse: ...

    async def retrieve_page(self, *, page_id: str) -> NotionResponse: ...

    async def update_page(
        self,
        *,
        page_id: str,
        properties: Mapping[str, Any] | None = None,
        in_trash: bool | None = None,
    ) -> NotionResponse: ...


class NotionSDKGateway:
    """Translate the SDK surface into the small interface used by the repository."""

    def __init__(
        self,
        *,
        token: SecretStr,
        timeout_seconds: float,
        notion_version: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = AsyncClient(
            auth=token.get_secret_value(),
            timeout_ms=int(timeout_seconds * 1000),
            notion_version=notion_version,
            client=http_client,
        )

    async def retrieve_database(self, *, database_id: str) -> NotionResponse:
        return await self._call(self._client.databases.retrieve, database_id=database_id)

    async def retrieve_data_source(self, *, data_source_id: str) -> NotionResponse:
        return await self._call(
            self._client.data_sources.retrieve,
            data_source_id=data_source_id,
        )

    async def query_data_source(
        self,
        *,
        data_source_id: str,
        filter_: Mapping[str, Any] | None,
        page_size: int,
        start_cursor: str | None = None,
    ) -> NotionResponse:
        kwargs: dict[str, Any] = {
            "data_source_id": data_source_id,
            "page_size": page_size,
        }
        if filter_ is not None:
            kwargs["filter"] = filter_
        if start_cursor is not None:
            kwargs["start_cursor"] = start_cursor
        return await self._call(
            self._client.data_sources.query,
            **kwargs,
        )

    async def create_page(
        self,
        *,
        data_source_id: str,
        properties: Mapping[str, Any],
    ) -> NotionResponse:
        return await self._call(
            self._client.pages.create,
            parent={"type": "data_source_id", "data_source_id": data_source_id},
            properties=properties,
        )

    async def retrieve_page(self, *, page_id: str) -> NotionResponse:
        return await self._call(self._client.pages.retrieve, page_id=page_id)

    async def update_page(
        self,
        *,
        page_id: str,
        properties: Mapping[str, Any] | None = None,
        in_trash: bool | None = None,
    ) -> NotionResponse:
        kwargs: dict[str, Any] = {"page_id": page_id}
        if properties is not None:
            kwargs["properties"] = properties
        if in_trash is not None:
            kwargs["in_trash"] = in_trash
        return await self._call(self._client.pages.update, **kwargs)

    @staticmethod
    async def _call(operation: Any, **kwargs: Any) -> NotionResponse:
        try:
            return await operation(**kwargs)
        except APIResponseError as error:
            if error.status == 404:
                raise NotionObjectNotFoundError("Notion object not found") from error
            raise NotionIntegrationError("Notion API request failed") from error
        except Exception as error:
            raise NotionIntegrationError("Notion API request failed") from error

    async def aclose(self) -> None:
        await self._client.aclose()
