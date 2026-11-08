# Design — #667

Anchors are on `origin/dev` `a18b979c`. Loop 1 applied every change below in a scratch
worktree and ran it: the exact code is in `tasks.md` "Appendix". Copy it; do not rewrite it.

## D1. Startup helper (in `src/bin/service_data_manager.py`)

The issue's acceptance criterion says `grep -rn "ensure_chunks_parent_id_index(" src` shows
only the definition (`schema.py`) and the startup call site (`service_data_manager.py`). So the
helper lives in `src/bin/service_data_manager.py`, not in a new module. Unit tests import that
module directly (`import src.bin.service_data_manager as service` works in the unit suite), so
diff-cover sees the new lines.

```python
def build_parent_id_index_at_startup(factory) -> bool:
    try:
        with factory.connection_pool.get_connection() as conn:
            with conn.cursor() as cursor:
                if not parent_nodes.parent_table_exists(cursor):
                    conn.rollback()
                    logger.info(...)          # names document_parent_nodes
                    return False
                ensure_chunks_parent_id_index(cursor)
            conn.commit()
    except Exception:
        logger.warning(..., exc_info=True)    # names idx_chunks_parent_id
        return False
    logger.info("Ensured idx_chunks_parent_id on document_chunks")
    return True
```

- The guard is the existing `parent_nodes.parent_table_exists` probe
  (`SELECT to_regclass('document_parent_nodes') IS NOT NULL`), as the issue plan step 3 asks.
- The skip path calls `rollback()` to end the read transaction of the probe, and never commits.
- `factory.connection_pool` is the same pool that `DataManager(factory=factory)` uses through
  `factory.config_service`, so the call opens no extra pool.
- New imports in `service_data_manager.py`: `from src.data_manager.vectorstore import
  parent_nodes` and `from src.data_manager.vectorstore.schema import
  ensure_chunks_parent_id_index`.

## D2. Failure at start: log and continue

The issue does not say what a failed build does. `main()` must not crash on it:

- Before this change, the data manager ran no DDL at start. A crash on a failed index build
  would turn a speed problem into an outage (the container stops and restarts).
- Without the index, the parent deletes are correct but slow. That is the state of every
  upgraded volume before PR #663.

So the helper catches `Exception` around the connection and the build, logs a WARNING with
`exc_info=True` whose message contains `idx_chunks_parent_id`, and returns `False`. The
`ConnectionPool.get_connection` context manager rolls back on a `psycopg2.Error` and returns the
connection to the pool, so the helper needs no rollback in its `except`. The PR body must
name this choice for the human reviewer.

## D3. Call site and in-line removals

- `main()`: one line, `build_parent_id_index_at_startup(factory)`, directly after
  `PostgresServiceFactory.set_instance(factory)` (`:38`) and before `DataManager(...)` (`:44`).
- `manager.py:36`: the import becomes `from .schema import ensure_hierarchical_schema`.
- `manager.py:544-545`: delete `if table_exists:` and `ensure_chunks_parent_id_index(cursor)`.
  Keep `table_exists = parent_nodes.parent_table_exists(cursor)`; the parent deletes and the log
  below still use it.
- `manager.py:770`: delete `ensure_chunks_parent_id_index(cursor)`. Keep
  `ensure_hierarchical_schema(cursor)` and the `conn.commit()` of the setup step.
- `parent_nodes.py:7`: delete `from .schema import ensure_chunks_parent_id_index` (and the blank
  line after it). `parent_nodes.py:61-63`: delete the two comment lines and the call.
- `schema.py`: docstrings only. `ensure_hierarchical_schema` says the index build is called only
  by the data-manager start; `ensure_chunks_parent_id_index` says only the data-manager start
  calls it, in its own committed transaction.

## D4. Known gap (accepted by the operator decision)

The table guard means a volume with no `document_parent_nodes` at start gets no index at that
start. The first hierarchical ingest then creates the table (`ensure_hierarchical_schema`), and
the index waits for the next data-manager start. A fresh volume is not affected: `init.sql`
creates both the table and the index. Record this in the PR body; do not change the guard.

## D5. Tests

New file `tests/unit/test_service_data_manager_parent_id_index.py` (5 tests, exact text in the
appendix). Fakes: a recording cursor that answers the table probe, a connection that records
`COMMIT` and `ROLLBACK`, a pool with a `get_connection` context manager, and a factory that
exposes `connection_pool`.

1. Table exists: the probe runs first, exactly one `CREATE INDEX IF NOT EXISTS
   idx_chunks_parent_id` on `document_chunks ((metadata->>'parent_id'))` with no `CONCURRENTLY`,
   then exactly one `COMMIT` after it; returns `True`.
2. Table absent: the only statement is the probe, no `COMMIT`, an INFO record that names
   `document_parent_nodes`; returns `False`.
3. The build raises: no `COMMIT`, a WARNING that names `idx_chunks_parent_id`; returns `False`
   and does not raise.
4. `factory.connection_pool` raises: a WARNING that names `idx_chunks_parent_id`; returns
   `False` and does not raise.
5. `main()` order: monkeypatch `setup_logging`, `read_secret`, `PostgresServiceFactory`, the
   three config getters, `build_parent_id_index_at_startup`, and `DataManager` (its `__init__`
   raises a private exception to stop `main()`). The recorded order is `set_instance`,
   `build_index`, `data_manager`, and all three receive the same factory object. `main()` writes
   three API-key environment variables, so the test calls `monkeypatch.setenv` on each first,
   and monkeypatch restores them.

Rewrite three tests in place (same position, new name, no duplicate name):

- `tests/unit/test_parent_nodes.py::test_delete_unreferenced_parents_for_resources_ensures_parent_id_index_first`
  becomes `test_delete_unreferenced_parents_for_resources_runs_no_index_ddl`: two hashes, three
  parents, one parent kept by a chunk; returns `2`, parent `3` survives, no statement contains
  `CREATE INDEX` or `idx_chunks_parent_id`.
- `tests/unit/test_vectorstore_manager_hierarchical.py::test_remove_from_postgres_ensures_parent_id_index_before_parent_delete`
  becomes `test_remove_from_postgres_deletes_parents_without_index_ddl`: no index DDL, the
  resource parent delete runs, one commit.
- `tests/unit/test_vectorstore_manager_hierarchical.py::test_add_to_postgres_hierarchical_ensures_parent_id_index_before_commit`
  becomes `test_add_to_postgres_hierarchical_runs_no_parent_id_index_ddl`: no event contains
  `idx_chunks_parent_id`, and the setup `COMMIT` still comes before the document parent delete.

`test_remove_from_postgres_skips_parent_id_index_when_table_absent` stays as it is; it still
passes.

## D6. Measured results (scratch worktree, `origin/dev` `a18b979c`)

Fast loop = `python -m pytest -q tests/unit/test_service_data_manager_parent_id_index.py
tests/unit/test_parent_nodes.py tests/unit/test_vectorstore_manager_hierarchical.py
tests/unit/test_vectorstore_schema.py`.

- Red (tests applied, source unchanged): `8 failed, 76 passed` — the 5 new tests and the 3
  rewritten tests.
- Green (tests and source applied): `84 passed`.
- Full unit suite with the change: `7890 passed, 60 skipped, 1 xfailed`.
- `diff-cover coverage.xml --compare-branch=origin/dev`: `src/bin/service_data_manager.py`
  100 %, `src/data_manager/vectorstore/manager.py` 100 %, total 19 lines, 0 missing.
- `black --check` and `isort --check-only` on `src tests scripts`: clean.
