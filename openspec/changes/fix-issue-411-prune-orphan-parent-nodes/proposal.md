# Delete unreferenced parent nodes on re-ingest (#411)

## Why

The hierarchical ingest inserts a full set of parent rows into `document_parent_nodes` on
every run, and nothing deletes the previous set.

- `_insert_hierarchical_file` (`src/data_manager/vectorstore/manager.py:973`) inserts each
  parent with `INSERT INTO document_parent_nodes ... RETURNING id` (`manager.py:995`) and
  stamps the new serial id onto each child's `metadata.parent_id`.
- `_remove_from_postgres` (`manager.py:516`) deletes only `document_chunks` rows, by
  `metadata->>'resource_hash'` and collection (`manager.py:522-529`).
- `grep -rn "DELETE FROM document_parent_nodes" src/` returns nothing.
- The `document_id ... ON DELETE CASCADE` foreign key (`src/cli/templates/init.sql:326`)
  never fires on a re-ingest, because the `documents` row stays.

Measured on FASRC dev on 2026-09-16: 29,847 parent rows, 27,457 orphaned (92 %), 73.5 MB —
the largest table in the retrieval schema. Each re-ingest adds about 2,390 more orphans.

## What Changes

- Add a small module `src/data_manager/vectorstore/parent_nodes.py` that holds the new
  `DELETE FROM document_parent_nodes` statements, a table check, and a truncate, with
  helpers that run them on a caller's cursor.
- In the hierarchical insert path of `_add_to_postgres`, after
  `_insert_hierarchical_file` writes the document's new parents and chunks (inside the same
  savepoint), delete that document's parents that no chunk references.
- In `_remove_from_postgres`, after the chunk delete for each resource hash, delete the
  now-unreferenced parents of the document with that `resource_hash`.
- In `delete_existing_collection_if_reset`, truncate `document_parent_nodes` after the
  `document_chunks` truncate, because no parent can be referenced after it.
- Add `ensure_chunks_parent_id_index`, which creates `idx_chunks_parent_id`, and call it
  from the committed setup step of `_add_to_postgres` and before the cleanup in
  `_remove_from_postgres`, so the unreferenced check uses an index on upgraded volumes.
- Skip the parent statements in the remove and reset paths when the table does not exist.
- Log one INFO summary line per call with the number of parent rows deleted.
- Add an operator section to `docs/docs/troubleshooting.md` with a one-off `psql` cleanup
  for existing deployments, the count queries, and a CAUTION about corpus fingerprints.

## Out of scope

- A startup self-heal or any automatic cleanup of rows that exist today.
- `CORPUS_STATE_V2_QUERY` and the corpus-fingerprint semantics (the v1 query is already
  removed).
- The chat-app git and Jira source-removal paths (`src/interfaces/chat_app/app.py`) and
  `PostgresVectorStore.delete()`, which delete chunks directly. Issue #600 tracks
  them.
- The hierarchical retriever (it reaches parents through the chunks' `parent_id`).
- The operator cleanup run on dev (post-merge, `needs-deploy`).

## Impact

- Code: `src/data_manager/vectorstore/parent_nodes.py` (new),
  `src/data_manager/vectorstore/manager.py` (three thin call sites),
  `src/data_manager/vectorstore/schema.py` (one index statement).
- Tests: `tests/unit/test_parent_nodes.py` (new),
  `tests/unit/test_vectorstore_manager_hierarchical.py`, the `schema.py` test.
- Docs: `docs/docs/troubleshooting.md`.
- Corpus fingerprint: `CORPUS_STATE_V2_QUERY` hashes a parent only when an in-scope chunk
  references it, so the new deletes and the cleanup do not change the fingerprint.
