# Design — delete unreferenced parent nodes (#411)

Decisions D1 and D2 were made with the operator on 2026-09-26 (issue #411 body). The rest
follow from the code on `origin/dev` `570814d0`.

## D1. Delete only parents that no chunk references, per document

The predicate is "no row in `document_chunks`, in ANY collection, has
`metadata->>'parent_id' = p.id::text`". A delete by `document_id` alone was rejected:
parents carry no collection column, but the chunk delete in `_remove_from_postgres` filters
by collection (`manager.py:522-529`). A blanket delete can remove parents that another
collection's chunks still reference. The `NOT EXISTS` predicate is collection-blind on
purpose, so a parent that any live chunk references always survives.

The `NOT EXISTS` subquery uses the expression index `idx_chunks_parent_id` on
`document_chunks ((metadata->>'parent_id'))` (`init.sql:346-347`). The outer scan uses
`idx_parent_nodes_document` (`init.sql:338`) or the `documents` hash index.

## D2. Existing rows are cleaned by a documented one-off snippet

No startup self-heal. The operator runs the snippet in `docs/docs/troubleshooting.md` and
decides when. The CAUTION in the docs states the fingerprint effect (D7).

## D3. A helper module holds the SQL

`src/data_manager/vectorstore/parent_nodes.py` exports:

```python
PARENT_TABLE_EXISTS = "SELECT to_regclass('document_parent_nodes') IS NOT NULL"

DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT = """
DELETE FROM document_parent_nodes p
WHERE p.document_id = %s
  AND NOT EXISTS (
      SELECT 1 FROM document_chunks c
      WHERE c.metadata->>'parent_id' = p.id::text
  )
"""

DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE = """
DELETE FROM document_parent_nodes p
WHERE (
        p.document_id IN (SELECT d.id FROM documents d WHERE d.resource_hash = %s)
        OR (p.document_id IS NULL AND p.metadata->>'resource_hash' = %s)
      )
  AND NOT EXISTS (
      SELECT 1 FROM document_chunks c
      WHERE c.metadata->>'parent_id' = p.id::text
  )
"""

TRUNCATE_PARENT_NODES = "TRUNCATE TABLE document_parent_nodes"

def parent_table_exists(cursor) -> bool: ...
def delete_unreferenced_parents(cursor, document_id) -> int: ...
def delete_unreferenced_parents_for_resource(cursor, resource_hash) -> int: ...
def truncate_parent_nodes(cursor) -> None: ...
```

- Each helper runs its statement on the caller's cursor. The delete helpers return
  `cursor.rowcount`. The helpers never commit; the caller owns the transaction.
- `delete_unreferenced_parents(cursor, None)` returns `0` and runs no SQL, because
  `= NULL` matches nothing.
- `delete_unreferenced_parents_for_resource` passes `(resource_hash, resource_hash)`. The
  second arm catches parents written with a `NULL` `document_id` (the catalog had no
  document row, `manager.py:735-739`). Those parents carry `resource_hash` in their
  metadata, because `_build_hierarchical_payload` copies it into `parent_metadata`.
- A negative `rowcount` (psycopg2 uses `-1` when the count is not known) is returned as `0`.
- `documents.resource_hash` is `UNIQUE` (`init.sql:209`), so the subquery matches at most
  one document. It does not filter on `is_deleted`: a soft-deleted document's unreferenced
  parents are garbage too.
- `parent_table_exists` returns the boolean from `PARENT_TABLE_EXISTS`. `init.sql` creates
  `document_parent_nodes` only on a fresh volume. On an upgraded volume, only
  `ensure_hierarchical_schema` creates it, and only the hierarchical write path calls that
  (`manager.py:718-720`). A statement on a missing table raises `UndefinedTable` and aborts
  the transaction, so call sites 2 and 3 MUST check first.

`manager.py` is black-clean today (`black --check` reports "unchanged"), but the SQL lives
in the module so that it is testable without a `VectorStoreManager`, and so that
`grep -rn "DELETE FROM document_parent_nodes" src/` returns exactly this one file.

## D4. Call site 1 — after the new chunks of a document are written

In `_add_to_postgres` (hierarchical branch, `manager.py:744-760`), right after
`_insert_hierarchical_file` returns, inside the same savepoint, before `RELEASE SAVEPOINT`:
call `delete_unreferenced_parents(cursor, document_id)` when `document_id` is not `None`,
else `delete_unreferenced_parents_for_resource(cursor, filehash)`. No table check is
necessary here: this branch already ran `ensure_hierarchical_schema`.

- The new chunks are already inserted in this transaction, so the statement sees them and
  keeps the new parents. Only older, unreferenced parents of this document go.
- If the delete raises, the existing `except` rolls the file back to its savepoint and
  marks the document `failed`, the same as any other error for that file.
- It is never run between the parent insert and the chunk insert (the issue's warning).
  `_insert_hierarchical_file` does both, so a call after it returns is safe.
- Concurrency: another ingest writes its parents and its chunks in one transaction. Under
  READ COMMITTED, its parents are not visible to this statement before its chunks are, so
  this statement cannot delete a parent that another run is about to reference.

Sum the deleted counts across the run in a local variable. After the file loop, log one
INFO line: `"Deleted %d unreferenced parent nodes after re-ingest"`. Log it only when
`hierarchical_chunking` is on.

## D5. Call site 2 — after the chunk delete for a resource

In `_remove_from_postgres` (`manager.py:516-535`), call `parent_table_exists(cursor)` once
before the hash loop. If it is `True`, then after the `DELETE FROM document_chunks` for each
`resource_hash`, call `delete_unreferenced_parents_for_resource(cursor, resource_hash)`,
before the existing `conn.commit()`. If it is `False`, run no parent statement. Sum the
counts and log one INFO line after the commit:
`"Deleted %d unreferenced parent nodes for %d removed resources"`.

Run this call whether or not `hierarchical_chunking` is on. A stack that turned
hierarchical chunking off still has old parents, and the statement is cheap when the
document has none.

On a re-ingest of a changed document, `_sync_vectorstore` calls `_remove_from_postgres` and
then `_add_to_postgres` (`manager.py:357`, `:369`). Call site 2 deletes the old parents when
the old chunks go. Call site 1 is the guard for a re-insert that did not go through a
remove, for example a retry after a failed file.

## D5b. Call site 3 — the reset path

`delete_existing_collection_if_reset` (`manager.py:217-246`) runs
`TRUNCATE TABLE document_chunks CASCADE`. No foreign key points from
`document_parent_nodes` to `document_chunks`, so the CASCADE does not reach parents, and
after the truncate no parent can be referenced. Right after that statement, if
`parent_table_exists(cursor)`, call `truncate_parent_nodes(cursor)` and log
`"Truncated document_parent_nodes table"` at INFO.

## D5c. The chunk parent-id index on upgraded volumes

`ensure_hierarchical_schema` (`src/data_manager/vectorstore/schema.py:36-46`) creates the
table and `idx_parent_nodes_document`, but not `idx_chunks_parent_id`, which only
`init.sql:346-347` creates. Without it, each `NOT EXISTS` scans `document_chunks`. Add
`ensure_chunks_parent_id_index(cursor)` to `schema.py`, which runs
`CREATE INDEX IF NOT EXISTS idx_chunks_parent_id ON document_chunks ((metadata->>'parent_id'))`,
with the same name and expression as `init.sql`.

Do not put this statement in `ensure_hierarchical_schema`. The chat retrieval path
(`hierarchical_retriever.py:159`) calls that function and closes its connection without a
commit, so the index build rolls back and repeats on each chat turn. Call the new function
only from the two write paths that commit:

- `_add_to_postgres`, in the setup step after `ensure_hierarchical_schema` and before its
  `conn.commit()`.
- `_remove_from_postgres`, when `parent_table_exists` is true, before the first parent
  delete. `_sync_vectorstore` calls the remove path before the add path, and a sync can
  remove resources and add none.

## Known gap — out of scope

Two chat-app source-removal paths delete chunks directly and never touch parents:
`src/interfaces/chat_app/app.py:6178-6185` (git repository) and `app.py:6812-6819` (Jira
project). `PostgresVectorStore.delete()` (`postgres_vectorstore.py:590-620`) is the same.
Those documents are soft-deleted and never re-ingested, so no call site above runs for
them. This change does not cover them; issue #600 tracks them, and the docs say so.

## D6. Tests

The unit suite has no Postgres. Tests use a stateful fake cursor in
`tests/unit/test_parent_nodes.py` that holds `parents` (id → document_id) and `chunks`
(list of dicts with `collection` and `parent_id`), and that applies the two module
constants by identity (`sql is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT`)
with the D1 predicate in Python. The fake sets `rowcount`, answers `PARENT_TABLE_EXISTS`, and understands the parent
`INSERT ... RETURNING id` and the chunk insert, so a test can drive `_add_to_postgres`
twice, and `_remove_from_postgres` then `_add_to_postgres`, and then read the rows that are
left. This proves the call order and
the predicate the code intends. It does not prove the SQL text against Postgres; the PR
body records that limit. The SQL text is pinned by tests that assert the `NOT EXISTS`
predicate and the absence of any collection filter.

## D7. Docs

Add a section `## Orphaned parent nodes after re-ingest` to
`docs/docs/troubleshooting.md` with:

1. The count query from the issue thread (total, referenced, orphaned).
2. A CAUTION before the cleanup: "CAUTION: Run this cleanup only between benchmark
   campaigns, and record the before and after counts. The corpus fingerprint hashes only
   parents that a chunk references, so the cleanup does not change it."
3. The cleanup:
   `DELETE FROM document_parent_nodes p WHERE NOT EXISTS (SELECT 1 FROM document_chunks c WHERE c.metadata->>'parent_id' = p.id::text);`
4. One paragraph: a re-ingest, a removed document, and `reset_collection` now delete their
   own orphans. A git or Jira source removal from the chat UI still leaves orphans (the known
   gap above), so run the count query after one.

The fingerprint statement is checked against `CORPUS_STATE_V2_QUERY`
(`src/utils/benchmark_provenance.py:388-396`), which joins parents through in-scope chunks.
The v1 query is gone (`tests/unit/test_benchmark_corpus_fingerprint.py:303-305`).
