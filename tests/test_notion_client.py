import json

import httpx
import pytest
from pydantic import SecretStr

from app.integrations.notion import NotionObjectNotFoundError, NotionSDKGateway


async def test_notion_gateway_uses_current_version_and_expected_endpoints() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"id": "result"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = NotionSDKGateway(
        token=SecretStr("test-notion-token"),
        timeout_seconds=4,
        notion_version="2026-03-11",
        http_client=http_client,
    )
    try:
        await gateway.retrieve_database(database_id="database-id")
        await gateway.retrieve_data_source(data_source_id="data-source-id")
        await gateway.query_data_source(
            data_source_id="data-source-id",
            filter_={"property": "TelegramUpdateId", "number": {"equals": 1}},
            page_size=1,
            start_cursor="cursor-1",
        )
        await gateway.create_page(
            data_source_id="data-source-id",
            properties={"Title": {"title": []}},
        )
        await gateway.retrieve_page(page_id="page-id")
        await gateway.update_page(
            page_id="page-id",
            properties={"PurchaseFocus": {"checkbox": True}},
        )
        await gateway.update_page(page_id="page-id", in_trash=True)
    finally:
        await gateway.aclose()

    assert [request.url.path for request in requests] == [
        "/v1/databases/database-id",
        "/v1/data_sources/data-source-id",
        "/v1/data_sources/data-source-id/query",
        "/v1/pages",
        "/v1/pages/page-id",
        "/v1/pages/page-id",
        "/v1/pages/page-id",
    ]
    assert all(request.headers["notion-version"] == "2026-03-11" for request in requests)
    assert all(
        request.headers["authorization"] == "Bearer test-notion-token" for request in requests
    )
    query_body = json.loads(requests[2].content)
    assert query_body == {
        "filter": {"property": "TelegramUpdateId", "number": {"equals": 1}},
        "page_size": 1,
        "start_cursor": "cursor-1",
    }
    create_body = json.loads(requests[3].content)
    assert create_body["parent"] == {
        "type": "data_source_id",
        "data_source_id": "data-source-id",
    }
    update_body = json.loads(requests[5].content)
    assert update_body == {"properties": {"PurchaseFocus": {"checkbox": True}}}
    trash_body = json.loads(requests[6].content)
    assert trash_body == {"in_trash": True}
    assert "archived" not in trash_body


async def test_notion_gateway_exposes_safe_not_found_for_stale_pages() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "object": "error",
                "status": 404,
                "code": "object_not_found",
                "message": "Could not find page",
                "request_id": "request-1",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = NotionSDKGateway(
        token=SecretStr("test-notion-token"),
        timeout_seconds=4,
        notion_version="2026-03-11",
        http_client=http_client,
    )
    try:
        with pytest.raises(NotionObjectNotFoundError, match="not found"):
            await gateway.retrieve_page(page_id="missing-page")
    finally:
        await gateway.aclose()


async def test_notion_gateway_omits_filter_for_unstructured_query() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": []})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = NotionSDKGateway(
        token=SecretStr("test-notion-token"),
        timeout_seconds=4,
        notion_version="2026-03-11",
        http_client=http_client,
    )
    try:
        await gateway.query_data_source(
            data_source_id="data-source-id",
            filter_=None,
            page_size=100,
        )
    finally:
        await gateway.aclose()

    assert json.loads(requests[0].content) == {"page_size": 100}
