"""Explicit, live-only natural-query interpretation evaluation."""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, TypeAdapter

from app.core.config import GeminiSettings
from app.integrations.gemini import GeminiRequestError, GeminiSDKClassificationGateway
from app.models.queries import QueryPlan

DEFAULT_FIXTURE = Path(__file__).resolve().parents[2] / "evals" / "query_cases.json"
DEFAULT_REQUEST_INTERVAL_SECONDS = 4.2
SEMANTIC_FIELDS = frozenset(
    {"types", "domains", "location", "keywords", "shopping_kind", "due_filter", "sort"}
)


class QueryEvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    input: str
    reference_datetime: datetime
    expected: QueryPlan


def load_cases(path: Path = DEFAULT_FIXTURE) -> list[QueryEvaluationCase]:
    return TypeAdapter(list[QueryEvaluationCase]).validate_json(path.read_text(encoding="utf-8"))


def differing_fields(expected: QueryPlan, actual: QueryPlan) -> set[str]:
    expected_data = expected.model_dump(mode="json")
    actual_data = actual.model_dump(mode="json")
    return {field for field in expected_data if expected_data[field] != actual_data[field]}


def format_evaluation_error(error: Exception) -> str:
    if isinstance(error, GeminiRequestError):
        return error.diagnostic
    return f"{type(error).__name__}: {error}"


async def evaluate(
    cases: list[QueryEvaluationCase],
    settings: GeminiSettings,
    *,
    request_interval_seconds: float = 0,
) -> int:
    gateway = GeminiSDKClassificationGateway(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.gemini_request_timeout_seconds,
    )
    semantic_matches = 0
    request_failures = 0
    try:
        for index, case in enumerate(cases):
            if index and request_interval_seconds:
                await asyncio.sleep(request_interval_seconds)
            try:
                actual = await gateway.interpret_query(
                    original_input=case.input,
                    reference_datetime=case.reference_datetime,
                )
            except Exception as error:
                request_failures += 1
                print(f"FAIL {case.name}: {format_evaluation_error(error)}")
                continue
            differences = differing_fields(case.expected, actual)
            semantic_differences = differences & SEMANTIC_FIELDS
            if not semantic_differences:
                semantic_matches += 1
                label = "PASS" if not differences else "VARIANT"
                print(f"{label} {case.name}")
                continue
            print(f"MISMATCH {case.name}: {', '.join(sorted(semantic_differences))}")
            print(
                "  expected="
                + json.dumps(case.expected.model_dump(mode="json"), sort_keys=True)
            )
            print("  actual=" + json.dumps(actual.model_dump(mode="json"), sort_keys=True))
    finally:
        await gateway.aclose()
    print(
        f"\n{semantic_matches}/{len(cases)} semantic matches; "
        f"{request_failures} request failures"
    )
    return int(semantic_matches != len(cases))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="confirm that this command may call the live Gemini API",
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument(
        "--request-interval-seconds",
        type=float,
        default=DEFAULT_REQUEST_INTERVAL_SECONDS,
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; the evaluation makes external Gemini calls")
    if args.request_interval_seconds < 0:
        parser.error("--request-interval-seconds cannot be negative")
    settings = GeminiSettings()
    raise SystemExit(
        asyncio.run(
            evaluate(
                load_cases(args.fixture),
                settings,
                request_interval_seconds=args.request_interval_seconds,
            )
        )
    )


if __name__ == "__main__":
    main()
