## Context

`/ssb/status` renders from `status_board()` (`src/interfaces/chat_app/service_alerts.py:104`)
and shows only alerts. The values an operator needs to answer "is what I am looking at
deployed" exist, but never reach the application:

- **Config pin** — `ensure_config` computes `head`, the pin, the match verdict and the
  dirty paths at `deploy/scripts/lib.sh:295-303`, then logs them and drops them.
  `CONFIG_REF` is a shell variable; it never enters a container or the database.
- **Deploy time** — there is no record. `static_config.created_at` cannot serve, because
  the upsert at `src/utils/config_service.py:405-440` omits `created_at` from its
  `ON CONFLICT DO UPDATE` list, so it holds the first ever seed on that volume. Data
  volumes survive redeploys, so that value can be months stale.
- **Ingest state** — `documents.ingested_at`, `documents.indexed_at` and
  `documents.ingestion_status` exist and nothing displays them.
- **Ingest flags** — only current configuration is stored (`static_config`), so reading
  it back after a configuration change misattributes the corpus.

Constraints. `service_alerts.py`, `app.py` and Jinja templates are thinly covered by unit
tests, and the gate enforces `diff-cover --fail-under=80`. Preserved data volumes mean an
existing database never re-runs `init.sql`, so any schema change needs a migration.

## Goals / Non-Goals

**Goals:**

- An operator reading `/ssb/status` can name the config actually deployed, and can tell a
  clean on-pin deploy from a live-edited one.
- The ingest flags shown describe the run that built the corpus, not current
  configuration.
- Configuration that drifted from the corpus is visible, per flag.
- No deploy, ingest, or page render fails because provenance could not be written or read.

**Non-Goals:**

- No change to chat, retrieval, the `/v1` endpoint, or the alert tables.
- No per-message version in the chat footer — that was issue #537's shape and is not this
  change.
- No new provenance beyond what `ensure_config` already computes. This change persists
  and displays existing values; it does not invent new pin semantics.
- No backfill of history. A deployment carries no record until its next deploy or ingest.

## Decisions

### D1 — Two tables, not one

`deployment_record` and `ingest_run` are written by different processes at different
times with different cardinality: the seed writes one row per deploy, the data manager one
row per ingest run. A single table would force a wide row with half its columns null at
every write, and would make "last deploy" and "last ingest" the same query with different
filters.

*Alternative rejected:* extending `static_config` with the pin columns. It is a
single-row table whose upsert already demonstrates the timestamp trap, and a deploy
history would be impossible.

### D2 — Append-only rows, read newest

Each write inserts; readers take the newest row by timestamp. This yields deploy and
ingest history for free, keeps writes trivial, and avoids the `ON CONFLICT` semantics that
produced the `created_at` bug. Growth is negligible — one row per deploy and per ingest
run.

*Alternative rejected:* a single upserted row per table. Cheaper to read, but repeats the
exact mistake this change exists to correct, and loses "when did this last change".

### D3 — Provenance reaches the seed as environment variables

`ensure_config` runs before `archi create` and already holds the values. It exports
`ARCHI_CONFIG_REF`, `ARCHI_CONFIG_SHA`, `ARCHI_CONFIG_HEAD`, `ARCHI_CONFIG_PIN_MATCHED`
and `ARCHI_CONFIG_DIRTY_PATHS`; the compose template passes them to the `config-seed`
service; `seed_entry` (`src/cli/tools/config_seed.py:127`) already receives `os.environ`
and reads them there. `APP_VERSION` needs no new plumbing — it is already an environment
variable in that container, because `config-seed` builds from `Dockerfile-chat`
(`base-compose.yaml:138`).

*Alternative rejected:* a file written to the deployment root, like `SOURCE_COMMIT`. The
container cannot see the host's deployment root — issue #537 verified that directly.

### D4 — The ingest run is recorded around `update_vectorstore()`, not at the log line

The "Vectorstore update has been completed" line
(`src/data_manager/vectorstore/manager.py:314`) sits inside the `else` branch, so it fires
only when documents changed. Recording there would leave a no-change re-ingest unrecorded,
and the board would report a stale "last ingest" after a run that really happened. The
record is therefore written when `update_vectorstore()` (line 255) finishes, covering both
the "up to date" and the "updated" branches, with a status that distinguishes them.

### D5 — The snapshot is built by a pure function

A builder takes the data-manager configuration dictionary and returns the flag mapping
(HTML-to-Markdown, categorization, chunking strategy, embedding model and dimensions,
chunk size, chunk overlap, distance metric, sitemap floor). It reads no environment and
opens no connection, so it is fully unit-testable, and the same function serves both the
write path (`self._data_manager_config` during a run) and the drift comparison (the
current configuration from `static_config.data_manager_config`). One function means the
two sides cannot disagree about what a flag is called or defaulted to.

### D6 — Drift is a per-key comparison of two snapshots

Both sides are built by the D5 function, so drift is a key-by-key comparison of two flat
mappings. The result names each differing flag with its current value and its value at
ingest. Comparing whole dictionaries would only yield a boolean and could not fill the
warning the operator asked for.

### D7 — All logic sits in tested helper modules

`service_alerts.py` and `status.html` stay thin call sites. New logic lives in helper
modules under `src/interfaces/chat_app/` with their own unit tests, following the
`config_fingerprint.py` + `tests/unit/test_config_fingerprint.py` precedent named in the
project's `CLAUDE.md`. This is what keeps patch coverage above the gate's floor: the
route handler gains a call and a template gains fields, while the branching — match
verdict, live-edited state, drift, missing-record fallbacks, duration formatting — is
exercised directly.

### D8 — Schema lands in three places

`src/cli/templates/init.sql` for fresh deployments, `tests/smoke/init-test.sql` so the
smoke schema keeps parity, and a new migration under `src/cli/templates/migrations/`
(precedent: `add_documents_last_modified.sql`) for existing databases, which never re-run
`init.sql` because their volumes are preserved.

### D9 — Provenance never breaks the thing it describes

Every write is wrapped: a failed deployment-record write logs and lets the deploy
continue; a failed ingest-run write logs and lets the ingest report success; a failed read
logs and renders the panel as unavailable while the alert sections still display. The
corpus and the config are the product; the record is commentary.

## Risks / Trade-offs

- **An existing deployment shows no provenance until it is next deployed or ingested.**
  → The panels render an explicit "unavailable" state rather than blank or zero, so the
  gap reads as "not recorded yet" instead of "nothing deployed".

- **`ARCHI_CONFIG_DIRTY_PATHS` can be large on a very dirty checkout.** → Store the
  name-status listing but display a count plus the first few paths; the full value stays
  queryable.

- **The dev host redeploy triggers a long re-ingest.** → Nothing in this change requires a
  redeploy to review. The unit tests and the gate validate it; a live check can ride the
  next scheduled deploy rather than forcing one.

- **The snapshot could drift from the flags that actually matter as ingest evolves.** →
  The flag list lives in one builder function (D5) with its own tests, so adding a flag is
  a single-site change rather than a hunt through the write path and the display path.

- **`pin_matched` true with dirty paths is the subtle case.** → It is treated as a
  first-class warning state, not as a clean deploy. The spec pins that behaviour with its
  own scenario, because this is precisely the case the operator asked to be able to see.

## Migration Plan

1. Add the two tables to `init.sql` and `init-test.sql`, and add the migration file for
   existing databases.
2. Land the writers (seed, data manager) and the readers (helpers, route, template)
   together. Readers tolerate absent rows from the start, so ordering within the release
   is not load-bearing.
3. On the first deploy after the change, the deployment record appears; on the first
   ingest after it, the ingest-run record appears. Until then the panels report
   unavailable.
4. Rollback is a revert: the tables become unread, and nothing else depends on them.

## Open Questions

None blocking. Two points were settled by the operator on 2026-09-23 when the terminal
mockup was approved: build all three parts, keep both warning states, and make the flag
list a true ingest-time snapshot rather than a "current configuration" label.
