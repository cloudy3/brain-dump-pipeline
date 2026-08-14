"""Small asynchronous adapter around the Notion Python SDK."""

from collections.abc import Mapping
from typing import Any, Protocol

import httpx
from notion_client import AsyncClient
from pydantic import SecretStr

NotionResponse = Mapping[str, Any]


class NotionIntegrationError(RuntimeError):
    """Raised when a Notion API operation fails."""


class NotionGateway(Protocol):
    async def retrieve_database(self, *, database_id: str) -> NotionResponse: ...

    async def retrieve_data_source(self, *, data_source_id: str) -> NotionResponse: ...

    async def query_data_source(
        self,
        *,
        data_source_id: str,
        filter_: Mapping[str, Any],
        page_size: int,
    ) -> NotionResponse: ...

    async def create_page(
        self,
        *,
        data_source_id: str,
        properties: Mapping[str, Any],
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
        filter_: Mapping[str, Any],
        page_size: int,
    ) -> NotionResponse:
        return await self._call(
            self._client.data_sources.query,
            data_source_id=data_source_id,
            filter=filter_,
            page_size=page_size,
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

    @staticmethod
    async def _call(operation: Any, **kwargs: Any) -> NotionResponse:
        try:
            return await operation(**kwargs)
        except Exception as error:
            raise NotionIntegrationError("Notion API request failed") from error

    async def aclose(self) -> None:
        await self._client.aclose()
