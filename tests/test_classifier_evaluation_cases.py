from datetime import date

import pytest

from app.core.config import GeminiSettings, Settings
from app.integrations.gemini import GeminiRequestError
from app.models.classification import (
    CaptureClassification,
    Confidence,
    Domain,
    SurfaceContext,
)
from app.services.classification import CaptureClassifier
from app.tools.evaluate_classifier import (
    DEFAULT_REQUEST_INTERVAL_SECONDS,
    EvaluationCase,
    differing_fields,
    format_evaluation_error,
    load_cases,
)


class FixtureGateway:
    def __init__(self, result: CaptureClassification) -> None:
        self.result = result

    async def classify(self, **_: object) -> CaptureClassification:
        return self.result


def test_application_and_evaluator_share_gemini_settings_path() -> None:
    assert issubclass(Settings, GeminiSettings)
    assert GeminiSettings.model_config["env_file"] == ".env"
    assert Settings.model_config["env_file"] == ".env"
    assert (
        Settings.model_fields["gemini_model"].default
        == GeminiSettings.model_fields["gemini_model"].default
        == "gemini-3.5-flash-lite"
    )


def test_manual_evaluation_fixture_is_valid_and_complete() -> None:
    cases = load_cases()
    inputs = {case.input for case in cases}

    assert len(cases) == 20
    assert "Bring my power bank to work tomorrow" in inputs
    assert "Need milk and eggs" in inputs
    assert "I want to buy a new monitor eventually" in inputs
    assert "Library@Orchard is a nice place to chill" in inputs
    assert "Maybe do pottery for a date next month" in inputs
    assert "I think I prefer backend-heavy software engineering roles" in inputs
    assert all(case.reference_datetime.utcoffset() is not None for case in cases)


def test_manual_date_cases_use_fixed_singapore_expectations() -> None:
    cases = {case.name: case for case in load_cases()}

    assert cases["date_today"].expected.due == date(2026, 8, 16)
    assert cases["date_tomorrow"].expected.due == date(2026, 8, 17)
    assert cases["date_friday"].expected.due == date(2026, 8, 21)
    assert cases["date_next_monday"].expected.due == date(2026, 8, 24)
    assert cases["date_explicit_iso"].expected.due == date(2026, 9, 3)
    assert cases["date_absent"].expected.due is None


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case.name)
async def test_representative_mocked_classification_mapping(case: EvaluationCase) -> None:
    classifier = CaptureClassifier(gateway=FixtureGateway(case.expected))

    outcome = await classifier.classify(
        original_input=case.input,
        reference_datetime=case.reference_datetime,
    )

    assert outcome.classification == case.expected
    assert outcome.used_fallback is False


def test_manual_evaluator_exposes_safe_sdk_failure_detail() -> None:
    error = GeminiRequestError(
        sdk_exception_type="ClientError",
        safe_message="404 NOT_FOUND: model is unavailable",
    )

    diagnostic = format_evaluation_error(error)

    assert diagnostic == (
        "GeminiRequestError: ClientError: 404 NOT_FOUND: model is unavailable"
    )


def test_manual_evaluator_treats_title_and_confidence_as_advisory() -> None:
    expected = load_cases()[0].expected
    actual = expected.model_copy(
        update={
            "title": "Bring my power bank to work tomorrow",
            "confidence": Confidence.MEDIUM,
        }
    )

    differences = differing_fields(expected, actual)

    assert differences == {"title", "confidence"}


def test_manual_evaluator_identifies_semantic_differences() -> None:
    expected = load_cases()[0].expected
    actual = expected.model_copy(
        update={"domain": Domain.CAREER, "surface_context": SurfaceContext.ANYTIME}
    )

    differences = differing_fields(expected, actual)

    assert differences == {"domain", "surface_context"}


def test_default_manual_request_interval_respects_fifteen_request_quota() -> None:
    assert DEFAULT_REQUEST_INTERVAL_SECONDS * 15 >= 60
