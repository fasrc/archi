# Vector store rebuild with cutover — design

- **Status:** draft, awaiting review
- **Date:** 2026-10-10
- **Issue:** [#671](https://github.com/fasrc/archi/issues/671) — option (b), "build into a new collection, then switch"
- **Anchors:** all code links point at `origin/dev` @ `7406721b`

## 1. Problem

A full re-ingest empties the live vector table before it fills it again. Chat reads the same table during the full rebuild.

- `reset_collection` defaults to `true`
  ([base-config.yaml:348-349](https://github.com/fasrc/archi/blob/7406721b/src/cli/templates/base-config.yaml#L348-L349)).
- The reset runs `TRUNCATE TABLE document_chunks CASCADE`, then `VACUUM FULL document_chunks`
  ([manager.py:241](https://github.com/fasrc/archi/blob/7406721b/src/data_manager/vectorstore/manager.py#L241),
  [manager.py:263](https://github.com/fasrc/archi/blob/7406721b/src/data_manager/vectorstore/manager.py#L263)).
  `VACUUM FULL` takes an `ACCESS EXCLUSIVE` lock, so it also blocks chat reads.
- `_add_to_postgres` commits every 25 files
  ([manager.py:581](https://github.com/fasrc/archi/blob/7406721b/src/data_manager/vectorstore/manager.py#L581)),
  so chat sees a corpus that grows from 0 to full.

On dev, #671 measured about 65 minutes for 1174 documents and 7105 chunks. During that window, chat answers from a partial knowledge base and does not say so.

A second problem has the same root. The `vector(N)` column dimension is fixed when the volume is created
([init.sql:275](https://github.com/fasrc/archi/blob/7406721b/src/cli/templates/init.sql#L275)).
A change of embedding model to a new dimension has no safe path today. The only paths are `nuke.sh` or a manual change of the table.

## 2. Goal and scope

**Goal:** A full rebuild writes into a new set of tables while chat reads the old set. When the new set is complete and passes a check, one short transaction makes the new set live.

**In scope:**

- Every full rebuild: a redeploy with `reset_collection: true`, a manual reset, and a change of embedding model, dimension, or chunk configuration.
- One previous set kept for a manual rollback.

**Out of scope:**

- Scheduled per-source refreshes and uploads. These keep their incremental writes to the live set
  ([scheduler.py](https://github.com/fasrc/archi/blob/7406721b/src/data_manager/scheduler.py)).
- More than one queryable set at a time, for A/B tests or benchmarks (#216). This design does not block that work later.
- Content-hash staleness (the open change `fix-issue-39-content-hash-staleness`). That change is orthogonal.

**Success criteria:**

1. During a full rebuild, a chat retrieval never returns zero rows because of the rebuild, and never fails because of it.
2. The cutover blocks a chat read for a maximum of `lock_timeout` (5 s). The usual block is under 100 ms.
3. A failed or rejected build leaves the live set unchanged.
4. A change to a new embedding dimension needs only a redeploy, with no `nuke.sh`.

## 3. Approaches considered

| Approach | Result |
|---|---|
| **A. Build tables under a build id, then rename them in one transaction** | **Chosen.** The reader SQL does not change. A new dimension is free. |
| B. Versioned tables behind a `document_chunks` view | Rejected. A dimension change needs `DROP VIEW` + `CREATE VIEW`. The BM25 index lookup by table name breaks ([postgres_vectorstore.py:484](https://github.com/fasrc/archi/blob/7406721b/src/data_manager/vectorstore/postgres_vectorstore.py#L484)). Raw `DELETE` statements write through the view. |
| C. A new collection tag in the same table | Rejected. It cannot change the dimension. It needs all four #671 blockers removed first: `UNIQUE(document_id, chunk_index)`, no collection on parent nodes, the global `ingestion_status`, and the table-wide `TRUNCATE`. Filtered HNSW search loses recall. |

Approach A works because all readers name `document_chunks` and `document_parent_nodes` directly. After a rename, a new query resolves the name to the new table. psycopg2 does not keep server-side prepared plans, so no reader holds a stale table reference across queries.

## 4. Design

### 4.1 Terms

- **Live set:** the tables named `document_chunks` and `document_parent_nodes`. Chat reads only these.
- **Build set:** the tables `document_chunks_b{id}` and `document_parent_nodes_b{id}`, where `{id}` is the `index_build.id`.
- **Previous set:** the tables `document_chunks_prev` and `document_parent_nodes_prev`, kept from the last cutover.

### 4.2 New state table

A new migration under `src/cli/templates/migrations/` adds:

```sql
CREATE TABLE IF NOT EXISTS index_build (
  id SERIAL PRIMARY KEY,
  status TEXT NOT NULL,            -- building | validating | live | rejected | failed | superseded | rolled_back
  reason TEXT,                     -- why: reset | embedding_change | manual
  embedding_model TEXT NOT NULL,
  embedding_dimensions INTEGER NOT NULL,
  config_snapshot JSONB,           -- same shape as ingest_run.config_snapshot
  documents_total INTEGER,
  documents_failed INTEGER,
  chunk_count INTEGER,
  live_chunk_count_at_gate INTEGER,
  gate_detail JSONB,               -- each check: value, threshold, pass
  document_results JSONB,          -- [(document_id, status)]; used by rollback (4.6)
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ
);
```

At most one row has `status = 'live'`. A partial unique index enforces this:
`CREATE UNIQUE INDEX ON index_build ((true)) WHERE status = 'live';`

### 4.3 Build phase

Today, `DataManager.run_ingestion` calls `delete_existing_collection_if_reset()` and then `update_vectorstore()`. With `rebuild_strategy: cutover` (see 4.7), the reset branch becomes a call to a new module, `src/data_manager/vectorstore/rebuild.py`:

1. Take the Postgres advisory lock `pg_try_advisory_lock(<fixed key>)`. If another process holds the lock, log the condition and do not start a build. The existing in-process `RLock`
   ([service_data_manager.py:45](https://github.com/fasrc/archi/blob/7406721b/src/bin/service_data_manager.py#L45))
   stays, and it still makes scheduled refreshes and uploads wait until the build ends.
2. Drop each leftover build set from a crashed build. These are the tables that match `document_chunks_b%` or `document_parent_nodes_b%` and that have no `building` row whose process holds the advisory lock. Mark their `index_build` rows `failed`.
3. Drop the previous set (`*_prev`), if it exists. Mark its row `superseded`.
4. Insert an `index_build` row with `status = 'building'`.
5. Create the build set from one Python DDL function, `build_set_ddl(build_id, dimensions, index_type, has_bm25)`. This function emits:
   - Both tables with the same columns, defaults, `SERIAL` keys, and foreign keys (`REFERENCES documents(id) ON DELETE CASCADE`) as
     [init.sql:265-338](https://github.com/fasrc/archi/blob/7406721b/src/cli/templates/init.sql#L265-L338),
     and `embedding vector(<configured dimensions>)`.
   - The `chunk_tsv` generated column, only if `pg_textsearch` is absent. This matches the fallback branch of init.sql.
   - No index and no `UNIQUE` constraint yet.
6. Embed all catalog documents into the build set. `_add_to_postgres` and `_insert_hierarchical_file` get a `target: TableSet` argument. The default is the live set, so incremental callers do not change. In build mode, these two functions:
   - Write to the build tables.
   - Do not write `documents.ingestion_status` or `indexed_at`. These columns describe the live set. The build keeps per-document results in memory and writes the counts to `index_build`.
   - Use one transaction for each 25-file batch, as today. The commits are not visible to chat, because chat does not read the build tables.
7. Create the indexes after the load, under build-scoped names (for example `idx_chunks_embedding_b42`). These indexes are:
   - The `UNIQUE (document_id, chunk_index)` constraint.
   - `idx_chunks_document` and `idx_chunks_parent_id`.
   - The vector index (HNSW, IVFFlat, or none, from config).
   - The BM25 index, or the GIN index on `chunk_tsv`.
   - `idx_parent_nodes_document`.

   A build of an HNSW or IVFFlat index after the load is faster than inserts into an existing index. For IVFFlat, a build after the load also gives better lists.
8. Run `ANALYZE` on both build tables.

### 4.4 Cutover gate

Set `status = 'validating'`. Do all of these checks and record each result in `gate_detail`:

| Check | Default threshold | Why |
|---|---|---|
| The `embedding` column dimension equals the configured dimension | exact | Catches a DDL error or a config error before chat sees it. |
| Build chunk count ÷ live chunk count | ≥ 0.90 | Stops a scraper outage or a source auth failure from making a near-empty corpus live. |
| Failed documents ÷ total documents | ≤ 0.05 | Stops an embedding-endpoint outage from making a partial corpus live. |
| Build chunk count | > 0 | Absolute floor. |

Skip the ratio check if no live set exists (first install), or if the live set has 0 chunks.

If one or more checks fail:

- Set `status = 'rejected'`.
- Drop the build set.
- Write a `failed` `ingest_run` row with the gate detail.
- Leave the live set unchanged.

An operator can override the gate for one build with `POST /api/index/rebuild {"force": true}`. Use this override for an intended corpus shrink.

### 4.5 Cutover transaction

Do the cutover in one transaction:

```sql
BEGIN;
SET LOCAL lock_timeout = '5s';

-- 1. Catch deletions that occurred in the live set during the build.
--    The chat app deletes git and Jira sources from document_chunks directly
--    (app.py:6185, app.py:6819); the build set never saw those deletes.
DELETE FROM document_chunks_b42 c
  USING documents d
  WHERE c.document_id = d.id AND d.is_deleted;
DELETE FROM document_chunks_b42 c
  WHERE c.document_id IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = c.document_id);
-- (same two statements for document_parent_nodes_b42)

-- 2. Live → previous.
ALTER TABLE document_chunks        RENAME TO document_chunks_prev;
ALTER TABLE document_parent_nodes  RENAME TO document_parent_nodes_prev;
-- ALTER INDEX ... RENAME and ALTER SEQUENCE ... RENAME for every index and owned
-- sequence of both tables: canonical name → <name>_prev.

-- 3. Build → live.
ALTER TABLE document_chunks_b42       RENAME TO document_chunks;
ALTER TABLE document_parent_nodes_b42 RENAME TO document_parent_nodes;
-- ALTER INDEX / ALTER SEQUENCE ... RENAME: <name>_b42 → canonical name.

-- 4. Make the per-document status describe the new live set.
UPDATE documents d SET ingestion_status = s.status, indexed_at = NOW()
  FROM (VALUES ...) AS s(id, status) WHERE d.id = s.id;

UPDATE index_build SET status = 'superseded' WHERE status = 'live';
UPDATE index_build SET status = 'live', finished_at = NOW() WHERE id = 42;
INSERT INTO ingest_run (...);
COMMIT;
```

The code reads the index and sequence names from `pg_indexes` and from `pg_get_serial_sequence`. They are not hardcoded. As a result, the rename also covers an index that an operator or a migration added by hand. Examples are `idx_chunks_parent_id`, which `schema.py` creates on upgraded volumes, and #667.

**Locks:** Each `ALTER TABLE ... RENAME` takes `ACCESS EXCLUSIVE` on the table. A chat query that is in progress holds `ACCESS SHARE`. The rename waits for that query, and new chat queries wait behind the rename. `lock_timeout = 5s` puts a limit on that wait. On a timeout, the transaction rolls back with no change. The cutover then tries again 3 times, with 10 s between attempts. After 3 failures, the build stays `validating` with its tables kept, and `POST /api/index/cutover` tries again.

Cutover renames only. It does not copy or rewrite data, so its duration does not depend on the corpus size.

### 4.6 Previous set and rollback

The previous set stays until the next build starts (4.3 step 3). `POST /api/index/rollback` swaps the previous set and the live set with the same transaction shape as 4.5. It sets the rows to `rolled_back` and `live` as applicable. It also writes `documents.ingestion_status` from the previous build's results. The previous build's `index_build.document_results` column holds these results.

Disk cost: a maximum of 3 copies of the chunk tables during a build (live, previous, and build) for a short time. Step 3 drops the previous set before the embed starts, so the actual peak is 2 copies. At 7105 chunks, one copy is small.

### 4.7 Configuration

New keys under `data_manager` in
[base-config.yaml](https://github.com/fasrc/archi/blob/7406721b/src/cli/templates/base-config.yaml):

```yaml
data_manager:
  rebuild_strategy: cutover        # cutover | truncate (legacy; remove after one release)
  rebuild_gate:
    min_chunk_ratio: 0.90
    max_failed_ratio: 0.05
  rebuild_cutover_lock_timeout_s: 5
```

`reset_collection: true` keeps its meaning, "do a full rebuild at boot". With `rebuild_strategy: cutover`, it no longer means "empty the live table first".

**Embedding change detection:** At boot, compare the configured model and dimension with the `live` `index_build` row, or with the `embedding` column type if no row exists. A mismatch starts a cutover build even when `reset_collection: false`. This makes success criterion 4 true.

### 4.8 What does not change

- All reader SQL in `postgres_vectorstore.py`, `hierarchical_retriever.py`, `benchmark_provenance.py`, `catalog_postgres.py`, and the database viewer.
- The incremental sync path (`_sync_vectorstore`) and its callers.
- `init.sql`. It still creates the first live set on a fresh volume.

### 4.9 Status visibility

`index_build` is the source for a status-board line, for example "Rebuild in progress: 412 / 1174 documents; live = build 41 (bge-small, 384-d)". The chat retriever does not change. While a build runs, chat serves the complete old corpus, so the honesty notice of #671 option (c) is not necessary.

## 5. Failure handling

| Failure | Result |
|---|---|
| The data-manager crashes during a build | The live set is unchanged. The next boot drops the build set (4.3 step 2) and starts again. |
| The embedding endpoint is down during a build | Documents fail. The gate rejects the build on `max_failed_ratio`. The live set is unchanged. |
| A source returns few documents | The gate rejects the build on `min_chunk_ratio`. The live set is unchanged. |
| The cutover lock times out | Rollback with no change, then 3 more tries, then the build waits for a manual `cutover`. |
| A crash during the cutover transaction | Postgres rolls back the transaction. Either all renames apply or none apply. |
| Disk full during the embed | The insert fails, the build fails, and the build set is dropped. The live set is unchanged. |

## 6. Testing

TDD, red first, per the project gate (`bash scripts/gate.sh`, patch coverage ≥ 80 %).

**Unit tests (`tests/unit/`):**

- `build_set_ddl`: emits the configured dimension, the `chunk_tsv` column only without BM25, and build-scoped index names.
- The gate: each threshold at, below, and above its value; first install; live count 0; `force`.
- The cutover SQL order against a fake cursor: deletion catch-up, then live → previous, then build → live, then status, all in one transaction with `SET LOCAL lock_timeout`.
- Lock-timeout retry: 3 tries, then `validating` with the tables kept.
- Leftover-build cleanup selects only orphaned `_b%` tables.
- `_add_to_postgres` in build mode writes to the target tables and does not write `documents.ingestion_status`.

**Integration test (real Postgres with pgvector, marked and skipped when no DB is available):**

1. Seed a live set with N chunks.
2. Start a build in a thread. In a second thread, run `similarity_search_by_vector_with_score` and `hybrid_search` in a loop.
3. Assert that no read returns 0 rows and that no read raises an error.
4. After the cutover, assert that the reads return build-set rows.
5. Compare `information_schema.columns` and `pg_indexes` of the new live set with the set that init.sql created. The column sets and index definitions must match, apart from the names. This test catches a drift between `build_set_ddl` and `init.sql`.
6. Change the dimension from 384 to 768 and rebuild. Assert that the cutover succeeds and that reads use the new dimension.

**Live check on dev:** Do a redeploy with `reset_collection: true`. During the build, ask one chat question every 60 s. Each answer must cite documents from the full corpus. Record the cutover duration from the logs.

## 7. Rollout

1. Merge with `rebuild_strategy: cutover` as the default. `truncate` stays available for one release as a kill switch.
2. Verify on dev with the live check in section 6.
3. In the next release, remove the `truncate` branch and `VACUUM FULL`.

## 8. Related defects found during design (separate issues)

- `templates_manager.py` looks up the default dimension by `embedding_name`, but its map keys are model names
  ([templates_manager.py:1090-1099](https://github.com/fasrc/archi/blob/7406721b/src/cli/managers/templates_manager.py#L1090-L1099)).
  If `OpenAIEmbeddings` has no explicit `dimensions`, init.sql renders `vector(384)`, while `manager.py` assumes 1536. This design reads the dimension from one resolver, so a cutover build gets it correct. But the fresh-volume path still needs a fix.
- The chat app deletes git and Jira chunks with raw SQL that has no collection filter
  ([app.py:6185](https://github.com/fasrc/archi/blob/7406721b/src/interfaces/chat_app/app.py#L6185),
  [app.py:6819](https://github.com/fasrc/archi/blob/7406721b/src/interfaces/chat_app/app.py#L6819)).
  Step 1 of the cutover covers this for the rebuild case. The scoping problem itself is in `multi-collection-routing.md` task 16.

## 9. Open questions

1. Is a 0.90 chunk ratio correct for dev, where the sources change little between redeploys? The proposal is to set it from the last 5 `ingest_run` chunk counts after the first month.
2. Must `POST /api/index/rollback` also be an `archi` CLI subcommand, or is the endpoint enough for operators?
