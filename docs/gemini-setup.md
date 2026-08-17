# Gemini setup for Phase 3

Phase 3 uses the official [`google-genai`](https://googleapis.github.io/python-genai/)
Python SDK and Gemini structured output. The default model is
`gemini-3.5-flash-lite`.

## Create an API key

Create or select a Gemini API key in
[Google AI Studio](https://aistudio.google.com/app/apikey). Store it only in your
local `.env` or deployed secret configuration:

```dotenv
GEMINI_API_KEY=replace-with-your-gemini-api-key
GEMINI_MODEL=gemini-3.5-flash-lite
```

The request timeout is optional and defaults to 10 seconds:

```dotenv
GEMINI_REQUEST_TIMEOUT_SECONDS=10
```

Never commit the real key. The application makes one Gemini attempt per new capture;
it does not retry inside the webhook request.

## Structured classification

Gemini receives the original capture plus an explicit Asia/Singapore reference
timestamp. It returns a Pydantic-validated structure containing:

- Title
- Type
- Domain
- Location
- Due
- SurfaceContext
- ShoppingKind
- Confidence

Enums prevent the model from creating new taxonomy values. Relative dates such as
`tomorrow` are interpreted from the supplied timestamp, not the model's internal
notion of the current date. The original Telegram text is stored separately and
unchanged in `OriginalInput`.

The local Pydantic model rejects unknown fields. Its Gemini transport schema omits
only Pydantic's `additionalProperties` keyword because the Gemini
`response_schema` endpoint does not accept that keyword; the returned data is still
validated against the original strict model before persistence.

If the request times out, the API fails, or the structured response is invalid, the
capture is still stored with a locally derived title and these safe values:

```text
Type: Thought
Domain: Personal
Location: empty
Due: empty
SurfaceContext: Anytime
ShoppingKind: empty
Confidence: Low
```

## Automated tests

```bash
uv run pytest
uv run ruff check .
```

The default tests use fakes. They never call Gemini, Telegram, or Notion.

## Optional live evaluation

The checked-in fixture at `evals/classifier_cases.json` contains representative
captures and fixed Singapore timestamps. To deliberately send those cases to the
configured live Gemini model, run:

```bash
uv run python -m app.tools.evaluate_classifier --live
```

The `--live` flag is mandatory. The command loads `GEMINI_API_KEY`, `GEMINI_MODEL`,
and the timeout through the same settings definitions as the application. Semantic
fields (`type`, `domain`, `location`, `due`, `surface_context`, and `shopping_kind`)
determine whether a case matches. Expected variation in `title` and `confidence` is
reported as `VARIANT` rather than treated as a semantic failure.

Requests are spaced 4.2 seconds apart by default so the 20-case fixture stays within
a 15-requests-per-minute free-tier limit. This pacing applies only to the manual tool;
the Telegram webhook still performs one attempt without retries. A paid tier can opt
out with `--request-interval-seconds 0`. SDK failures include a secret-scrubbed
exception type and message. The command does not write to Notion or contact Telegram.
