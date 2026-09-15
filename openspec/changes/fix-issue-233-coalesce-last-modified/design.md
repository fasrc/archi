## Context

`PostgresCatalogService.upsert_resource`
(`src/data_manager/collectors/utils/catalog_postgres.py:197`) is the single write path for
the Postgres `documents` catalog. It issues one `INSERT ... ON CONFLICT (resource_hash) DO
UPDATE SET ...` statement. Keys present in module-level `_METADATA_COLUMN_MAP` (`:49`) are
promoted to real columns; everything else is folded into `extra_json`.

`last_modified` was promoted to its own column by #155 (`_METADATA_COLUMN_MAP:65`, DDL in
`src/cli/templates/init.sql`, migration
`src/cli/templates/migrations/add_documents_last_modified.sql`). It is also listed in the
metadata-rebuild key list (`:1395`) so it is readable after being written.

The value reaches the payload from exactly one producer today:
`ScraperManager._handle_standard_url` sets `resource.metadata["last_modified"] = lm`
(`scraper_manager.py:711`) — and only inside an `if lm is not None:` guard. So a page with
no available timestamp arrives with the key **absent**, `payload.get("last_modified")`
returns `None`, `_parse_timestamp(None)` yields `None`, and the conflict path writes that
`None` over whatever was stored.

Two facts bound the blast radius:

- The clause is inside a single SQL string literal. No control flow, no call signature, and
  no parameter tuple changes — the preserve decision is made by the database, not by Python.
- The SQLite `resources` backend (`index_utils.py`) has its own independent
  `_METADATA_COLUMN_MAP` and is untouched.

## Goals / Non-Goals

**Goals**: stop a timestamp-less re-ingest from clearing a stored `documents.last_modified`;
keep a supplied timestamp overwriting unconditionally; state the semantics where the next
caller will read them.

**Non-Goals**: backfilling rows already cleared (the data is gone and cannot be
reconstructed); any change to which pages are fetched or skipped; the SQLite backend; a
sentinel for explicit withdrawal (D3); generalising the rule to other columns (D4).

## Decisions

### D1 — COALESCE in the conflict clause, not a caller-side merge

```sql
last_modified = COALESCE(EXCLUDED.last_modified, documents.last_modified),
```

`EXCLUDED.last_modified` is the proposed row's value; `documents.last_modified` is the row
already stored. `COALESCE` takes the first non-NULL, so an absent incoming timestamp means
"no new information" and the stored value survives, while any supplied value wins.

The alternative — read the current row first and merge in Python — costs an extra round
trip, and is racy: two concurrent ingests of the same `resource_hash` could interleave
between the read and the write. Doing it in the `ON CONFLICT` clause keeps the whole upsert
a single atomic statement, which is what makes it correct under the concurrent collection
this codebase already runs (`tests/unit/test_persistence_concurrency.py`).

This is issue #233's recommended option 1.

### D2 — The pre-update row is referenced by table name

Inside `ON CONFLICT DO UPDATE SET`, an unqualified column on the right-hand side already
resolves to the existing row, but it reads ambiguously next to `EXCLUDED.`-qualified
siblings. The statement is `INSERT INTO documents` with no alias, so `documents` is the
correct and available qualifier. Writing `documents.last_modified` makes "old value vs
proposed value" explicit at the point a future reader will be scanning for it.

### D3 — No sentinel for explicit withdrawal (issue #233 option 2)

The accepted cost of D1 is that a page whose timestamp is *legitimately* withdrawn — a
sitemap that still lists the page but has dropped its `<lastmod>` — keeps the last value it
had. That is the safe direction to be wrong in: `last_modified` is a conservative change
signal, and a stale-but-present timestamp makes a future incremental re-ingest re-fetch a
page it might have skipped. Clearing it, by contrast, is silent data loss.

A sentinel would need a distinguished value threaded through the scraper, the metadata
dict, `_parse_timestamp`, and the column — real surface area for a case nobody has
observed. Deferred until a concrete need appears; it composes cleanly on top of this change
rather than conflicting with it.

### D4 — Scoped to `last_modified`, deliberately

The same shape exists for other nullable columns in the same clause (`url`, `ticket_id`,
`file_modified_at`, …), and they are NOT changed here. The distinction is what the column
means:

- `last_modified` is a **signal accumulated across ingests**. Absence in one ingest means
  "this ingest learned nothing new", not "the page has no timestamp".
- The others are **properties observed in the current ingest**. For those, absence
  legitimately means "not set as of now", and preserving a prior value would make the row
  disagree with the source it was just re-read from.

Blanket-COALESCE-ing the clause would therefore turn a correct overwrite into a silent
stale read for those columns, and would make it impossible to ever clear one. Stated here
because it is the first question a reviewer will ask.

### D5 — Verification under a mocked cursor

`tests/unit/test_catalog_postgres_upsert_last_modified.py` builds the service with
`__new__` and a `MagicMock` cursor, so the tests observe the emitted SQL string and the
params tuple — not real database behaviour. Preservation is therefore verified as "the
statement instructs Postgres to preserve", which is the honest limit of a unit test with no
live DB; `COALESCE` semantics themselves are Postgres's, not ours to re-test.

Two properties are asserted so a regression cannot pass by accident: the clause is the
COALESCE form (not the bare `EXCLUDED` assignment), **and** the params tuple still carries
`None` when the metadata omits the key — proving the preserve decision is made in SQL and
was not smuggled into Python by dropping the parameter.

## Risks / Trade-offs

- **Stale timestamp on legitimate withdrawal** — accepted, D3; the failure direction is a
  redundant re-fetch, not lost data.
- **Already-cleared rows stay NULL** — this change is forward-only. A row cleared by a past
  re-ingest reads `NULL` until some ingest supplies a timestamp for it again, at which point
  it repopulates normally.
- **Diff coverage** — the changed production line is inside a SQL string literal, so it
  carries no executable-line coverage of its own; the gate's ≥80% patch-coverage threshold
  is met by the test file and the docstring change adds no executable lines.

## Migration Plan

None. No DDL, no data migration, no ordering constraint against a deploy — the column is
already nullable and already present in both `init.sql` and the existing migration. The
change is a single statement-text edit that takes effect the next time the code runs.

## Open Questions

None. Issue #233 states the recommendation (option 1) and the acceptance criteria; no
requirement is invented here.
