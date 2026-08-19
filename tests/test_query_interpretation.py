import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr

from app.integrations.gemini import (
    GeminiRequestError,
    GeminiResponseError,
    GeminiSDKClassificationGateway,
    gemini_query_response_schema,
)
from app.models.queries import QueryPlan


class FakeModels:
    def __init__(self, parsed: object, error: Exception | None = None) -> None:
        self.parsed = parsed
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(parsed=self.parsed)


class FakeAsyncClient:
    def __init__(self, models: FakeModels) -> None:
        self.models = models

    async def aclose(self) -> None:
        return None


class FakeClient:
    def __init__(self, models: FakeModels) -> None:
        self.aio = FakeAsyncClient(models)

    def close(self) -> None:
        return None


CASES = [
    ("show portfolio ideas", ["Idea"], ["Portfolio"], None, [], "Any", "Relevance"),
    ("show programming ideas", ["Idea"], ["Tech"], None, [], "Any", "Relevance"),
    ("what do I need to do today", ["Task"], [], None, [], "Today", "Relevance"),
    ("show overdue tasks", ["Task"], [], None, [], "Overdue", "Relevance"),
    ("show planned purchases", [], ["Shopping"], "Planned", [], "Any", "Relevance"),
    ("what groceries do I need", [], ["Shopping"], "Routine", ["groceries"], "Any", "Relevance"),
    ("show date ideas", [], ["Dating"], None, [], "Any", "Relevance"),
    ("show travel ideas", ["Idea"], ["Travel"], None, [], "Any", "Relevance"),
    ("show places in Orchard", [], ["Places"], None, [], "Any", "Relevance"),
    (
        "where to chill and have dessert at Somerset",
        [],
        ["Places", "Dating"],
        None,
        ["chill", "dessert"],
        "Any",
        "Relevance",
    ),
    ("show old portfolio ideas", ["Idea"], ["Portfolio"], None, [], "Any", "Oldest"),
]


@pytest.mark.parametrize(
    ("text", "types", "domains", "shopping_kind", "keywords", "due_filter", "sort"),
    CASES,
)
async def test_mocked_structured_query_interpretations(
    text: str,
    types: list[str],
    domains: list[str],
    shopping_kind: str | None,
    keywords: list[str],
    due_filter: str,
    sort: str,
) -> None:
    location = "Somerset" if "Somerset" in text else "Orchard" if "Orchard" in text else None
    payload = {
        "types": types,
        "domains": domains,
        "location": location,
        "keywords": keywords,
        "shopping_kind": shopping_kind,
        "due_filter": due_filter,
        "sort": sort,
        "limit": 10,
        "confidence": "High",
    }
    models = FakeModels(payload)
    gateway = GeminiSDKClassificationGateway(
        api_key=SecretStr("test-key"),
        model="test-model",
        timeout_seconds=10,
        client=FakeClient(models),
    )
    reference = datetime(2026, 8, 19, 9, tzinfo=ZoneInfo("Asia/Singapore"))

    result = await gateway.interpret_query(original_input=text, reference_datetime=reference)

    assert result == QueryPlan.model_validate(payload)
    call = models.calls[0]
    assert json.loads(call["contents"]) == {
        "reference_datetime": "2026-08-19T09:00:00+08:00",
        "timezone": "Asia/Singapore",
        "original_input": text,
    }
    assert call["config"].response_schema == gemini_query_response_schema()
    assert call["config"].tools is None
    assert call["config"].automatic_function_calling.disable is True


@pytest.mark.parametrize("parsed", [None, {"types": ["Reminder"], "confidence": "High"}])
async def test_query_gateway_rejects_malformed_structured_output(parsed: object) -> None:
    gateway = GeminiSDKClassificationGateway(
        api_key=SecretStr("test-key"),
        model="test-model",
        timeout_seconds=10,
        client=FakeClient(FakeModels(parsed)),
    )

    with pytest.raises(GeminiResponseError):
        await gateway.interpret_query(
            original_input="show tasks",
            reference_datetime=datetime.now(ZoneInfo("Asia/Singapore")),
        )


async def test_query_gateway_wraps_api_failure_without_fallback() -> None:
    gateway = GeminiSDKClassificationGateway(
        api_key=SecretStr("test-key"),
        model="test-model",
        timeout_seconds=10,
        client=FakeClient(FakeModels({}, TimeoutError("safe timeout"))),
    )

    with pytest.raises(GeminiRequestError):
        await gateway.interpret_query(
            original_input="show tasks",
            reference_datetime=datetime.now(ZoneInfo("Asia/Singapore")),
        )
