"""Pure deterministic eligibility, scoring, grouping, and review planning."""

import unicodedata
from collections import Counter
from datetime import date

from app.core.time import SINGAPORE_TIMEZONE
from app.models.classification import CaptureType, Domain, SurfaceContext
from app.models.reviews import (
    DeadlineUrgency,
    ReviewCandidateCriteria,
    ReviewEntry,
    ReviewItem,
    ReviewPlan,
    ReviewPolicy,
    ReviewRequest,
    ReviewWindow,
    RoutineShoppingGroup,
)
from app.repositories.reviews import ReviewRepository

_URGENCY_ORDER = {
    DeadlineUrgency.NONE: 0,
    DeadlineUrgency.LATER: 1,
    DeadlineUrgency.MODERATE: 2,
    DeadlineUrgency.STRONG: 3,
    DeadlineUrgency.TODAY: 4,
    DeadlineUrgency.OVERDUE: 5,
}


class ResurfacingService:
    """Build a read-only ReviewPlan without AI, Telegram, or persistence writes."""

    def __init__(
        self,
        *,
        repository: ReviewRepository,
        policy: ReviewPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._policy = policy or ReviewPolicy()

    async def build_plan(self, *, request: ReviewRequest) -> ReviewPlan:
        reference = request.reference_time.astimezone(SINGAPORE_TIMEZONE)
        reference_date = reference.date()
        candidates = await self._repository.list_candidates(
            criteria=ReviewCandidateCriteria(
                window=request.window,
                reference_date=reference_date,
            )
        )

        routine_items: list[ReviewItem] = []
        scored_entries: list[ReviewEntry] = []
        for item in candidates:
            eligibility = _eligibility(
                item=item,
                window=request.window,
                reference_date=reference_date,
                policy=self._policy,
            )
            if eligibility is None:
                continue
            urgency, cooldown_overridden = eligibility
            if item.is_routine_purchase:
                routine_items.append(item)
                continue
            score = review_score(
                item=item,
                window=request.window,
                reference_date=reference_date,
                urgency=urgency,
                cooldown_overridden=cooldown_overridden,
                policy=self._policy,
            )
            if score >= self._policy.minimum_score:
                scored_entries.append(ReviewEntry(item=item, score=score, urgency=urgency))

        entry_limit = self._entry_limit(request)
        entries = _select_entries(
            scored_entries,
            window=request.window,
            limit=entry_limit,
            policy=self._policy,
        )
        routine_group = self._routine_group(routine_items, request.window)
        return ReviewPlan(
            window=request.window,
            generated_at=reference,
            entries=tuple(entries),
            routine_shopping=routine_group,
        )

    def _entry_limit(self, request: ReviewRequest) -> int:
        normal_limit = {
            ReviewWindow.MORNING: self._policy.morning_limit,
            ReviewWindow.AFTER_WORK: self._policy.after_work_task_limit,
            ReviewWindow.EVENING: self._policy.evening_limit,
            ReviewWindow.WEEKEND: self._policy.weekend_limit,
        }[request.window]
        return min(normal_limit, request.limit_override or normal_limit)

    def _routine_group(
        self,
        items: list[ReviewItem],
        window: ReviewWindow,
    ) -> RoutineShoppingGroup | None:
        if window is not ReviewWindow.AFTER_WORK or not items:
            return None
        ordered = sorted(
            items,
            key=lambda item: (
                item.created_at,
                _normalize(item.title),
                item.page_id,
            ),
        )
        visible = tuple(ordered[: self._policy.routine_shopping_limit])
        return RoutineShoppingGroup(
            items=visible,
            total_eligible_count=len(ordered),
        )


def deadline_urgency(item: ReviewItem, reference_date: date) -> DeadlineUrgency:
    """Classify a Task due date using deterministic Singapore date boundaries."""
    if item.type is not CaptureType.TASK or item.due is None:
        return DeadlineUrgency.NONE
    days = (item.due - reference_date).days
    if days < 0:
        return DeadlineUrgency.OVERDUE
    if days == 0:
        return DeadlineUrgency.TODAY
    if days <= 2:
        return DeadlineUrgency.STRONG
    if days <= 7:
        return DeadlineUrgency.MODERATE
    return DeadlineUrgency.LATER


def review_score(
    *,
    item: ReviewItem,
    window: ReviewWindow,
    reference_date: date,
    urgency: DeadlineUrgency,
    cooldown_overridden: bool,
    policy: ReviewPolicy,
) -> int:
    """Score an already eligible item with centralized readable weights."""
    score = (
        policy.exact_context_score
        if item.surface_context.value == window.value
        else policy.anytime_context_score
    )
    if item.is_planned_purchase:
        score += policy.planned_purchase_score
    elif item.type is CaptureType.TASK:
        score += policy.task_score
    elif item.type is CaptureType.IDEA:
        score += policy.idea_score
    elif item.type is CaptureType.THOUGHT:
        score += policy.thought_score
    else:
        score += policy.reference_score

    score += {
        DeadlineUrgency.NONE: 0,
        DeadlineUrgency.LATER: 0,
        DeadlineUrgency.MODERATE: policy.moderate_deadline_score,
        DeadlineUrgency.STRONG: policy.strong_deadline_score,
        DeadlineUrgency.TODAY: policy.today_deadline_score,
        DeadlineUrgency.OVERDUE: policy.overdue_deadline_score,
    }[urgency]

    age_days = _age_days(item, reference_date)
    age_buckets = min(age_days // policy.age_bucket_days, policy.maximum_age_buckets)
    score += age_buckets * policy.age_bucket_score
    if item.last_surfaced is None:
        score += policy.never_surfaced_score
    if item.is_planned_purchase and item.purchase_focus:
        score += policy.purchase_focus_score
    if cooldown_overridden:
        score += policy.urgent_cooldown_override_penalty
    score += _window_affinity(item, window, policy)
    return score


def _eligibility(
    *,
    item: ReviewItem,
    window: ReviewWindow,
    reference_date: date,
    policy: ReviewPolicy,
) -> tuple[DeadlineUrgency, bool] | None:
    if item.snoozed_until is not None and item.snoozed_until > reference_date:
        return None
    if not _matches_window(item, window):
        return None

    age_days = _age_days(item, reference_date)
    if (
        item.type is CaptureType.IDEA
        and not (item.is_routine_purchase or item.is_planned_purchase)
        and age_days < policy.idea_minimum_age_days
    ):
        return None
    if item.type is CaptureType.THOUGHT and age_days < policy.thought_minimum_age_days:
        return None
    if (
        item.is_planned_purchase
        and not item.purchase_focus
        and age_days < policy.planned_purchase_minimum_age_days
    ):
        return None

    urgency = deadline_urgency(item, reference_date)
    cooldown_overridden = False
    if item.last_surfaced is not None:
        days_since = (reference_date - item.last_surfaced).days
        if days_since <= 0:
            return None
        spacing = _spacing_days(item, policy)
        if days_since < spacing:
            if (
                item.type is CaptureType.TASK
                and not (item.is_routine_purchase or item.is_planned_purchase)
                and urgency in {DeadlineUrgency.TODAY, DeadlineUrgency.OVERDUE}
            ):
                cooldown_overridden = True
            else:
                return None
    return urgency, cooldown_overridden


def _matches_window(item: ReviewItem, window: ReviewWindow) -> bool:
    if item.is_routine_purchase:
        return (
            window is ReviewWindow.AFTER_WORK
            and item.surface_context is SurfaceContext.AFTER_WORK
        )
    if item.is_planned_purchase:
        return window in {ReviewWindow.EVENING, ReviewWindow.WEEKEND} and _compatible_context(
            item.surface_context, window
        )
    if window is ReviewWindow.MORNING:
        return item.type is CaptureType.TASK and item.surface_context is SurfaceContext.MORNING
    if window is ReviewWindow.AFTER_WORK:
        return (
            item.type is CaptureType.TASK
            and item.domain is not Domain.SHOPPING
            and item.surface_context is SurfaceContext.AFTER_WORK
        )
    if not _compatible_context(item.surface_context, window):
        return False
    return item.type in {
        CaptureType.TASK,
        CaptureType.IDEA,
        CaptureType.THOUGHT,
        CaptureType.REFERENCE,
    }


def _compatible_context(context: SurfaceContext, window: ReviewWindow) -> bool:
    return context is SurfaceContext.ANYTIME or context.value == window.value


def _spacing_days(item: ReviewItem, policy: ReviewPolicy) -> int:
    if item.is_routine_purchase:
        return policy.routine_shopping_spacing_days
    if item.is_planned_purchase:
        return policy.planned_purchase_spacing_days
    return {
        CaptureType.TASK: policy.task_spacing_days,
        CaptureType.IDEA: policy.idea_spacing_days,
        CaptureType.THOUGHT: policy.thought_spacing_days,
        CaptureType.REFERENCE: policy.reference_spacing_days,
    }[item.type]


def _age_days(item: ReviewItem, reference_date: date) -> int:
    created_date = item.created_at.astimezone(SINGAPORE_TIMEZONE).date()
    return max((reference_date - created_date).days, 0)


def _window_affinity(item: ReviewItem, window: ReviewWindow, policy: ReviewPolicy) -> int:
    if (
        window is ReviewWindow.EVENING
        and item.type is CaptureType.IDEA
        and item.domain in {Domain.PORTFOLIO, Domain.TECH, Domain.PERSONAL}
    ):
        return policy.evening_idea_affinity_score
    if window is ReviewWindow.WEEKEND:
        if item.domain is Domain.PERSONAL:
            return policy.weekend_personal_score
        if item.domain in {Domain.DATING, Domain.TRAVEL, Domain.PLACES}:
            return policy.weekend_activity_score
        if item.domain is Domain.PORTFOLIO:
            return policy.weekend_portfolio_score
    return 0


def _select_entries(
    entries: list[ReviewEntry],
    *,
    window: ReviewWindow,
    limit: int,
    policy: ReviewPolicy,
) -> list[ReviewEntry]:
    ordered = sorted(entries, key=_entry_sort_key)
    urgent = [entry for entry in ordered if _is_urgent_task(entry)]
    nonurgent = [entry for entry in ordered if not _is_urgent_task(entry)]
    selected: list[ReviewEntry] = []
    domain_counts: Counter[Domain] = Counter()

    for entry in urgent:
        if len(selected) >= limit:
            break
        if _category_cap_allows(entry, selected):
            selected.append(entry)
            domain_counts[entry.item.domain] += 1

    soft_cap = (
        policy.evening_domain_soft_cap
        if window is ReviewWindow.EVENING
        else policy.weekend_domain_soft_cap
        if window is ReviewWindow.WEEKEND
        else limit
    )
    deferred: list[ReviewEntry] = []
    for entry in nonurgent:
        if len(selected) >= limit:
            break
        if not _category_cap_allows(entry, selected):
            continue
        if domain_counts[entry.item.domain] >= soft_cap:
            deferred.append(entry)
            continue
        selected.append(entry)
        domain_counts[entry.item.domain] += 1

    for entry in deferred:
        if len(selected) >= limit:
            break
        if _category_cap_allows(entry, selected):
            selected.append(entry)
    return selected


def _entry_sort_key(entry: ReviewEntry) -> tuple[object, ...]:
    return (
        -entry.score,
        -_URGENCY_ORDER[entry.urgency],
        entry.item.due is None,
        entry.item.due or date.max,
        entry.item.created_at,
        entry.item.page_id,
    )


def _is_urgent_task(entry: ReviewEntry) -> bool:
    return entry.item.type is CaptureType.TASK and entry.urgency in {
        DeadlineUrgency.STRONG,
        DeadlineUrgency.TODAY,
        DeadlineUrgency.OVERDUE,
    }


def _category_cap_allows(entry: ReviewEntry, selected: list[ReviewEntry]) -> bool:
    if entry.item.is_planned_purchase:
        return not any(value.item.is_planned_purchase for value in selected)
    if entry.item.type is CaptureType.THOUGHT:
        return not any(value.item.type is CaptureType.THOUGHT for value in selected)
    return True


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
