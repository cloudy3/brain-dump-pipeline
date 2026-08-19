"""Manual query routing, interpretation, deterministic matching, and formatting."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.core.time import SINGAPORE_TIMEZONE, singapore_date
from app.models.classification import CaptureType, Confidence, Domain, ShoppingKind
from app.models.queries import DueFilter, QueryCriteria, QueryItem, QueryPlan, QuerySort
from app.repositories.queries import QueryRepository

_CANDIDATE_PATTERN = re.compile(
    r"^(?:show|find|list|where|what\s+do\s+i|what\s+did\s+i|"
    r"what\b.{0,80}\b(?:do|did)\s+i|do\s+i\s+have|which|search|give\s+me\s+my|"
    r"(?:tasks?|ideas?|places?|purchases?)\s+(?:today|this\s+week|overdue|in|at|near|around))\b",
    re.IGNORECASE,
)
_FORCED_COMMAND_PATTERN = re.compile(
    r"^/(?:search|query)(?:@[A-Za-z0-9_]+)?(?:\s+(.+))?$", re.IGNORECASE
)


class QueryInterpreter(Protocol):
    async def interpret_query(
        self,
        *,
        original_input: str,
        reference_datetime: datetime,
    ) -> QueryPlan: ...


class QueryInterpretationError(RuntimeError):
    """Raised when an intentional query cannot be interpreted safely."""


@dataclass(frozen=True)
class QueryRequest:
    text: str | None = None
    plan: QueryPlan | None = None
    label: str | None = None


@dataclass(frozen=True)
class QueryResults:
    plan: QueryPlan
    items: list[QueryItem]
    label: str


def _shortcut_plan(**values: object) -> QueryPlan:
    return QueryPlan(confidence=Confidence.HIGH, limit=10, **values)


_SHORTCUTS: dict[str, tuple[str, QueryPlan]] = {
    "today": (
        "Tasks due today",
        _shortcut_plan(types=[CaptureType.TASK], due_filter=DueFilter.TODAY),
    ),
    "tasks": ("Tasks", _shortcut_plan(types=[CaptureType.TASK])),
    "ideas": ("Ideas", _shortcut_plan(types=[CaptureType.IDEA])),
    "portfolio": ("Portfolio", _shortcut_plan(domains=[Domain.PORTFOLIO])),
    "shopping": ("Shopping", _shortcut_plan(domains=[Domain.SHOPPING])),
    "planned purchases": (
        "Planned purchases",
        _shortcut_plan(domains=[Domain.SHOPPING], shopping_kind=ShoppingKind.PLANNED),
    ),
    "places": ("Places", _shortcut_plan(domains=[Domain.PLACES])),
    "date ideas": ("Date ideas", _shortcut_plan(domains=[Domain.DATING])),
    "travel": ("Travel", _shortcut_plan(domains=[Domain.TRAVEL])),
}
_COMMAND_SHORTCUTS = {
    "today": "today",
    "tasks": "tasks",
    "ideas": "ideas",
    "portfolio": "portfolio",
    "shopping": "shopping",
    "planned_purchases": "planned purchases",
    "places": "places",
    "date_ideas": "date ideas",
    "travel": "travel",
}


def detect_query_request(text: str) -> QueryRequest | None:
    """Conservatively distinguish intentional retrieval from normal capture."""
    compact = " ".join(text.split())
    if not compact:
        return None

    forced = _FORCED_COMMAND_PATTERN.fullmatch(compact)
    if forced is not None:
        query_text = forced.group(1)
        return QueryRequest(text=query_text or "")

    if compact.startswith("/"):
        parts = compact.split(maxsplit=1)
        command = parts[0][1:].split("@", maxsplit=1)[0].casefold()
        shortcut_key = _COMMAND_SHORTCUTS.get(command)
        if shortcut_key is not None and len(parts) == 1 and parts[0].count("/") == 1:
            label, plan = _SHORTCUTS[shortcut_key]
            return QueryRequest(plan=plan, label=label)
        return QueryRequest(text=parts[1] if len(parts) == 2 else "")

    shortcut = _SHORTCUTS.get(compact.casefold())
    if shortcut is not None:
        label, plan = shortcut
        return QueryRequest(plan=plan, label=label)
    if _CANDIDATE_PATTERN.match(compact) is not None:
        return QueryRequest(text=compact)
    return None


class ManualQueryService:
    def __init__(
        self,
        *,
        interpreter: QueryInterpreter,
        repository: QueryRepository,
    ) -> None:
        self._interpreter = interpreter
        self._repository = repository

    async def execute(
        self,
        *,
        request: QueryRequest,
        reference_datetime: datetime,
    ) -> QueryResults:
        if reference_datetime.tzinfo is None or reference_datetime.utcoffset() is None:
            raise ValueError("reference_datetime must be timezone-aware")
        singapore_reference = reference_datetime.astimezone(SINGAPORE_TIMEZONE)
        if request.plan is not None:
            plan = request.plan
        else:
            if not request.text:
                raise QueryInterpretationError("Query text is missing")
            try:
                plan = await self._interpreter.interpret_query(
                    original_input=request.text,
                    reference_datetime=singapore_reference,
                )
            except Exception as error:
                raise QueryInterpretationError("Query interpretation failed") from error
            if plan.confidence is Confidence.LOW:
                raise QueryInterpretationError("Query confidence is low")

        criteria = QueryCriteria(
            types=tuple(plan.types),
            domains=tuple(plan.domains),
            location=plan.location,
            shopping_kind=plan.shopping_kind,
            due_filter=plan.due_filter,
            reference_date=singapore_date(singapore_reference),
        )
        candidates = await self._repository.search(criteria=criteria)
        items = rank_query_items(candidates, plan)[: plan.limit]
        return QueryResults(
            plan=plan,
            items=items,
            label=request.label or query_label(plan),
        )


def normalize_search_text(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def rank_query_items(items: list[QueryItem], plan: QueryPlan) -> list[QueryItem]:
    keywords = [normalize_search_text(value) for value in plan.keywords]
    location = normalize_search_text(plan.location)
    matched: list[tuple[QueryItem, tuple[int, int, int, int, int]]] = []

    for item in items:
        title = normalize_search_text(item.title)
        item_location = normalize_search_text(item.location)
        original = normalize_search_text(item.original_input)
        fields = (title, item_location, original)
        if location and not any(location in field for field in fields):
            continue

        title_matches = sum(keyword in title for keyword in keywords)
        location_matches = sum(keyword in item_location for keyword in keywords)
        original_matches = sum(keyword in original for keyword in keywords)
        matched_count = sum(any(keyword in field for field in fields) for keyword in keywords)
        if keywords and matched_count == 0:
            continue
        full_phrase = " ".join(keywords)
        relevance = (
            matched_count,
            int(bool(full_phrase) and full_phrase in title),
            title_matches,
            location_matches,
            original_matches,
        )
        matched.append((item, relevance))

    if plan.sort is QuerySort.RELEVANCE:
        matched.sort(key=lambda value: value[0].page_id)
        matched.sort(key=lambda value: value[0].created_at, reverse=True)
        matched.sort(key=lambda value: value[1], reverse=True)
    elif plan.sort is QuerySort.NEWEST:
        matched.sort(key=lambda value: value[0].page_id)
        matched.sort(key=lambda value: value[0].created_at, reverse=True)
    elif plan.sort is QuerySort.OLDEST:
        matched.sort(key=lambda value: value[0].page_id)
        matched.sort(key=lambda value: value[0].created_at)
    else:
        matched.sort(key=lambda value: value[0].page_id)
        matched.sort(key=lambda value: value[0].created_at, reverse=True)
        matched.sort(key=lambda value: (value[0].due is None, value[0].due or datetime.max.date()))
    return [item for item, _ in matched]


def query_label(plan: QueryPlan) -> str:
    parts: list[str] = []
    if plan.location:
        parts.append(plan.location)
    if plan.domains:
        parts.extend(domain.value for domain in plan.domains[:2])
    if plan.types:
        parts.extend(type_.value for type_ in plan.types[:2])
    if plan.due_filter is not DueFilter.ANY:
        parts.append(plan.due_filter.value)
    return " · ".join(parts) or "Saved Brain Dump results"


def no_results_message(plan: QueryPlan) -> str:
    if plan.types == [CaptureType.TASK] and plan.due_filter is DueFilter.TODAY:
        return "No tasks are due today."
    terms = [*(domain.value for domain in plan.domains), *(type_.value for type_ in plan.types)]
    if plan.location:
        terms.append(plan.location)
    terms.extend(plan.keywords)
    description = " + ".join(terms)
    return (
        f"No saved Brain Dump items matched {description}."
        if description
        else "No saved Brain Dump items matched."
    )
