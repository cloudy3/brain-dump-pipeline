# Manual queries in Phase 5

Phase 5 searches only active pages in the configured Notion database named exactly
`Brain Dump v2`. It never searches the public web, calls a recommendation service,
or reads the old Brain Dump.

## Natural-language examples

Obvious retrieval messages are interpreted by Gemini using a strict `QueryPlan`:

```text
show portfolio ideas
show old portfolio ideas
what do I need to do today
show overdue tasks
what groceries do I need
show date ideas
show travel ideas
show places to chill in Orchard
where to chill and have dessert at Somerset
```

Gemini only converts the request into Type, Domain, Location, ShoppingKind, DueFilter,
Sort, keywords, limit, and confidence fields. It never receives database candidates and
does not rank individual saved items. Invalid or Low-confidence output produces a short
failure response and is never saved as a capture.

Routing is deliberately conservative. Retrieval phrases at the beginning of a message
are queries; ambiguous prose remains a capture. `/query <text>` and `/search <text>` can
force query interpretation.

## Deterministic shortcuts

These commands and exact text phrases bypass Gemini:

| Command | Exact phrase | Search |
| --- | --- | --- |
| `/today` | `Today` | Tasks due today |
| `/tasks` | `Tasks` | All tasks |
| `/ideas` | `Ideas` | All ideas |
| `/portfolio` | `Portfolio` | Portfolio domain |
| `/shopping` | `Shopping` | Shopping domain |
| `/planned_purchases` | `Planned Purchases` | Planned shopping |
| `/places` | `Places` | Places domain |
| `/date_ideas` | `Date Ideas` | Dating domain |
| `/travel` | `Travel` | Travel domain |

`Surprise Me` is not implemented because it belongs to Phase 6 resurfacing.

## Filtering and ranking

Notion applies coarse structured Type, Domain, ShoppingKind, Location/text, and Due
filters with full cursor pagination. Due boundaries are calculated from an injected
Asia/Singapore datetime. Trashed pages are excluded.

Local matching normalizes Title, Location, and OriginalInput using Unicode NFKC,
case-folding, whitespace collapsing, and trimming. Requested locations must occur in
one of those saved fields. When keywords exist, at least one must match. Relevance
prefers more distinct keyword matches, then a full phrase in Title, keyword matches in
Title, Location, and OriginalInput. Creation time and page ID make ties deterministic.

Manual retrieval intentionally ignores `SnoozedUntil`, `LastSurfaced`, Keep history,
and Phase 6 eligibility. An active item stays searchable until it is moved to Notion
trash.

## Telegram results and failures

The bot sends one heading followed by at most five compact actionable item messages.
Each item uses the same Phase 4 action keyboard as capture confirmations. The internal
query limit remains bounded from 1 through 20, while `TELEGRAM_QUERY_RESULT_LIMIT`
controls the displayed cap and defaults to 5.

No matches produce a contextual message without recommendations. Gemini or validation
failure produces an interpretation failure message. Notion failure produces a
saved-data search failure message. None of these paths writes a capture.

The optional fixed interpretation dataset can be run explicitly against live Gemini:

```bash
uv run python -m app.tools.evaluate_queries --live
```

This command is never part of pytest and refuses to run without `--live`.
