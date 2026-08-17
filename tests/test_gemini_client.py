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
    gemini_capture_response_schema,
)
from app.models.classification import CaptureClassification

VALID_RESPONSE = {
    "title": "Bring power bank to work",
    "type": "Task",
    "domain": "Personal",
    "location": None,
    "due": "2026-08-17",
    "surface_context": "Morning",
    "shopping_kind": "None",
    "confidence": "High",
}


class FakeModels:
    def __init__(self, *, parsed: object = VALID_RESPONSE, error: Exception | None = None):
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
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, models: FakeModels) -> None:
        self.aio = FakeAsyncClient(models)
        self.closed = False

    def close(self) -> None:
        self.closed = True


async def test_gateway_requests_structured_output_without_tools_or_afc() -> None:
    models = FakeModels()
    client = FakeClient(models)
    gateway = GeminiSDKClassificationGateway(
        api_key=SecretStr("test-key"),
        model="test-model",
        timeout_seconds=10,
        client=client,
    )
    reference = datetime(2026, 8, 16, 9, 0, tzinfo=ZoneInfo("Asia/Singapore"))

    result = await gateway.classify(
        original_input="Bring my power bank to work tomorrow",
        reference_datetime=reference,
    )

    assert result == CaptureClassification.model_validate(VALID_RESPONSE)
    call = models.calls[0]
    assert call["model"] == "test-model"
    payload = json.loads(call["contents"])
    assert payload == {
        "reference_datetime": "2026-08-16T09:00:00+08:00",
        "timezone": "Asia/Singapore",
        "original_input": "Bring my power bank to work tomorrow",
    }
    config = call["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema == gemini_capture_response_schema()
    assert config.response_schema["title"] == "CaptureClassification"
    assert config.response_schema["properties"]["type"]["$ref"] == (
        "#/$defs/CaptureType"
    )
    assert not _contains_key(config.response_schema, "additionalProperties")
    assert config.tools is None
    assert config.tool_config is None
    assert config.automatic_function_calling.disable is True
    assert "Never create another domain" in config.system_instruction
    assert "Add dark mode support to my portfolio" in config.system_instruction
    assert "Clean up my GitHub README" in config.system_instruction
    assert "interview question about optimistic locking" in config.system_instruction
    assert "Library@Orchard" in config.system_instruction
    assert "Call the clinic next Monday" in config.system_instruction
    assert "Organize my desk sometime" in config.system_instruction
    assert "Visit Hokkaido someday" in config.system_instruction


def _contains_key(value: object, target: str) -> bool:
    if isinstance(value, dict):
        return target in value or any(_contains_key(child, target) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, target) for child in value)
    return False


def test_gateway_configures_timeout_and_disables_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_client(**kwargs: Any) -> FakeClient:
        captured.update(kwargs)
        return FakeClient(FakeModels())

    monkeypatch.setattr("app.integrations.gemini.genai.Client", fake_client)

    GeminiSDKClassificationGateway(
        api_key=SecretStr("test-key"),
        model="test-model",
        timeout_seconds=7.5,
    )

    assert captured["api_key"] == "test-key"
    options = captured["http_options"]
    assert options.timeout == 7_500
    assert options.retry_options.attempts == 1


@pytest.mark.parametrize("parsed", [None, {"type": "Reminder"}])
async def test_gateway_rejects_invalid_or_unsupported_structured_response(
    parsed: object,
) -> None:
    gateway = GeminiSDKClassificationGateway(
        api_key=SecretStr("test-key"),
        model="test-model",
        timeout_seconds=10,
        client=FakeClient(FakeModels(parsed=parsed)),
    )

    with pytest.raises(GeminiResponseError):
        await gateway.classify(
            original_input="Test",
            reference_datetime=datetime.now(ZoneInfo("Asia/Singapore")),
        )


async def test_gateway_wraps_sdk_failure() -> None:
    sdk_error = TimeoutError("safe timeout")
    gateway = GeminiSDKClassificationGateway(
        api_key=SecretStr("test-key"),
        model="test-model",
        timeout_seconds=10,
        client=FakeClient(FakeModels(error=sdk_error)),
    )

    with pytest.raises(GeminiRequestError) as raised:
        await gateway.classify(
            original_input="Test",
            reference_datetime=datetime.now(ZoneInfo("Asia/Singapore")),
        )

    assert raised.value.__cause__ is sdk_error
    assert raised.value.sdk_exception_type == "TimeoutError"
    assert raised.value.safe_message == "safe timeout"


async def test_gateway_redacts_api_key_from_wrapped_sdk_failure() -> None:
    gateway = GeminiSDKClassificationGateway(
        api_key=SecretStr("secret-test-key"),
        model="test-model",
        timeout_seconds=10,
        client=FakeClient(
            FakeModels(error=RuntimeError("request failed?key=secret-test-key"))
        ),
    )

    with pytest.raises(GeminiRequestError) as raised:
        await gateway.classify(
            original_input="Test",
            reference_datetime=datetime.now(ZoneInfo("Asia/Singapore")),
        )

    assert "secret-test-key" not in raised.value.diagnostic
    assert "[REDACTED]" in raised.value.diagnostic


async def test_gateway_closes_sync_and_async_clients() -> None:
    client = FakeClient(FakeModels())
    gateway = GeminiSDKClassificationGateway(
        api_key=SecretStr("test-key"),
        model="test-model",
        timeout_seconds=10,
        client=client,
    )

    await gateway.aclose()

    assert client.aio.closed is True
    assert client.closed is True
