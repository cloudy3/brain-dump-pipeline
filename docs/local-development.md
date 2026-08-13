# Local development

Phase 1 provides the FastAPI health check and secured Telegram webhook. It sends a
temporary acknowledgement but does not persist messages yet.

## Prerequisites

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- A Telegram bot token for manual end-to-end testing

## Setup

```bash
uv sync --extra test
cp .env.example .env
```

Replace the placeholder values in `.env`. The allowed user and chat IDs are integers.
For a direct conversation with the bot, they are normally the same value.

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
with the values from `.env`. Because an authorized text update sends a Telegram API
acknowledgement, the bot token must also be valid.

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
Telegram and do not require real credentials.

```bash
uv run pytest
uv run ruff check .
```

