## Context

`init.sql` is mounted at `/docker-entrypoint-initdb.d/init.sql`
(`base-compose.yaml:104`), which Postgres runs **only** when it initializes an empty data
directory. Every upgraded deployment therefore keeps the schema it was born with.
`src/cli/templates/migrations/` holds two hand-written catch-up files, but nothing reads
them: `grep -rn "migrations" src/cli/` returns nothing, and `templates_manager.py:595–629`
renders `init.sql` alone.

The visible symptom is `documents.last_modified` (#155/#163): the catalog names it
unconditionally in its INSERT, psycopg2 raises `UndefinedColumn` on an upgraded volume,
and the scraper's per-URL `except Exception` swallows it — ingest logs success, persists
nothing.

**Anchor corrections to the issue body** (verified against `origin/dev` @ `0a157cdc`):

| Issue body says | Actually |
|---|---|
| `src/data_manager/catalog_postgres.py` | `src/data_manager/collectors/utils/catalog_postgres.py` |
| `catalog_postgres.py` contains `_handle_standard_url` | it is `scraper_manager.py:673`; the swallow is its `except Exception` at ~`:716`. `upsert_resource` has no try/except of its own — the error propagates into the scraper's per-URL catch-all |
| `catalog_postgres.py:216` names `last_modified` in the INSERT | the INSERT column list is `:224–242`; `last_modified` is `:238` |
| both migration files are idempotent | `add_documents_last_modified.sql` is; **`rename_mid_to_message_id.sql` is not** — see Decision 3 |

The behavioral description in the issue is correct in every case; only the file
attribution and line numbers drifted. `base-compose.yaml:90`, the `config-seed` precedent
(`:119–146`), `init.sql:240`, and the two migration filenames all verified as stated.

## Goals / Non-Goals

**Goals:**
- Pending `migrations/*.sql` apply automatically on an upgraded deployment, before
  anything reads or writes the schema.
- A fresh deployment runs the same files as a no-op — no startup failure.
- A genuinely broken migration blocks startup loudly instead of half-migrating.
- The catalog refuses to run against a schema missing columns it writes, naming them.

**Non-Goals:**
- A migration ledger table / version tracking. Files are made re-runnable instead
  (Decision 4). If ordering or partial application ever needs tracking, that is a
  separate change.
- Writing new migration files — the existing two cover the known gaps.
- Changing the Postgres entrypoint or `init.sql`.
- #193 (nullable `client_sent_msg_ts`) — depends on this, tracked separately.
- Any `deploy/` change. This touches the CLI's rendering templates only; the rendered
  output is what a deployment consumes.

## Decisions

### Decision 1 — Run migrations with `psql` from the Postgres image, not a Python sidecar

The `db-migrate` service builds from the same
`archi_code/cli/templates/dockerfiles/Dockerfile-postgres` as the `postgres` service, so
the image is already built and `psql` is already present. `config-seed` uses the chat
image because it runs `python -m src.cli.tools.config_seed`; applying raw `.sql` needs no
Python, no `psycopg2`, and no new dependency.

Command shape (lexicographic order, stop at first error):

```sh
for f in /migrations/*.sql; do psql -v ON_ERROR_STOP=1 -f "$f"; done
```

`ON_ERROR_STOP=1` plus the default `set -e` behavior of the shell gives the fail-loud
requirement for free. **Alternative considered:** a Python runner reusing the chat image
and `psycopg2`. Rejected — more code, more surface, and it would need its own error
handling to match what `ON_ERROR_STOP` already does.

### Decision 2 — Gate ordering on `config-seed` *and* the data-manager, not on all nine services

Compose `depends_on` is transitive, and every app service already waits on `config-seed`
(`:38–39, :161–164, :242–245, :279–282, :368–371, :420–423, :496–499, :561–564, :627–630`).
Making `config-seed` wait on `db-migrate` therefore migrates the schema before *anything*
starts, with one edit rather than nine.

A direct edge is also added to the data-manager (`:35–39`) even though it is redundant
with the transitive one. It is two lines, it is the service the issue is actually about,
and it keeps the guarantee readable to someone auditing that block in isolation — it does
not silently depend on `config-seed` continuing to exist.

**Alternative considered:** add `db-migrate` to all nine `depends_on` blocks. Rejected —
eight redundant edits, eight chances to miss one when a service is added later.

### Decision 3 — Make the renames conditional (the issue's idempotency premise is false)

`rename_mid_to_message_id.sql` opens with:

```sql
ALTER TABLE feedback RENAME COLUMN mid TO message_id;
```

PostgreSQL has **no** `IF EXISTS` form for `RENAME COLUMN`. On a fresh deployment
`init.sql` has already created `message_id` and there is no `mid`, so this raises
`undefined_column`, `db-migrate` exits non-zero, `service_completed_successfully` never
fires, and **the entire stack fails to start**. Shipping the sidecar without fixing this
would convert a silent data bug into a total outage on every fresh deploy.

Each rename is wrapped in a presence guard on the old column (and absence of the new),
e.g.:

```sql
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'feedback' AND column_name = 'mid')
     AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                     WHERE table_name = 'feedback' AND column_name = 'message_id')
  THEN
    ALTER TABLE feedback RENAME COLUMN mid TO message_id;
  END IF;
END $$;
```

This is edit-for-idempotency of an existing file, not a new migration — the issue's
"idempotent on a fresh deployment" acceptance criterion cannot be met otherwise. The six
renames across `feedback`, `timing`, and `ab_comparisons` all need it; the `IF EXISTS` /
`IF NOT EXISTS` statements in the rest of the file are already safe.

### Decision 4 — No migration ledger

With every file re-runnable, running all of them every boot is correct and cheap (six
`information_schema` lookups and a few no-op `ALTER`s). A ledger would add a table, a
write path, and a new failure mode (ledger says applied, schema says otherwise) for no
benefit at this scale. Revisit if migrations ever become non-idempotent by nature (data
backfills, destructive rewrites).

### Decision 5 — Put the column check in `refresh()`, called from `__post_init__`

`PostgresCatalogService.__post_init__` (`:111`) already calls `refresh()` (`:147`), which
already opens a connection. That is the natural startup hook — no new lifecycle concept,
and it runs exactly once per service instance, satisfying "not per resource".

The required set is the INSERT's column list (`:225–241`), declared as a module-level
frozenset next to the existing `_METADATA_COLUMN_MAP` / `_NON_TEXT_COLUMNS` constants so
it sits with its siblings:

```
resource_hash, file_path, display_name, source_type, url, ticket_id, suffix,
size_bytes, original_path, base_path, relative_path, file_modified_at,
ingested_at, last_modified, ingestion_status, extra_json, extra_text, is_deleted
```

One `information_schema.columns` query for `documents`, set-difference against that
frozenset, `RuntimeError` naming the sorted missing columns if non-empty.

**Alternative considered:** parse the column names out of the SQL string at import time so
the list cannot drift from the query. Rejected — regex-parsing our own SQL is more fragile
than a declared constant, and a test asserting the constant matches the INSERT gives the
same protection legibly.

### Decision 6 — Both target Python files are `black`-clean

`black --check` passes on `catalog_postgres.py` and `templates_manager.py` at
`origin/dev`, so in-place edits will not trigger a whole-file reflow that would swamp the
diff and sink diff coverage. No seam-routing needed; edit directly.

## Risks / Trade-offs

- **A fresh deploy fails outright if any migration is non-idempotent** → Decision 3 fixes
  the one file that is. A test applies every migration against an `init.sql`-shaped schema
  so a future non-idempotent file is caught by the gate, not by a broken deploy.
- **Migrations now run on every boot, lengthening startup** → each is a guarded no-op;
  the cost is a handful of catalog lookups. Acceptable against silent data loss.
- **The startup check turns a partial-write bug into a hard startup failure** → that is
  the point, and it is what the issue asks for. The blast radius is bounded: the failure
  names the missing column, and `db-migrate` will normally have just added it.
- **`db-migrate` blocking `config-seed` puts a new service on the critical path** →
  `restart: "no"` and a fail-fast runner mean it either completes quickly or reports a
  real schema problem that would have broken ingest anyway.
- **The transitive-ordering argument breaks if `config-seed` is ever removed** →
  mitigated by the direct data-manager edge (Decision 2) and by a rendered-output test
  asserting both edges.

## Migration Plan

1. Ship the change; the next redeploy renders the sidecar and copies `migrations/`.
2. On an existing volume, `db-migrate` adds `documents.last_modified` (and the `/v1`
   columns) before the data-manager starts; the first ingest persists timestamps with no
   manual SQL.
3. On a fresh volume, every file is a no-op and the stack starts as before.
4. **Rollback:** revert the compose template. The migrations are additive
   (`ADD COLUMN IF NOT EXISTS`, guarded renames); an older build ignores the extra
   columns, so no down-migration is required.

## Open Questions

None. The two decisions the issue left to the operator (sidecar vs. entrypoint; include
the precondition check) were resolved on 2026-08-10 and are recorded in the issue body.
