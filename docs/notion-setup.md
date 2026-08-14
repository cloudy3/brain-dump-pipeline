# Notion setup for Phase 2

This application writes only to a separate database named exactly `Brain Dump v2`.
Do not rename, duplicate, migrate, share, or modify the existing personal Brain Dump.

## 1. Create the internal connection

In the [Notion Developer Portal](https://www.notion.so/profile/integrations), create
an internal connection for the workspace that will contain `Brain Dump v2`.

Use these capabilities for Phase 2:

- Read content
- Insert content

No comment or user-information capability is needed. Update content will be needed
later for item actions, but it is intentionally not required in Phase 2.

Copy the installation access token to `NOTION_API_TOKEN` in `.env`. Never commit or
paste the real token into documentation.

## 2. Create the isolated database

Create a new full-page Notion database named exactly:

```text
Brain Dump v2
```

Create it from scratch. Do not use the existing Brain Dump as a template or linked
data source.

Add the following properties with exact, case-sensitive names and types:

| Property | Notion type | Select options |
| --- | --- | --- |
| Title | Title | — |
| Type | Select | Task, Idea, Reference, Thought |
| Domain | Select | Personal, Portfolio, Tech, Shopping, Places, Dating, Travel, Career, Reservist |
| Location | Text | — |
| Due | Date | — |
| Created | Created time | — |
| OriginalInput | Text | — |
| SurfaceContext | Select | Morning, AfterWork, Evening, Weekend, OnDemand, Anytime |
| ShoppingKind | Select | Routine, Planned |
| PurchaseFocus | Checkbox | — |
| LastSurfaced | Date | — |
| SnoozedUntil | Date | — |
| Confidence | Select | High, Medium, Low |
| TelegramMessageId | Number | — |
| TelegramUpdateId | Number | — |

In the normal view, show only `Title`, `Type`, `Domain`, `Location`, `Due`, and
`Created`. Hide every system property. Hiding a property in a view does not remove it
from the database schema.

## 3. Grant narrowly scoped access

Open `Brain Dump v2`, use the page menu to add the internal connection, and grant it
access to this database only.

Do not add the connection to:

- the existing Brain Dump;
- a shared parent that also contains the existing Brain Dump; or
- the whole workspace when narrower content access is available.

The runtime validates both the configured database and data source before its first
write. It refuses to write when the database name is not exactly `Brain Dump v2`, the
data source belongs to another database, or the schema differs from the table above.

## 4. Copy the identifiers

The database ID is the 32-character identifier in the database URL. Store it as:

```dotenv
NOTION_BRAIN_DUMP_DATABASE_ID=replace-with-brain-dump-v2-database-id
```

To get the data source ID, open the database settings, choose **Manage data sources**,
open the data source's `•••` menu, and choose **Copy data source ID**. Store it as:

```dotenv
NOTION_BRAIN_DUMP_DATA_SOURCE_ID=replace-with-brain-dump-v2-data-source-id
```

The complete Phase 2 Notion configuration is:

```dotenv
NOTION_API_TOKEN=replace-with-your-notion-installation-access-token
NOTION_BRAIN_DUMP_DATABASE_ID=replace-with-brain-dump-v2-database-id
NOTION_BRAIN_DUMP_DATA_SOURCE_ID=replace-with-brain-dump-v2-data-source-id
NOTION_API_VERSION=2026-03-11
NOTION_REQUEST_TIMEOUT_SECONDS=10
```

## 5. Validate without writing

After filling in `.env`, run:

```bash
uv run python -m app.tools.validate_notion
```

Expected output:

```text
Validated isolated Notion target: Brain Dump v2
```

This check is read-only. Fix any reported name, access, identifier, property-type, or
select-option mismatch before sending a Telegram capture.
