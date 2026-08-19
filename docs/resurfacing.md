# Deterministic resurfacing in Phase 6

Phase 6 selects useful active items from the configured Notion database named exactly
`Brain Dump v2`. It makes no Gemini, web-search, recommendation-service, Telegram, or
old-Brain-Dump calls. It produces a typed `ReviewPlan`; it does not deliver or mutate it.

Every review receives an aware reference timestamp and uses the Asia/Singapore calendar.
An empty plan is a successful result and Phase 7 should later send nothing for it.

## Review windows

| Window | Eligible content | Limit |
| --- | --- | ---: |
| Morning | Tasks with `SurfaceContext=Morning` only | 3 |
| AfterWork | AfterWork Tasks plus grouped Routine Shopping | 3 Tasks + 1 group |
| Evening | Evening/Anytime Tasks, Ideas, rare Thoughts, explicit proactive References, and at most one Planned Purchase | 3 |
| Weekend | Weekend/Anytime personal work, activities, Ideas, rare Thoughts, explicit proactive References, and at most one Planned Purchase | 8 |

Morning and AfterWork are strict: a due date cannot move a Task into the wrong context.
`OnDemand` References remain excluded. Evening and Weekend References require an explicit
compatible proactive context. Ideas must be three days old, Thoughts 30 days old, and
non-focused Planned purchases seven days old before they can enter scoring.

## Snooze and recent-surfacing behavior

`SnoozedUntil` later than the Singapore reference date is a hard exclusion. Equality is
the expiry boundary and permits eligibility. Expired values are not cleared.

Passive spacing is one day for Routine Shopping, two days for Tasks, seven days for
Ideas, 14 days for Planned purchases, and 30 days for Thoughts and References. Items
surfaced on the same Singapore date are excluded. A Task due today or overdue may bypass
its two-day spacing on a later date, with a score penalty, but cannot bypass context or
snooze rules.

These rules apply only to proactive reviews. Phase 5 manual queries intentionally ignore
`SnoozedUntil` and `LastSurfaced`, so active snoozed items remain searchable.

## Scoring

Hard eligibility runs before scoring. Eligible entries must reach 35 points.

* Context: exact `+20`, Anytime `+10`.
* Category: Task `+30`, Idea `+18`, Planned Purchase `+8`, Thought `-5`, Reference `-10`.
* Deadline: 3–7 days `+15`, 1–2 days `+35`, today `+60`, overdue `+70`.
* Age: three points per completed week, capped at 18.
* Never surfaced: `+12`.
* Focused Planned Purchase: `+25`.
* Urgent cooldown override: `-25`.
* Small deterministic Evening and Weekend domain-affinity bonuses favor appropriate
  Ideas and personal/activity content.

Ties use urgency, due date, creation time, and page ID. Urgent Tasks are selected before
diversity. Remaining entries use a two-per-domain Evening soft cap and three-per-domain
Weekend soft cap, followed by a deterministic backfill pass. Thought and Planned
Purchase caps remain absolute at one each.

## Shopping

Routine Shopping appears only AfterWork and never consumes a normal Task slot. Eligible
items sort by creation time, normalized title, then page ID. The group contains at most
10 items and records how many additional eligible items exist. Each item retains its own
page ID and Phase 4 metadata for individual Bought, Snooze, Delete, and Open actions.

Planned purchases appear only Evening or Weekend, compete with normal entries, and are
limited to one. `PurchaseFocus` adds weight but cannot bypass snooze or passive spacing.
The engine never focuses a purchase automatically.

## Read-only preview

Preview the current live v2 data explicitly:

```bash
uv run python -m app.tools.preview_review --window evening
uv run python -m app.tools.preview_review \
  --window weekend \
  --at 2026-08-22T19:00:00+08:00
```

The command prints JSON, sends no Telegram messages, makes no Gemini calls, and does not
update `LastSurfaced`, `SnoozedUntil`, or any other Notion property.

## Phase 7 boundary

Phase 7 can render the stable entries directly with the existing Phase 4 action-keyboard
builder. It must record `LastSurfaced` only for items whose Telegram delivery succeeds.
Selection and preview never record delivery, and a partial delivery must update only the
successfully delivered items.
