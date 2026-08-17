import logging
from datetime import UTC, datetime

import pytest

from app.integrations.gemini import GeminiRequestError, GeminiResponseError
from app.models.classification import (
    CaptureClassification,
    CaptureType,
    Confidence,
    Domain,
    ShoppingKind,
    SurfaceContext,
)
from app.services.classification import (
    FALLBACK_TITLE_MAX_LENGTH,
    CaptureClassifier,
)


class FakeGateway:
    def __init__(
        self,
        *,
        result: CaptureClassification | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, datetime]] = []

    async def classify(
        self,
        *,
        original_input: str,
        reference_datetime: datetime,
    ) -> CaptureClassification:
        self.calls.append((original_input, reference_datetime))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _classification() -> CaptureClassification:
    return CaptureClassification(
        title="Add dark mode support",
        type=CaptureType.IDEA,
        domain=Domain.PORTFOLIO,
        location=None,
        due=None,
        surface_context=SurfaceContext.EVENING,
        shopping_kind=ShoppingKind.NONE,
        confidence=Confidence.HIGH,
    )


async def test_valid_result_is_returned_with_singapore_reference() -> None:
    gateway = FakeGateway(result=_classification())
    classifier = CaptureClassifier(gateway=gateway)

    outcome = await classifier.classify(
        original_input="Add dark mode support to my portfolio",
        reference_datetime=datetime(2026, 8, 16, 1, 30, tzinfo=UTC),
    )

    assert outcome.classification == _classification()
    assert outcome.used_fallback is False
    assert gateway.calls[0][1].isoformat() == "2026-08-16T09:30:00+08:00"


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        GeminiRequestError(
            sdk_exception_type="ClientError",
            safe_message="api failed",
        ),
        GeminiResponseError("unsupported response"),
        RuntimeError("unexpected malformed output"),
    ],
)
async def test_every_gateway_failure_returns_safe_fallback(
    error: Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    personal_text = "  A private thought\nwith whitespace  "
    classifier = CaptureClassifier(gateway=FakeGateway(error=error))

    with caplog.at_level(logging.WARNING):
        outcome = await classifier.classify(
            original_input=personal_text,
            reference_datetime=datetime(2026, 8, 16, 9, 0).astimezone(),
        )

    assert outcome.used_fallback is True
    assert outcome.classification == CaptureClassification(
        title="A private thought with whitespace",
        type=CaptureType.THOUGHT,
        domain=Domain.PERSONAL,
        location=None,
        due=None,
        surface_context=SurfaceContext.ANYTIME,
        shopping_kind=ShoppingKind.NONE,
        confidence=Confidence.LOW,
    )
    assert personal_text not in " ".join(record.getMessage() for record in caplog.records)


async def test_fallback_title_is_bounded_without_changing_original() -> None:
    original = "word " * 100
    classifier = CaptureClassifier(gateway=FakeGateway(error=TimeoutError()))

    outcome = await classifier.classify(
        original_input=original,
        reference_datetime=datetime(2026, 8, 16, 9, 0, tzinfo=UTC),
    )

    assert len(outcome.classification.title) <= FALLBACK_TITLE_MAX_LENGTH
    assert outcome.classification.title.endswith("...")


async def test_naive_reference_datetime_is_rejected() -> None:
    classifier = CaptureClassifier(gateway=FakeGateway(result=_classification()))

    with pytest.raises(ValueError, match="timezone-aware"):
        await classifier.classify(
            original_input="Test",
            reference_datetime=datetime(2026, 8, 16, 9, 0),
        )
