# Build idx_chunks_parent_id at data-manager start, not in request paths (#667)

## Why

`idx_chunks_parent_id` is an expression index on `document_chunks ((metadata->>'parent_id'))`.
`src/cli/templates/init.sql:346` creates it only on a fresh Postgres volume, so an upgraded
volume can lack it. Without it, each `NOT EXISTS` in the parent-delete queries
(`src/data_manager/vectorstore/parent_nodes.py`) scans all of `document_chunks`.

Today `ensure_chunks_parent_id_index` (`src/data_manager/vectorstore/schema.py:59`) builds the
index lazily, in-line, from three places on `origin/dev` `a18b979c`:

- `src/data_manager/vectorstore/manager.py:545` (`_remove_from_postgres`, ingest removal)
- `src/data_manager/vectorstore/manager.py:770` (`_add_to_postgres`, hierarchical setup step)
- `src/data_manager/vectorstore/parent_nodes.py:63` (`delete_unreferenced_parents_for_resources`,
  called by the chat-app git and Jira source-removal endpoints)

On a large upgraded volume, the first removal runs `CREATE INDEX` (a full scan and sort, with a
SHARE lock that blocks writes to `document_chunks`) while the HTTP request waits. A review of
PR #663 rated this in-request build [high].

## What Changes

Operator decision of 2026-10-20 (issue body, "Decision"):

- Add `build_parent_id_index_at_startup(factory) -> bool` to `src/bin/service_data_manager.py`.
  It builds the index once, with the existing plain `CREATE INDEX IF NOT EXISTS` (no
  `CONCURRENTLY`), through `ensure_chunks_parent_id_index`, in its own committed transaction.
  It builds the index only when `document_parent_nodes` exists. When the table is absent, it
  runs no DDL and logs at INFO.
- Call it from `main()` directly after `PostgresServiceFactory.set_instance(factory)` and
  before `DataManager(...)` is built.
- Remove all three in-line calls and the two imports that only they use. Startup is the single
  owner; no request or ingest path runs DDL.
- Update `docs/docs/troubleshooting.md` ("Orphaned parent nodes after re-ingest"): a restart of
  the data-manager container creates the index on an upgraded volume.

Design choice that the issue does not settle (see `design.md` D2): if the connection or the
build fails at start, the helper logs a WARNING and returns `False`, and the service still
starts. The index is a speed aid, not a correctness need. Without it the parent deletes are
slow but correct, which is the state of every upgraded volume before PR #663.

## Spec housekeeping

The unarchived change `fix-issue-411-prune-orphan-parent-nodes` has the requirement "The
committed write paths create the parent-id index". This change makes it false, so this change
deletes that requirement block from the #411 delta and adds the replacement requirement here
(ADDED, because the #411 delta is not archived into `openspec/specs/` yet). The #411 scenario
"Chat retrieval path" moves into the new requirement unchanged in meaning.

## Out of scope

- A deployment that turns on hierarchical chunking on an upgraded volume that has no
  `document_parent_nodes` table: the first hierarchical ingest creates the table, and the index
  is built at the next data-manager start. The operator decision keeps the table guard, so the
  index can be absent until that restart (design D4).
- `CREATE INDEX CONCURRENTLY`, `statement_timeout`, and any change to `init.sql`.
- The chat-app service (`src/interfaces/chat_app/app.py`): it shares the Postgres database, so
  the index that the data manager builds serves its removal endpoints. No edit there.

## Impact

- Code: `src/bin/service_data_manager.py`, `src/data_manager/vectorstore/manager.py`,
  `src/data_manager/vectorstore/parent_nodes.py`, `src/data_manager/vectorstore/schema.py`
  (docstrings only).
- Tests: new `tests/unit/test_service_data_manager_parent_id_index.py`; three tests rewritten
  in place in `tests/unit/test_parent_nodes.py` and
  `tests/unit/test_vectorstore_manager_hierarchical.py`.
- Docs: `docs/docs/troubleshooting.md`.
- OpenSpec: the #411 delta loses one superseded requirement block.
- No schema change, no config change, no deploy change.
