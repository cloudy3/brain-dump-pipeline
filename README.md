# Brain Dump Pipeline

A personal AI-powered capture and resurfacing system built around Telegram, Gemini, Notion, and FastAPI.

The goal is simple:

> Capture thoughts in seconds, organize them automatically, and bring them back when they are useful.

## Problem

Traditional reminder and note-taking systems are easy to write into but easy to forget.

Ideas, tasks, places, purchases, and random thoughts often end up stored somewhere without a reliable way to bring them back into attention.

This creates a "write-only" idea graveyard.

Brain Dump Pipeline solves this by separating the workflow into four steps:

1. Capture
2. Understand
3. Store
4. Resurface

Telegram acts as the primary interaction layer.

Notion acts as the structured database and browsing layer.

The system does not rely on manually opening Notion to review saved information.

## Example

Send a Telegram message:

```text
Add an architecture diagram to my portfolio
```

The bot classifies and stores it:

```text
Saved · Idea · Portfolio

Add an architecture diagram to my portfolio
```

Later, during an evening review:

```text
Worth revisiting tonight

Portfolio Idea
Add an architecture diagram to my portfolio
Saved 18 days ago

Keep | Delete | Open
```

## Core Features

Planned V1 features include:

* Frictionless Telegram text capture
* AI-powered structured classification
* Automatic due-date extraction
* Automatic context detection
* Notion persistence
* Natural-language retrieval
* Location-aware queries against saved data
* Context-aware task reminders
* Adaptive idea resurfacing
* Grouped grocery reminders
* Planned-purchase tracking
* Telegram inline actions
* Deterministic resurfacing logic
* Single-user authorization
* Idempotent webhook processing
* Automated tests
* Google Cloud Run deployment

## Example Queries

The Telegram bot will support queries such as:

```text
show my portfolio ideas
```

```text
what do I need to do today?
```

```text
show things I want to buy
```

```text
show planned purchases
```

```text
show date ideas
```

```text
show travel ideas
```

```text
where to chill in Orchard
```

```text
where to chill and have dessert at Somerset
```

Queries only search personal Brain Dump data.

V1 does not search the public web for recommendations.

## Data Model

All entries live inside one Notion database.

### Type

```text
Task
Idea
Reference
Thought
```

### Domain

```text
Personal
Portfolio
Tech
Shopping
Places
Dating
Travel
Career
Reservist
```

### Surface Context

```text
Morning
AfterWork
Evening
Weekend
OnDemand
Anytime
```

An entry might look like:

```text
Title: Bring power bank to work
Type: Task
Domain: Personal
Surface Context: Morning
Due: Tomorrow
```

Another example:

```text
Title: Add View Transitions to portfolio
Type: Idea
Domain: Portfolio
Surface Context: Evening
```

## Context-Aware Resurfacing

Different information should appear at different times.

### Morning

Only actionable tasks relevant before leaving home or early in the day.

Examples:

```text
Bring power bank
Pack gym clothes
Take parcel to work
```

### After Work

Tasks suitable for completing after work.

Examples:

```text
Buy groceries
Collect parcel
Pass something to a friend
```

Routine shopping items are grouped into one reminder.

```text
Shopping

- Toothpaste
- Shampoo
- Coffee beans
```

### Evening

The main lightweight backlog review.

A maximum of three useful items are selected from areas such as:

* unfinished tasks
* portfolio ideas
* programming ideas
* personal ideas
* older thoughts
* planned purchases

### Weekend

A broader review focused on areas such as:

* personal projects
* portfolio work
* date ideas
* places
* travel
* non-urgent tasks
* older ideas

## Shopping Model

Shopping uses two different behaviors.

### Routine Purchases

Examples:

```text
Milk
Toothpaste
Shampoo
Coffee beans
```

These are grouped and resurfaced after work.

### Planned Purchases

Examples:

```text
Monitor
Headphones
Chair
Camera
```

These are intentionally low urgency.

Only one planned purchase should become the current focus at a time.

After a focused purchase is bought, remaining planned purchases enter a configurable cooldown before another item is resurfaced.

## AI Responsibilities

Gemini is used for interpretation.

Examples include:

* capture versus query intent
* title extraction
* type classification
* domain classification
* location extraction
* due-date extraction
* surface-context inference
* shopping classification
* natural-language query interpretation

Structured AI output is validated using Pydantic models.

## Deterministic Responsibilities

Important productivity behavior stays outside the LLM.

Application code controls:

* resurfacing eligibility
* deadline urgency
* resurfacing score
* notification limits
* cooldowns
* shopping grouping
* review windows
* snoozing
* idempotency

The LLM does not decide which three items should appear in a daily review.

This keeps the system predictable and testable.

## Resurfacing

Each eligible item receives a deterministic score based on signals such as:

```text
deadline urgency
age
context match
never surfaced bonus
focused purchase bonus
recently surfaced penalty
snooze state
item type
```

The highest-value eligible entries are selected for the current review window.

Sending no notification is considered a valid outcome.

## Telegram Actions

Planned item actions include:

```text
Done
Bought
Delete
Keep
Snooze
Focus
Open
```

### Keep

Means:

```text
I still care about this, but do not show it again for a while.
```

### Snooze

Defers an item to a specific future period.

### Focus

Marks the planned purchase currently being considered.

Only one planned purchase holds focus at a time.

## Architecture

```text
Telegram
    |
    v
FastAPI
    |
    +------ Capture ------> Gemini ------> Notion
    |
    +------ Query --------> Gemini ------> Notion
    |
    +------ Actions --------------------> Notion
    |
    +------ Scheduled Review
                 |
                 v
        Deterministic Resurfacing
                 |
                 v
              Telegram
```

Scheduled reviews are triggered through Google Cloud Scheduler.

The backend runs on Google Cloud Run.

## Tech Stack

Backend:

```text
Python
FastAPI
Pydantic
pytest
```

AI:

```text
Google Gemini API
Structured output
```

Storage:

```text
Notion API
```

Interface:

```text
Telegram Bot API
```

Infrastructure:

```text
Google Cloud Run
Google Cloud Scheduler
Docker
```

## Reliability

The capture flow is designed to avoid silent data loss.

```text
Telegram message
        |
        v
Validate sender
        |
        v
Check idempotency
        |
        v
Attempt AI classification
        |
        +---- Success ----> Structured entry
        |
        +---- Failure ----> Safe fallback entry
        |
        v
Persist to Notion
        |
        v
Send success confirmation
```

A success confirmation is only sent after persistence succeeds.

Telegram retries must not produce duplicate Notion entries.

## Security

The bot is designed for a single user.

V1 security includes:

* Telegram user allowlist
* Telegram chat validation
* webhook secret validation
* scheduler authentication
* environment-based secrets
* minimal personal-content logging
* no committed API keys

## Existing Notion Data

Development uses a separate new Notion database:

```text
Brain Dump v2
```

The existing personal Brain Dump remains untouched.

New Telegram captures go only into the new database.

Historical data migration happens later through a dedicated migration tool.

The migration process will support:

```text
Dry run
Classification
Duplicate detection
Import reporting
Final import
```

The original source data will not be modified during migration.

## Development Strategy

Development is incremental.

### Phase 1

Foundation and Telegram webhook

* FastAPI project
* configuration
* health endpoint
* Telegram webhook
* authentication
* basic tests

### Phase 2

Notion persistence

* Notion integration
* canonical schema
* capture persistence
* idempotency

### Phase 3

Gemini classification

* structured classification
* due-date extraction
* domain detection
* context detection
* fallback behavior

### Phase 4

Telegram actions

* Done
* Bought
* Delete
* Keep
* Snooze
* Focus
* Open

### Phase 5

Natural-language queries

* query interpretation
* filtering
* location queries
* Telegram result formatting

### Phase 6

Resurfacing engine

* deterministic scoring
* weekday reviews
* weekend reviews
* grocery grouping
* deadline behavior
* planned purchases

### Phase 7

Deployment and scheduling

* Cloud Run
* Docker
* Cloud Scheduler
* scheduled review endpoint

### Phase 8

Existing Notion migration

* dry-run migration
* classification
* duplicate handling
* import reporting

### Phase 9

Hardening

* reliability
* error handling
* logging
* security
* test coverage
* documentation

## V1 Non-Goals

The first version intentionally excludes:

* voice notes
* image capture
* RAG
* vector databases
* embeddings
* semantic duplicate merging
* calendar integration
* Apple Reminders synchronization
* GPS-triggered reminders
* web recommendations
* multi-user support
* custom web frontend
* complex AI agents
* AI-generated daily plans

These features should only be considered after the basic capture and resurfacing loop proves useful.

## Project Status

Work in progress.

Development follows an incremental phase-based approach, with each phase implemented and tested before moving to the next one.
