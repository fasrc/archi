# Vector store rebuild with cutover — design

- **Status:** draft, revised after review
- **Date:** 2026-10-10. Revised 2026-10-15 for the 9 review findings on PR #692, and again for the 7 findings of the second review round. A third revision on the same day made each step a code path, with no manual or agent step.
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

**Code only:** The data-manager code does each step of the process: it starts a build, checks it, makes it live, tries again after a lock timeout, and rolls back a bad set. No step needs a person or an agent to run a command, call an endpoint, or read a log. Tests in CI verify the process, not a manual check after a deploy.

**In scope:**

- Every full rebuild: a redeploy with `reset_collection: true`, and a change of embedding model, dimension, or chunk configuration.
- One previous set kept for an automatic rollback (4.6).

**Out of scope:**

- Scheduled per-source refreshes and uploads. These keep their incremental writes to the live set
  ([scheduler.py](https://github.com/fasrc/archi/blob/7406721b/src/data_manager/scheduler.py)).
  While a build runs, they wait or defer (4.3 step 1).
- More than one queryable set at a time, for A/B tests or benchmarks (#216). This design does not block that work later.
- Content-hash staleness (the open change `fix-issue-39-content-hash-staleness`). That change is orthogonal.

**Success criteria:**

1. During a full rebuild, a chat retrieval never returns zero rows because of the rebuild, and never fails because of it. This includes a build for a new embedding model: chat uses the model of the live set until the cutover (4.8).
2. The cutover blocks a chat read for a maximum of 5 s plus the time of the catalog renames (4.5). The usual block is under 100 ms.
3. A failed or rejected build leaves the live set unchanged.
4. A change to a new embedding dimension needs only a redeploy, with no `nuke.sh`.
5. No step of a build, a cutover, a retry, or a rollback needs a person or an agent. The process has no manual endpoint and no manual check.

## 3. Approaches considered

| Approach | Result |
|---|---|
| **A. Build tables under a build id, then rename them in one transaction** | **Chosen.** The reader SQL text does not change. A new dimension is free. |
| B. Versioned tables behind a `document_chunks` view | Rejected. A dimension change needs `DROP VIEW` + `CREATE VIEW`. The BM25 index lookup by table name breaks ([postgres_vectorstore.py:484](https://github.com/fasrc/archi/blob/7406721b/src/data_manager/vectorstore/postgres_vectorstore.py#L484)). Raw `DELETE` statements write through the view. |
| C. A new collection tag in the same table | Rejected. It cannot change the dimension. It needs all four #671 blockers removed first: `UNIQUE(document_id, chunk_index)`, no collection on parent nodes, the global `ingestion_status`, and the table-wide `TRUNCATE`. Filtered HNSW search loses recall. |

Approach A works because all readers name `document_chunks` and `document_parent_nodes` directly. After a rename, a new query resolves the name to the new table. psycopg2 does not keep server-side prepared plans, so no reader holds a stale table reference across queries. Two reader changes are still necessary: the query embedder follows the live set, and a hierarchical read uses one transaction (4.8).

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
  status TEXT NOT NULL,            -- building | validating | live | previous | rejected | failed | superseded
  reason TEXT,                     -- why: reset | embedding_change | chunk_config_change | baseline
  embedding_model TEXT NOT NULL,   -- the model itself, as the chunk metadata 'embedding_model' tag records it
  embedding_dimensions INTEGER NOT NULL,
  collection_name TEXT NOT NULL,   -- the chunk metadata 'collection' value of this set (it names the class, not the model)
  config_snapshot JSONB,           -- same shape as ingest_run.config_snapshot; holds the embedding class and kwargs
  documents_total INTEGER,
  documents_failed INTEGER,
  chunk_count INTEGER,
  live_chunk_count_at_gate INTEGER,
  gate_detail JSONB,               -- each check: value, threshold, pass
  document_results JSONB,          -- [(document_id, status, error, ingested_at)]; used by cutover (4.5) and rollback (4.6)
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  rolled_back_at TIMESTAMPTZ       -- set when a rollback (4.6) demotes this build
);
```

**Table ownership.** Each physical table set has exactly one owner row, and the status names the set:

| Status | Owns |
|---|---|
| `live` | the live set |
| `previous` | the previous set (`*_prev`) |
| `building`, `validating` | the build set `*_b{id}` of the same id |
| `rejected`, `failed`, `superseded` | no tables |

At most one row has `status = 'live'`, and at most one row has `status = 'previous'`. Two partial unique indexes enforce this:
`CREATE UNIQUE INDEX ON index_build ((true)) WHERE status = 'live';`
`CREATE UNIQUE INDEX ON index_build ((true)) WHERE status = 'previous';`

Each transaction that renames or drops a table set also changes the status of its owner row (4.5, 4.6). As a result, a rollback always uses the row that owns the physical previous set.

**Parent table.** On an upgraded volume, `document_parent_nodes` can be absent. Today `schema.py` creates the table only on the first hierarchical read or write, because `init.sql` runs only on a fresh volume
([schema.py:1-10](https://github.com/fasrc/archi/blob/1def2267/src/data_manager/vectorstore/schema.py#L1-L10)).
The reader (4.8) and the cutover (4.5) lock that table, so it must exist first. The same migration runs the `schema.py` statements: the table, `idx_parent_nodes_document`, and `idx_chunks_parent_id`. These statements use `IF NOT EXISTS`, so they do nothing on a volume that has the table.

**Baseline row.** The migration creates `index_build` only. On an upgraded volume, the live set already exists, but no row describes it. The first time that the data-manager starts after the migration, it takes the build lock (4.3 step 1). Then, if no row has `status = 'live'` and `document_chunks` has rows, it inserts a baseline row for the existing live set:

- `status = 'live'` and `reason = 'baseline'`.
- The model comes from the chunk `metadata->>'embedding_model'` tag. `_tag_embedding_model` writes this tag on each chunk, because the collection tag names the embedding class, not the model
  ([manager.py:216-227](https://github.com/fasrc/archi/blob/1def2267/src/data_manager/vectorstore/manager.py#L216-L227), #570).
  The collection name comes from `metadata->>'collection'`, and the dimension from the `embedding` column type. None of these values comes from the current config, because the current config can already name a new model. If the chunks have more than one model value, the baseline uses the most common value and logs a warning. If no chunk has the model tag (a volume from before #570), the baseline uses the model of the current class-map entry and logs a warning. Chat makes the same assumption today.
- `config_snapshot` holds the constructor configuration of that model: the class and kwargs of the class-map entry for the collection's class key, with the model kwarg (the key that `retrieval_identity` reads) set to the recorded model. Its chunk configuration comes from the `config_snapshot` of the last successful `ingest_run`, or from the current config if no run exists.
- `document_results` is a copy of the current `documents.ingestion_status`, `ingestion_error`, and `ingested_at`.

As a result, the first cutover has a previous build row, and the first rollback (4.6) can restore the old statuses and the old model.

### 4.3 Build phase

Today, `DataManager.run_ingestion` calls `delete_existing_collection_if_reset()` and then `update_vectorstore()`. With `rebuild_strategy: cutover` (see 4.7), the reset branch becomes a call to a new module, `src/data_manager/vectorstore/rebuild.py`:

1. Take two Postgres advisory locks:
   - The **build lock** (`<build key>`), with `pg_try_advisory_lock`. Only a full build takes it. If another process holds the build lock, log the condition and do not start a build.
   - The **write lock** (`<write key>`), in exclusive mode, with `pg_advisory_lock`. The build waits until each incremental write that is in progress ends, and then holds the write lock until the cutover ends.

   Every vector writer takes the write lock, so the lock works across processes. `VectorStoreManager.update_vectorstore` takes it in shared mode with `pg_try_advisory_lock_shared` for the duration of one incremental write. All incremental writers call this one function:
   - the scheduled refreshes and the upload helper in the data-manager;
   - `/api/upload/embed` in the chat app
     ([app.py:6417](https://github.com/fasrc/archi/blob/7406721b/src/interfaces/chat_app/app.py#L6417)), which runs in a different process and does not see the data-manager `RLock`.

   If a build holds the write lock, the incremental write does not embed. The document stays `pending`, and `/api/upload/embed` tells the user that a rebuild is in progress. The sync after the cutover embeds the document (4.5). The existing in-process `RLock`
   ([service_data_manager.py:45](https://github.com/fasrc/archi/blob/7406721b/src/bin/service_data_manager.py#L45))
   stays, and it still makes scheduled refreshes and uploads in the data-manager wait until the build ends.
2. Find the build sets of earlier builds. These are the tables that match `document_chunks_b%` or `document_parent_nodes_b%`. Each set belongs to an `index_build` row, or to no row:
   - A `building` row: the process of that build stopped, because this process now holds the build lock. Drop its tables, and mark the row `failed`.
   - A `failed`, `rejected`, or `superseded` row, or no row: drop the tables. These rows own no tables (4.2), so the tables are leftovers.
   - A `validating` row: the build passed the gate, but its cutover did not complete (4.5). This is resumable state, and this step does not drop it. Step 2a decides what occurs to it.

   2a. If a `validating` row exists, compare its embedding model, dimension, and chunk configuration with the current config:
   - If they match, try its cutover (4.5) again, and do not start a new build.
   - If they do not match, mark the row `superseded`, and log its build id and the cause. Then drop its tables.

   The cutover retry job (4.5) applies the same rule each time it runs. No other path drops a `validating` set.
3. Keep the previous set (`*_prev`) through the build and the gate. A failed or rejected build thus does not remove the rollback target. Only a successful cutover replaces the previous set (4.5).
4. Insert an `index_build` row with `status = 'building'`.
5. Create the build set from one Python DDL function, `build_set_ddl(build_id, dimensions, index_type, has_bm25)`. This function emits:
   - Both tables with the same columns, defaults, `SERIAL` keys, and foreign keys (`REFERENCES documents(id) ON DELETE CASCADE`) as
     [init.sql:265-338](https://github.com/fasrc/archi/blob/7406721b/src/cli/templates/init.sql#L265-L338),
     and `embedding vector(<configured dimensions>)`.
   - The `chunk_tsv` generated column, only if `pg_textsearch` is absent. This matches the fallback branch of init.sql.
   - No index and no `UNIQUE` constraint yet.
6. Embed all catalog documents into the build set. `_add_to_postgres` and `_insert_hierarchical_file` get a `target: TableSet` argument. The default is the live set, so incremental callers do not change. In build mode, these two functions:
   - Write to the build tables.
   - Do not write `documents.ingestion_status`, `ingestion_error`, or `indexed_at`. These columns describe the live set. The build keeps a result for each document in memory: the status, the error text of a failure, and the `ingested_at` value that the build read. It writes the counts and the results to `index_build`.
   - Use one transaction for each 25-file batch, as today. The commits are not visible to chat, because chat does not read the build tables.
7. Create the indexes after the load, under build-scoped names (for example `idx_chunks_embedding_b42`). These indexes are:
   - The `UNIQUE (document_id, chunk_index)` constraint.
   - `idx_chunks_document` and `idx_chunks_parent_id`.
   - The vector index (HNSW, IVFFlat, or none, from config).
   - The BM25 index, or the GIN index on `chunk_tsv`.
   - `idx_parent_nodes_document`.

   A build of an HNSW or IVFFlat index after the load is faster than inserts into an existing index. For IVFFlat, a build after the load also gives better lists.

   7a. Copy the extra schema of the live set. A rename (4.5) moves a name only: an index stays on its table. For this reason, read each index of the live tables from `pg_indexes`, and each constraint from `pg_constraint` with `pg_get_constraintdef`.

   - Skip each managed object. `build_set_ddl` keeps the list of managed names: the primary keys, the foreign keys, the `UNIQUE` constraint, each index of step 7, and each vector index name that any `vector_index_type` creates. Skip a managed object even if its definition differs from the build set. The build set has the configured version of that object, and the old version must not come back. For example, a change from HNSW to `none` must not copy the old HNSW index.
   - Copy each other object (an index that a person added by hand, for example) to the build table under a build-scoped name, and log it. That object thus moves to the new live set.
8. Run `ANALYZE` on both build tables.

### 4.4 Cutover gate

Set `status = 'validating'`. Do all of these checks and record each result in `gate_detail`:

| Check | Default threshold | Why |
|---|---|---|
| The `embedding` column dimension equals the configured dimension | exact | Catches a DDL error or a config error before chat sees it. |
| Build chunk count ÷ live chunk count | ≥ 0.90 | Stops a scraper outage or a source auth failure from making a near-empty corpus live. |
| Failed documents ÷ total documents | ≤ 0.05 | Stops an embedding-endpoint outage from making a partial corpus live. |
| Build chunk count | > 0 | Absolute floor. |

Skip the ratio check in these conditions. The code reads each condition from the database and the config, so no person or agent has to approve a shrink:

- No live set exists (first install), or the live set has 0 chunks.
- The source configuration differs from the source configuration in the `config_snapshot` of the live row. A change of sources is an intended change of the corpus, so a smaller corpus is expected. The other checks still apply.

If the source configuration did not change, a large drop in chunks is not intended, and the ratio check applies.

If one or more checks fail:

- Set `status = 'rejected'`.
- Drop the build set.
- Write a `failed` `ingest_run` row with the gate detail.
- Leave the live set unchanged.

The dimension check, the floor, and the failed-document check apply to every build. No override exists.

### 4.5 Cutover transaction

Do the cutover in one transaction:

```sql
BEGIN;

-- Part A: data work. No statement in part A locks a live table against chat reads.

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

-- 2. Save the current per-document status of the outgoing live set in its
--    owner row. An incremental sync can change a status after the build
--    became live, so the results of the build alone can be stale. A rollback
--    to this set (4.6) restores these saved values.
UPDATE index_build b
  SET document_results = (
    SELECT jsonb_agg(jsonb_build_array(d.id, d.ingestion_status, d.ingestion_error, d.ingested_at))
      FROM documents d)
  WHERE b.status = 'live';

-- 3. Make the per-document status and error describe the new live set,
--    as update_ingestion_status does today (catalog_postgres.py:959-973):
--    a success clears the error, and a failure records it.
--    Skip a document whose catalog row changed during the build: its
--    ingested_at differs from the value that the build read. That document
--    stays 'pending', and the sync after the commit embeds it again.
UPDATE documents d
  SET ingestion_status = s.status,
      ingestion_error  = s.error,
      indexed_at       = CASE WHEN s.status = 'embedded' THEN NOW() ELSE d.indexed_at END
  FROM (VALUES ...) AS s(id, status, error, ingested_at)
  WHERE d.id = s.id
    AND d.ingested_at IS NOT DISTINCT FROM s.ingested_at;

-- 4. Replace the old previous set. Chat never reads it, so the drop does not
--    block chat. If this transaction rolls back, the drop rolls back too.
DROP TABLE IF EXISTS document_chunks_prev, document_parent_nodes_prev;

-- 5. Move each owner row with its tables (4.2, table ownership).
UPDATE index_build SET status = 'superseded' WHERE status = 'previous';
UPDATE index_build SET status = 'previous' WHERE status = 'live';
UPDATE index_build SET status = 'live', finished_at = NOW() WHERE id = 42;
INSERT INTO ingest_run (...);

-- Part B: the locks, with one limit on the total wait.
-- 6. Lock both live tables in one statement, in the order that readers use (4.8).
SET LOCAL statement_timeout = '5s';
LOCK TABLE document_chunks, document_parent_nodes IN ACCESS EXCLUSIVE MODE;

-- Part C: catalog renames only.
-- 7. Live → previous.
ALTER TABLE document_chunks        RENAME TO document_chunks_prev;
ALTER TABLE document_parent_nodes  RENAME TO document_parent_nodes_prev;
-- ALTER INDEX ... RENAME and ALTER SEQUENCE ... RENAME for every index and owned
-- sequence of both tables: canonical name → <name>_prev.

-- 8. Build → live.
ALTER TABLE document_chunks_b42       RENAME TO document_chunks;
ALTER TABLE document_parent_nodes_b42 RENAME TO document_parent_nodes;
-- ALTER INDEX / ALTER SEQUENCE ... RENAME: <name>_b42 → canonical name.
COMMIT;
```

The code reads the index and sequence names from `pg_indexes` and from `pg_get_serial_sequence`. They are not hardcoded. A rename moves a name only, so the old live set keeps its own indexes under `_prev` names. Step 7a of 4.3 copied each extra index and constraint onto the build set before the gate, so the new live set has them too.

**Locks:** Part A does all the data work. A chat read does not wait for part A, because part A changes only the build tables, the old previous set, `documents`, `index_build`, and `ingest_run`. Part B takes `ACCESS EXCLUSIVE` on both live tables with one `LOCK TABLE` statement. A chat query that is in progress holds `ACCESS SHARE`, so the lock waits for that query, and new chat queries wait behind the lock request.

`lock_timeout` limits each lock wait separately, so it cannot limit the total block. For this reason, part B uses `statement_timeout = 5s`, which limits the full `LOCK` statement: the waits for both tables together. After part B holds both locks, part C does only catalog renames, and then the transaction commits. As a result, chat waits for a maximum of 5 s plus the rename time. On a timeout, the transaction rolls back with no change.

The cutover then tries again 3 times, with 10 s between attempts. After 3 failures, the build stays `validating` with its tables kept (4.3 step 2).

**Cutover retry job:** The data-manager registers a job on its existing `CronScheduler` (`add_job`, cron `*/5 * * * *` by default). Each run takes the build lock, finds the `validating` row, and applies the rule of 4.3 step 2a: if the config still matches, it tries the cutover again; if not, it marks the row `superseded` and drops its tables. A run that finds no `validating` row does nothing. Each try and its result go to `index_build.gate_detail`, so the status board (4.9) shows them.

**After the commit:** Release the write lock and the build lock. Then run one incremental sync (`update_vectorstore()`) on the new live set. It embeds each document that is still `pending`: an upload that a build deferred (4.3 step 1), a new document from after the build read the catalog, and a document that changed during the build.

Cutover renames only. It does not copy or rewrite chunk data, so its duration does not depend on the corpus size.

### 4.6 Previous set and rollback

The previous set stays until a successful cutover replaces it (4.3 step 3).

**Post-cutover probe:** Right after each cutover commits, the data-manager runs a probe through the chat read path of 4.8. The probe sends 5 queries (configurable). Each query text is the `display_name` of a random `embedded` document. The probe fails if one or more queries raise an error, or if 3 or more queries return 0 rows. The probe catches a set that the gate cannot judge from counts: an embedder that chat cannot load, a dimension error at query time, or a broken index. The probe result goes to `index_build.gate_detail`.

**Automatic rollback:** If the probe fails, the data-manager rolls back at once. It writes a `failed` `ingest_run` row with the probe detail, and it logs the build id at `ERROR`. The rolled-back build becomes `previous` with `rolled_back_at` set, and the next build supersedes it. The retry job never retries a rolled-back build. The rollback uses the same transaction shape as 4.5:

- Part A saves the current per-document status into the `live` row (4.5 step 2). It writes `documents.ingestion_status` and `ingestion_error` from the `document_results` of the `previous` row. Then it swaps the two statuses: the `previous` row becomes `live`, and the `live` row becomes `previous` with `rolled_back_at = NOW()`. The partial unique indexes (4.2) check each row immediately, so the swap uses three statements: `live` → `swap`, `previous` → `live`, `swap` → `previous`. The `swap` status exists only inside this transaction. A rollback drops no table.
- Parts B and C lock the live tables and swap the names of the two sets through a temporary name (live → `*_swap`, previous → live, `*_swap` → previous).

The `previous` row owns the physical previous set (4.2, table ownership), so the rollback always restores the model and the statuses that match the tables. A rollback occurs at most one time for each cutover. For the first cutover on an upgraded volume, the baseline row (4.2) becomes the `previous` row. Readers take the embedding model from the `live` row (4.8), so a rollback also moves chat back to the previous model.

If the data-manager stops after a cutover commits and before its probe ends, the next boot runs the probe for the `live` row, because that row has no probe result in `gate_detail`. A failed probe then rolls back as above.

Disk cost: a maximum of 3 copies of the chunk tables during a build (live, previous, and build). The cutover drops the old previous set, so 2 copies remain after it. At 7105 chunks, one copy is small.

### 4.7 Configuration

New keys under `data_manager` in
[base-config.yaml](https://github.com/fasrc/archi/blob/7406721b/src/cli/templates/base-config.yaml):

```yaml
data_manager:
  rebuild_strategy: cutover        # cutover | truncate (legacy; remove after one release)
  rebuild_gate:
    min_chunk_ratio: 0.90
    max_failed_ratio: 0.05
  rebuild_cutover_lock_wait_s: 5   # statement_timeout of the cutover LOCK (4.5)
  rebuild_cutover_retry_cron: "*/5 * * * *"   # cutover retry job (4.5)
  rebuild_probe_queries: 5         # post-cutover probe (4.6)
```

The thresholds are config values. No step of the process asks a person to tune them.

`reset_collection: true` keeps its meaning, "do a full rebuild at boot". With `rebuild_strategy: cutover`, it no longer means "empty the live table first".

**Change detection:** At boot, compare the current config with the `live` `index_build` row. Compare the model, the dimension, and the effective chunk configuration (the strategy, the sizes, and the overlap, in the same fields as `ingest_run.config_snapshot`). On the first boot after the migration, this row is the baseline row (4.2). A mismatch in one or more of these values starts a cutover build even when `reset_collection: false`. This makes success criterion 4 true, and it obeys the scope of section 2: a change of chunk configuration is a full rebuild.

### 4.8 Read path

The reader SQL text in `postgres_vectorstore.py`, `hierarchical_retriever.py`, `benchmark_provenance.py`, `catalog_postgres.py`, and the database viewer does not change. Two reader changes are necessary.

**The query embedder follows the live set.** Today the chat process builds its query embedder and its collection filter from the config at start-up
([vectorstore_connector.py:38-49](https://github.com/fasrc/archi/blob/7406721b/src/archi/utils/vectorstore_connector.py#L38-L49)).
After a redeploy that changes the model, chat then queries the old live set with the new model for the full build. The collection filter
([postgres_vectorstore.py:366](https://github.com/fasrc/archi/blob/7406721b/src/data_manager/vectorstore/postgres_vectorstore.py#L366))
removes every old row, a new dimension makes pgvector reject the comparison, and a new model of the same dimension gives vectors that do not compare.

With this design, the chat process takes the model, the dimension, and the collection name from the `live` `index_build` row (`config_snapshot` and `collection_name`), not from the config. The retrieval identity that benchmark provenance records comes from the same row. The chat process keeps one embedder for each build id. Each retrieval does these steps:

1. Embed the query with the embedder of the last live build id that the process saw.
2. Open one transaction. Take `ACCESS SHARE` on `document_chunks` and then on `document_parent_nodes` with one `LOCK TABLE ... IN ACCESS SHARE MODE` statement. Then read the `live` row.
3. If the live build id is not the id of step 1, end the transaction, load the embedder for the new build, and start again at step 1. This occurs one time for each cutover.
4. Run the candidate search in the same transaction.

The cutover renames the tables and changes the `live` row in one transaction. It cannot get its lock while a reader holds `ACCESS SHARE`, so a reader always sees a `live` row and tables from the same build. The reader and the cutover lock the two tables in the same order, so they cannot deadlock. If no `live` row exists (a fresh volume before its first build), the chat process uses the config, as today. If the chat process cannot build the embedder of the live set (for example, the old model is not available), retrieval returns no documents and logs the cause until the cutover.

**A hierarchical read uses one transaction.** Today the retriever reads the child candidates, reranks them on the CPU, and then reads the parents by serial id on a new connection
([hierarchical_retriever.py:151-176](https://github.com/fasrc/archi/blob/7406721b/src/data_manager/vectorstore/retrievers/hierarchical_retriever.py#L151-L176)).
A cutover between the two reads can match a child of the old set with an unrelated parent of the new set, because each set has its own serial ids. With this design, the retriever reads the parents of all candidates in the transaction of step 2 above, before the rerank. `hybrid_search` and `_fetch_parents` get an optional connection argument for this. The rerank then runs with no open transaction, so it does not delay a cutover.

Other parts that do not change:

- The incremental sync path (`_sync_vectorstore`) and its SQL. Only its lock changes (4.3 step 1).
- `init.sql`. It still creates the first live set on a fresh volume.

### 4.9 Status visibility

`index_build` is the source for a status-board line, for example "Rebuild in progress: 412 / 1174 documents; live = build 41 (bge-small, 384-d)". A `validating` build gets its own line, for example "Build 42 waits for cutover". While a build runs, chat serves the complete old corpus with the old model, so the honesty notice of #671 option (c) is not necessary.

## 5. Failure handling

| Failure | Result |
|---|---|
| The data-manager crashes during a build | The live set is unchanged. The next boot drops the build set (4.3 step 2) and starts again. |
| The embedding endpoint is down during a build | Documents fail. The gate rejects the build on `max_failed_ratio`. The live set and the previous set are unchanged. |
| A source returns few documents | The gate rejects the build on `min_chunk_ratio`. The live set is unchanged. |
| The cutover lock times out | Rollback with no change, then 3 more tries. Then the retry job tries again every 5 minutes, until a cutover succeeds or a config change supersedes the build. |
| The new live set fails the post-cutover probe | Automatic rollback to the previous set. A `failed` `ingest_run` row and an `ERROR` log line record the cause. |
| The data-manager restarts while a build waits for a cutover | The next boot keeps the `validating` build and tries its cutover again, if the config still matches (4.3 step 2a). |
| A crash during the cutover transaction | Postgres rolls back the transaction. Either all renames apply or none apply. |
| A user uploads a file during a build | The upload does not embed, and the document stays `pending`. The sync after the cutover embeds it. |
| The chat process cannot load the embedder of the live set | Retrieval returns no documents and logs the cause until the cutover. |
| Disk full during the embed | The insert fails, the build fails, and the build set is dropped. The live set is unchanged. |

## 6. Testing

TDD, red first, per the project gate (`bash scripts/gate.sh`, patch coverage ≥ 80 %).

**Unit tests (`tests/unit/`):**

- `build_set_ddl`: emits the configured dimension, the `chunk_tsv` column only without BM25, and build-scoped index names.
- Copy of the extra schema (step 7a): an extra index or constraint on the live set gets a build-scoped copy. A managed object is never copied, also when its definition differs: after a change from HNSW to `none`, the build set has no HNSW index.
- The gate: each threshold at, below, and above its value; first install; live count 0. A change of the source configuration skips the chunk ratio only: with it, a dimension error, an empty build, or a high failed ratio still rejects the build. With the same source configuration, a drop below the ratio rejects the build.
- The cutover SQL order against a fake cursor: deletion catch-up, then the status save into the outgoing `live` row, then the status and error update, then the drop of the old previous set, then the owner-row moves (`previous` → `superseded`, `live` → `previous`, build → `live`), then `SET LOCAL statement_timeout` and one `LOCK TABLE` of both live tables, then live → previous, then build → live, all in one transaction. No data statement follows the `LOCK`.
- Table ownership: after each cutover and each rollback, exactly one row is `live` and at most one row is `previous`. A `validating` build that is superseded between two cutovers does not change which row is `previous`.
- Rollback: restores the statuses that the cutover saved for the `previous` row, also after an incremental sync changed a status while that set was live. The status swap does not violate a partial unique index.
- Post-cutover probe: a probe with an error, or with 3 or more empty results of 5, starts the automatic rollback. A probe with 2 empty results does not. A boot that finds a `live` row with no probe result runs the probe.
- Cutover retry job: a run with a matching `validating` row tries the cutover; a run with a config change supersedes the row; a run with no `validating` row does nothing; a rolled-back build is never retried.
- A rejected or failed build leaves the previous set and its row unchanged.
- The status update writes the error of a failed document, clears the error of an embedded document, and skips a document whose `ingested_at` changed.
- Lock-timeout retry: 3 tries, then `validating` with the tables kept.
- Cleanup of earlier builds: drops the set of a `building` row, a `failed`, `rejected`, or `superseded` row, or no row. It keeps a `validating` set. A `validating` build that matches the config gets its cutover again. A `validating` build that does not match becomes `superseded`, with a log line.
- Baseline row: inserted one time, only if no `live` row exists and the live set has rows. The model comes from the chunk `embedding_model` tag, not from the config. A test with two models of one class (same collection tag) gets the recorded model in the row and in the kwargs of `config_snapshot`.
- Change detection: a change of the chunk strategy, size, or overlap with `reset_collection: false` starts a cutover build. The same config does not.
- `update_vectorstore` takes the write lock in shared mode. If a build holds the lock, it returns without an embed and leaves the documents `pending`.
- The reader: it changes the embedder when the live build id changes, and it reads the child and parent rows in one transaction.
- `_add_to_postgres` in build mode writes to the target tables and does not write `documents.ingestion_status`.

**Integration test (real Postgres with pgvector):** CI runs these tests in a job with a `pgvector/pgvector:pg17` service container, so a merge cannot skip them. On a workstation with no database, the tests are skipped.

1. Seed a live set with N chunks.
2. Start a build in a thread. In a second thread, run `similarity_search_by_vector_with_score`, `hybrid_search`, and the hierarchical retriever in a loop.
3. Assert that no read returns 0 rows, that no read raises an error, and that each parent belongs to the document of its child.
4. After the cutover, assert that the reads return build-set rows.
5. Compare `information_schema.columns` and `pg_indexes` of the new live set with the set that init.sql created. The column sets and index definitions must match, apart from the names. This test catches a drift between `build_set_ddl` and `init.sql`.
6. Add an extra index to the live set, then rebuild. Assert that the new live set has the same index definition.
7. Change the dimension from 384 to 768 and rebuild. Assert that the reads use the old model and return rows during the build, and use the new dimension after the cutover.
8. During a build, call `update_vectorstore` from a second connection for a new document. Assert that it does not embed, and that the document is `embedded` after the cutover.
9. Start from an upgraded volume that has `document_chunks` but no `document_parent_nodes`. Run the migration, then a build. Assert that reads during the build and the cutover succeed with no `undefined_table` error.
10. Make the embedder of a new build fail at query time, then rebuild. Assert that the probe fails, that the code rolls back with no call from the test, and that the reads then return rows of the old set.
11. In test 2, measure the longest read wait during the cutover. Assert that it is less than `rebuild_cutover_lock_wait_s` plus 1 s.

**Records in place of a manual check:** Each build writes its gate result, each cutover try, the lock wait time, and the probe result to `index_build`. The status board (4.9) shows them. After a deploy, no person or agent has to ask chat questions or read logs to know the result.

## 7. Rollout

1. Merge with `rebuild_strategy: cutover` as the default. `truncate` stays available for one release as a kill switch. The CI job of section 6 must pass before the merge.
2. Redeploy dev. The data-manager does the build, the gate, the cutover, and the probe by itself, and the status board shows the result.
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

None. The two earlier questions are closed:

1. The chunk ratio is a config value (4.7) with a default of 0.90. No step asks a person to tune it.
2. The rollback is automatic (4.6), so it needs no endpoint and no CLI subcommand.
