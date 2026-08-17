"""Classification orchestration and reliable local fallback."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from app.integrations.gemini import ClassificationGateway
from app.models.classification import (
    CaptureClassification,
    CaptureType,
    Confidence,
    Domain,
    ShoppingKind,
    SurfaceContext,
)

SINGAPORE_TIMEZONE = ZoneInfo("Asia/Singapore")
FALLBACK_TITLE_MAX_LENGTH = 120

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClassificationOutcome:
    classification: CaptureClassification
    used_fallback: bool


class ClassificationService(Protocol):
    async def classify(
        self,
        *,
        original_input: str,
        reference_datetime: datetime,
    ) -> ClassificationOutcome: ...


class CaptureClassifier:
    """Classify captures and turn every model failure into safe metadata."""

    def __init__(self, *, gateway: ClassificationGateway) -> None:
        self._gateway = gateway

    async def classify(
        self,
        *,
        original_input: str,
        reference_datetime: datetime,
    ) -> ClassificationOutcome:
        if reference_datetime.tzinfo is None:
            raise ValueError("reference_datetime must be timezone-aware")
        singapore_reference = reference_datetime.astimezone(SINGAPORE_TIMEZONE)
        try:
            classification = await self._gateway.classify(
                original_input=original_input,
                reference_datetime=singapore_reference,
            )
        except Exception as error:
            logger.warning(
                "capture_classification_fallback",
                extra={
                    "operation": "capture_classification",
                    "state": "fallback",
                    "classification_status": "fallback",
                    "error_type": type(error).__name__,
                },
            )
            return ClassificationOutcome(
                classification=fallback_classification(original_input),
                used_fallback=True,
            )
        return ClassificationOutcome(classification=classification, used_fallback=False)


def fallback_classification(original_input: str) -> CaptureClassification:
    normalized = " ".join(original_input.split())
    if len(normalized) > FALLBACK_TITLE_MAX_LENGTH:
        normalized = normalized[: FALLBACK_TITLE_MAX_LENGTH - 3].rstrip() + "..."
    return CaptureClassification(
        title=normalized,
        type=CaptureType.THOUGHT,
        domain=Domain.PERSONAL,
        location=None,
        due=None,
        surface_context=SurfaceContext.ANYTIME,
        shopping_kind=ShoppingKind.NONE,
        confidence=Confidence.LOW,
    )
