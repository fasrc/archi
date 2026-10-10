# Delete unreferenced parent nodes when the chat app removes a git or Jira source (#600)

## Why

#411 (PR #601) made re-ingest and `_remove_from_postgres` delete the
`document_parent_nodes` rows that no chunk references. It left out the two chat-app
source-removal paths:

- `src/interfaces/chat_app/app.py:6178-6191` (git repository removal) and
  `app.py:6812-6825` (Jira project removal) run
  `DELETE FROM document_chunks WHERE metadata->>'resource_hash' = ANY(%s)` and then
  soft-delete the `documents` rows. They never touch `document_parent_nodes`.
- The `documents` row stays (soft delete), so the `ON DELETE CASCADE` on
  `document_parent_nodes.document_id` (`src/cli/templates/init.sql:326`) never fires.
- The removed hashes are then in neither the vectorstore nor the catalog, so
  `_remove_from_postgres` never runs for them. Their parent rows stay forever.

## What Changes

- Add `delete_unreferenced_parents_for_resources(cursor, resource_hashes) -> int` to
  `src/data_manager/vectorstore/parent_nodes.py`. It returns `0` and runs no SQL for an
  empty list, returns `0` after one existence probe when the parent table is missing, and
  otherwise calls the existing `delete_unreferenced_parents_for_resource` once per hash
  and returns the total.
- Call it from the two `app.py` removal sites, right after the chunk delete, inside the
  `if hashes_to_delete:` block and the same transaction. The `app.py` change is one call
  plus one log line per site and one import — no new logic there (unit tests do not
  import `app.py`).

## Out of scope

- Issue plan step 3 (`PostgresVectorStore.delete()`): no caller in `src/` calls
  `.delete(ids=...)` or `.delete(document_id=...)` on dev `6156b760`
  (`git grep -nE "\.delete\((ids|document_id)=" -- src/` is empty). It does not delete
  by resource, so the issue's condition ("if its callers delete by resource") is false.
- Cleanup of parent rows that earlier removals already orphaned. A later re-ingest or
  `_remove_from_postgres` does not reach them; a one-off SQL backfill is a human decision.

## Impact

- Code: `src/data_manager/vectorstore/parent_nodes.py`, `src/interfaces/chat_app/app.py`
  (two call sites, one import).
- Tests: `tests/unit/test_parent_nodes.py` (append).
- No schema change, no config change, no deploy change.
