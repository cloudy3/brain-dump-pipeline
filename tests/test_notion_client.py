import json

import httpx
from pydantic import SecretStr

from app.integrations.notion import NotionSDKGateway


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
        )
        await gateway.create_page(
            data_source_id="data-source-id",
            properties={"Title": {"title": []}},
        )
    finally:
        await gateway.aclose()

    assert [request.url.path for request in requests] == [
        "/v1/databases/database-id",
        "/v1/data_sources/data-source-id",
        "/v1/data_sources/data-source-id/query",
        "/v1/pages",
    ]
    assert all(request.headers["notion-version"] == "2026-03-11" for request in requests)
    assert all(
        request.headers["authorization"] == "Bearer test-notion-token" for request in requests
    )
    query_body = json.loads(requests[2].content)
    assert query_body == {
        "filter": {"property": "TelegramUpdateId", "number": {"equals": 1}},
        "page_size": 1,
    }
    create_body = json.loads(requests[3].content)
    assert create_body["parent"] == {
        "type": "data_source_id",
        "data_source_id": "data-source-id",
    }
