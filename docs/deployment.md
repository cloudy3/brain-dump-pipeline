# Phase 7 deployment and scheduled delivery

Phase 7 deploys the FastAPI service to Cloud Run and invokes its protected review
endpoint from three Cloud Scheduler jobs. The service remains publicly reachable for
Telegram, while Telegram and Scheduler use separate application secrets. Scheduled
delivery reuses the deterministic Phase 6 engine and makes no Gemini call.

The commands use `asia-southeast1`, the Singapore region. Replace uppercase placeholders.

## Delivery contract

Cloud Scheduler sends `POST /internal/reviews/run` with a JSON slot (`morning`,
`afterwork`, or `evening`), `X-Brain-Dump-Scheduler-Secret`, and Scheduler's job-name and
schedule-time headers. The headers identify duplicate executions; they cannot override
the application's execution clock or request a `Weekend` window.

The actual execution time is converted to `Asia/Singapore`. Weekday slots map directly;
the evening slot maps to Weekend on Saturday and Sunday. Weekend morning/after-work calls
are rejected before Notion or Telegram work.

An empty plan sends and writes nothing. A non-empty plan sends one normal summary,
records `LastSurfaced` for every displayed item, then sends silent per-item Phase 4
action messages. Routine Shopping is grouped in the summary, but each displayed item is
individually actionable. Overflow is counted but is not marked surfaced.

The `LastSurfaced` batch starts only after Telegram accepts the summary and retries once
with the identical IDs/date. Final persistence failure is logged without failing or
resending the delivered review. A primary Telegram failure returns 502 and writes
nothing. Silent action-message failures are logged without changing delivery status.

`SCHEDULER_SECRET` is the only new required environment variable. No Notion schema
change is needed: `LastSurfaced` is already part of the validated Brain Dump v2 schema.

## Test the production container locally

Add a distinct secret of at least 32 characters to `.env`, then run:

```dotenv
SCHEDULER_SECRET=replace-with-a-distinct-long-random-secret
```

```bash
docker build -t brain-dump-pipeline:phase7 .
docker run --rm --env-file .env -p 8080:8080 brain-dump-pipeline:phase7
curl --fail http://127.0.0.1:8080/health
```

The expected body is `{"status":"ok"}`. The health check makes no external call.

## Prepare Google Cloud

```bash
export PROJECT_ID=YOUR_PROJECT_ID
export REGION=asia-southeast1
export SERVICE=brain-dump-pipeline
export RUNTIME_SERVICE_ACCOUNT=brain-dump-runtime

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com \
  cloudscheduler.googleapis.com
gcloud iam service-accounts create "$RUNTIME_SERVICE_ACCOUNT" \
  --display-name="Brain Dump Cloud Run runtime"
export RUNTIME_EMAIL="${RUNTIME_SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com"
```

Create independent secrets, then add their real values from standard input. For later
rotation use `gcloud secrets versions add NAME --data-file=-` again.

```bash
for SECRET_NAME in telegram-bot-token telegram-webhook-secret \
  brain-dump-scheduler-secret gemini-api-key notion-api-token
do
  gcloud secrets create "$SECRET_NAME" --replication-policy=automatic
done

read -rsp "Telegram bot token: " VALUE; printf '%s' "$VALUE" | \
  gcloud secrets versions add telegram-bot-token --data-file=-; unset VALUE
read -rsp "Telegram webhook secret: " VALUE; printf '%s' "$VALUE" | \
  gcloud secrets versions add telegram-webhook-secret --data-file=-; unset VALUE
read -rsp "Scheduler secret (32+ chars): " VALUE; printf '%s' "$VALUE" | \
  gcloud secrets versions add brain-dump-scheduler-secret --data-file=-; unset VALUE
read -rsp "Gemini API key: " VALUE; printf '%s' "$VALUE" | \
  gcloud secrets versions add gemini-api-key --data-file=-; unset VALUE
read -rsp "Notion API token: " VALUE; printf '%s' "$VALUE" | \
  gcloud secrets versions add notion-api-token --data-file=-; unset VALUE

for SECRET_NAME in telegram-bot-token telegram-webhook-secret \
  brain-dump-scheduler-secret gemini-api-key notion-api-token
do
  gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
    --member="serviceAccount:${RUNTIME_EMAIL}" \
    --role=roles/secretmanager.secretAccessor
done
```

## Deploy Cloud Run

```bash
export TELEGRAM_ALLOWED_USER_ID=123456789
export TELEGRAM_ALLOWED_CHAT_ID=123456789
export NOTION_DATABASE_ID=YOUR_BRAIN_DUMP_V2_DATABASE_ID
export NOTION_DATA_SOURCE_ID=YOUR_BRAIN_DUMP_V2_DATA_SOURCE_ID

gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --service-account "$RUNTIME_EMAIL" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 1 \
  --concurrency 4 \
  --timeout 120s \
  --cpu 1 \
  --memory 512Mi \
  --port 8080 \
  --set-env-vars="TELEGRAM_ALLOWED_USER_ID=${TELEGRAM_ALLOWED_USER_ID},TELEGRAM_ALLOWED_CHAT_ID=${TELEGRAM_ALLOWED_CHAT_ID},NOTION_BRAIN_DUMP_DATABASE_ID=${NOTION_DATABASE_ID},NOTION_BRAIN_DUMP_DATA_SOURCE_ID=${NOTION_DATA_SOURCE_ID},LOG_LEVEL=INFO" \
  --set-secrets="TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,TELEGRAM_WEBHOOK_SECRET=telegram-webhook-secret:latest,SCHEDULER_SECRET=brain-dump-scheduler-secret:latest,GEMINI_API_KEY=gemini-api-key:latest,NOTION_API_TOKEN=notion-api-token:latest" \
  --startup-probe="httpGet.path=/health,httpGet.port=8080,initialDelaySeconds=0,timeoutSeconds=2,periodSeconds=5,failureThreshold=12" \
  --liveness-probe="httpGet.path=/health,httpGet.port=8080,initialDelaySeconds=10,timeoutSeconds=2,periodSeconds=30,failureThreshold=3"

export SERVICE_URL="$(gcloud run services describe "$SERVICE" \
  --region "$REGION" --format='value(status.url)')"
curl --fail "${SERVICE_URL}/health"
```

Public access is required for Telegram; endpoint secrets plus the existing Telegram
user/chat guard remain mandatory. Minimum zero enables scale-to-zero. Maximum one,
concurrency four, and the Docker command's single worker preserve V1 process-local
deduplication while permitting a few overlapping Telegram callbacks.

## Register and verify Telegram

```bash
read -rsp "Telegram bot token: " TELEGRAM_BOT_TOKEN; echo
read -rsp "Telegram webhook secret: " TELEGRAM_WEBHOOK_SECRET; echo
curl --fail-with-body -X POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H 'Content-Type: application/json' \
  --data "{\"url\":\"${SERVICE_URL}/webhooks/telegram\",\"secret_token\":\"${TELEGRAM_WEBHOOK_SECRET}\",\"allowed_updates\":[\"message\",\"callback_query\"]}"
curl --fail-with-body \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
unset TELEGRAM_BOT_TOKEN TELEGRAM_WEBHOOK_SECRET
```

Confirm the returned URL, then send one normal capture. Verify one page appears only in
`Brain Dump v2` and the acknowledgement/actions arrive.

## Create three Scheduler jobs

These example clock times are configurable. All cron expressions explicitly use
`Asia/Singapore`. Zero automatic retries avoids repeated sends after ambiguous network
failure; inspect failures and invoke a job deliberately when appropriate.

```bash
read -rsp "Scheduler secret: " SCHEDULER_SECRET; echo

gcloud scheduler jobs create http brain-dump-morning \
  --location "$REGION" --schedule '0 8 * * 1-5' --time-zone Asia/Singapore \
  --uri "${SERVICE_URL}/internal/reviews/run" --http-method POST \
  --headers="Content-Type=application/json,X-Brain-Dump-Scheduler-Secret=${SCHEDULER_SECRET}" \
  --message-body='{"slot":"morning"}' --attempt-deadline=120s \
  --max-retry-attempts=0

gcloud scheduler jobs create http brain-dump-afterwork \
  --location "$REGION" --schedule '0 18 * * 1-5' --time-zone Asia/Singapore \
  --uri "${SERVICE_URL}/internal/reviews/run" --http-method POST \
  --headers="Content-Type=application/json,X-Brain-Dump-Scheduler-Secret=${SCHEDULER_SECRET}" \
  --message-body='{"slot":"afterwork"}' --attempt-deadline=120s \
  --max-retry-attempts=0

gcloud scheduler jobs create http brain-dump-evening \
  --location "$REGION" --schedule '0 21 * * *' --time-zone Asia/Singapore \
  --uri "${SERVICE_URL}/internal/reviews/run" --http-method POST \
  --headers="Content-Type=application/json,X-Brain-Dump-Scheduler-Secret=${SCHEDULER_SECRET}" \
  --message-body='{"slot":"evening"}' --attempt-deadline=120s \
  --max-retry-attempts=0
unset SCHEDULER_SECRET
```

Pause all jobs until smoke testing is complete:

```bash
for JOB in brain-dump-morning brain-dump-afterwork brain-dump-evening; do
  gcloud scheduler jobs pause "$JOB" --location "$REGION"
done
```

## Live smoke test

Run the job appropriate to the current Singapore review context:

```bash
gcloud scheduler jobs run brain-dump-evening --location "$REGION"
gcloud run services logs read "$SERVICE" --region "$REGION" --limit 100
```

Verify at most one audible summary, silent action cards, and today's Singapore
`LastSurfaced` date on displayed items only. Confirm no Gemini operation appears. The
existing `app.tools.preview_review` remains the read-only way to inspect selection at an
explicit test timestamp.

Resume after verification:

```bash
for JOB in brain-dump-morning brain-dump-afterwork brain-dump-evening; do
  gcloud scheduler jobs resume "$JOB" --location "$REGION"
done
```

## Logs, disabling, redeployment, and rollback

```bash
gcloud run services logs read "$SERVICE" --region "$REGION" --limit 200
gcloud scheduler jobs list --location "$REGION"
gcloud scheduler jobs describe brain-dump-evening --location "$REGION"

for JOB in brain-dump-morning brain-dump-afterwork brain-dump-evening; do
  gcloud scheduler jobs pause "$JOB" --location "$REGION"
done

gcloud run revisions list --service "$SERVICE" --region "$REGION"
gcloud run services update-traffic "$SERVICE" --region "$REGION" \
  --to-revisions PRIOR_REVISION=100
```

Redeploy by repeating `gcloud run deploy`. After rollback or redeployment, check health,
`getWebhookInfo`, one capture, and one deliberate Scheduler run before resuming jobs.

## Known V1 limitation

The service coalesces in-flight duplicates and retains up to 100 successful run keys for
72 hours using Scheduler job name plus scheduled timestamp. This protection is
process-local and is lost on restart, scale-to-zero, deployment, or crash. One instance
and one worker improve the guarantee but do not make it durable. Phase 7 deliberately
adds no Firestore, Redis, SQL database, migration ledger, or Phase 8 behavior.
