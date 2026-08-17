# Local development

Phase 3 provides the FastAPI health check and secured Telegram webhook. Authorized
text messages are classified through Gemini structured output and stored in the
isolated `Brain Dump v2` Notion database before the bot sends a compact confirmation.

## Prerequisites

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- A Telegram bot token for manual end-to-end testing
- A Gemini API key configured according to [the Gemini setup guide](gemini-setup.md)
- A `Brain Dump v2` database and internal Notion connection configured according to
  [the Notion setup guide](notion-setup.md)

## Setup

```bash
uv sync --extra test
cp .env.example .env
```

Replace the placeholder values in `.env`. The allowed user and chat IDs are integers.
For a direct conversation with the bot, they are normally the same value.

Before starting the service, run the read-only target and schema check:

```bash
uv run python -m app.tools.validate_notion
```

It must report `Validated isolated Notion target: Brain Dump v2`. This command reads
the configured database and data source but does not create or modify any page.

Start the development server:

```bash
uv run uvicorn app.main:app --reload
```

Check its health from another terminal:

```bash
curl http://127.0.0.1:8000/health
```

The expected response is:

```json
{"status":"ok"}
```

## Exercise the webhook locally

The following request validates the local request flow. Replace the IDs and secret
with the values from `.env`. It classifies the text, creates one page in `Brain Dump
v2`, then sends a Telegram acknowledgement, so the Gemini, Notion, and Telegram
credentials must be valid.

```bash
curl -X POST http://127.0.0.1:8000/webhooks/telegram \
  -H 'Content-Type: application/json' \
  -H 'X-Telegram-Bot-Api-Secret-Token: replace-with-your-secret' \
  -d '{
    "update_id": 1,
    "message": {
      "message_id": 1,
      "from": {"id": 123456789},
      "chat": {"id": 123456789},
      "text": "Test capture"
    }
  }'
```

Telegram must eventually be configured to send the same secret through its
`secret_token` webhook option. Public webhook setup and Cloud Run deployment are
intentionally deferred to Phase 7.

## Checks

The default tests use fakes and an in-memory HTTP transport. They never contact
Gemini, Telegram, or Notion and do not require real credentials.

```bash
uv run pytest
uv run ruff check .
```

To run the optional live classifier dataset without writing to Notion, see
[Gemini setup](gemini-setup.md#optional-live-evaluation).
