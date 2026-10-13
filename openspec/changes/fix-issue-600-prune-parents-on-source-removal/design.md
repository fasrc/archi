# Design — #600

## D1. Helper API

In `src/data_manager/vectorstore/parent_nodes.py`, after
`delete_unreferenced_parents_for_resource`:

```python
def delete_unreferenced_parents_for_resources(cursor, resource_hashes) -> int:
    hashes = list(resource_hashes or ())
    if not hashes:
        return 0
    if not parent_table_exists(cursor):
        return 0
    ensure_chunks_parent_id_index(cursor)
    return sum(delete_unreferenced_parents_for_resource(cursor, h) for h in hashes)
```

- `None` or an empty list: `0`, no SQL at all (not even the existence probe).
- Missing table: one `PARENT_TABLE_EXISTS` query, then `0`. The removal must not break
  on a volume that predates hierarchical chunking (acceptance criterion 2).
- The helper never commits. The caller owns the transaction (module docstring rule).
- When the table exists, it calls `ensure_chunks_parent_id_index` once, before the first
  delete, as `_remove_from_postgres` does. An upgraded volume can lack that index
  (`docs/docs/troubleshooting.md`), and then each `NOT EXISTS` scans all of
  `document_chunks` once for each removed hash. The statement is a no-op when the index
  exists. Both chat-app callers commit, so the build persists.

## D2. Predicate is unchanged

`DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE` matches parents by
`documents.resource_hash` (no `is_deleted` filter) OR by a `NULL` `document_id` plus
`metadata.resource_hash`. As a result, the call works both before and after the
soft-delete `UPDATE`. Call it after the chunk delete, so the chunks of the removed source
no longer hold their parents. A parent that a chunk in another collection still
references survives (the `NOT EXISTS` has no collection filter).

## D3. Call sites in app.py (thin)

Add one module import next to the other `src.data_manager` imports (near
`app.py:70-71`):

```python
from src.data_manager.vectorstore import parent_nodes
```

Confirm first that the name `parent_nodes` is not already bound in `app.py`
(`grep -n "parent_nodes" src/interfaces/chat_app/app.py`); if it is, import the function
by name instead.

Inside each `if hashes_to_delete:` block, directly after the `logger.info(...)` that
reports `chunks_deleted`:

```python
parents_deleted = parent_nodes.delete_unreferenced_parents_for_resources(
    cursor, hashes_to_delete
)
logger.info(f"Deleted {parents_deleted} unreferenced parent nodes")
```

No other change to `app.py`. These lines are not covered by unit tests (diff-cover
counts them as uncovered); the helper tests carry the patch-coverage ratio. Keep the
`app.py` lines to these few so patch coverage stays at or above 80 %.

## D4. Tests

Append to `tests/unit/test_parent_nodes.py` and reuse its `_FakeCursor` (stateful:
parents, chunks, documents, `table_exists`, `rowcount`). Read the fake first and drive it
the same way the existing `..._for_resource` tests do. New test names must be unique in
the file (`grep -n "def test_" tests/unit/test_parent_nodes.py`).

1. Two hashes, each with an orphan parent and a parent that a live chunk references:
   both orphans are deleted, both referenced parents survive, the return value is `2`.
2. A parent of a removed hash that a chunk in ANOTHER collection references survives.
3. Empty list and `None`: return `0`, the cursor recorded no `execute` call.
4. `table_exists=False`: return `0`, exactly one `execute` (the existence probe), no
   `DELETE` statement ran.
5. The helper never calls `commit` (mirror `test_helpers_never_commit`).
