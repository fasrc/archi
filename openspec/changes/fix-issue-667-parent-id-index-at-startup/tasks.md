# Tasks — build idx_chunks_parent_id at data-manager start (#667)

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
tests, watch them fail, write the smallest fix, run the gate, commit — in ONE task. Never end a
task with the suite red, and never use `--no-verify`.

Standing notes for every task:

- **Scope.** Files this change may edit: `src/bin/service_data_manager.py`,
  `src/data_manager/vectorstore/manager.py`, `src/data_manager/vectorstore/parent_nodes.py`,
  `src/data_manager/vectorstore/schema.py` (docstrings only),
  `tests/unit/test_service_data_manager_parent_id_index.py` (new),
  `tests/unit/test_parent_nodes.py`, `tests/unit/test_vectorstore_manager_hierarchical.py`,
  `docs/docs/troubleshooting.md`, and this change's `tasks.md`. Do NOT edit `init.sql`,
  `src/interfaces/chat_app/app.py`, `deploy/**`, `.github/**`, or any control-plane file.
- **Read `design.md` first.** Loop 1 ran every code block in the Appendix below in a scratch
  worktree (red `8 failed, 76 passed`, green `84 passed`, full suite `7890 passed`, patch
  coverage 100 %). **Copy the Appendix code exactly. Do not rewrite it.**
- **Fast loop:** `python -m pytest -q tests/unit/test_service_data_manager_parent_id_index.py tests/unit/test_parent_nodes.py tests/unit/test_vectorstore_manager_hierarchical.py tests/unit/test_vectorstore_schema.py`
- **Rewrite in place.** The three changed tests replace the old functions at the same position.
  Never append a second function with an existing name. After the edit, each old name must
  return `0` from `grep -c` and each new name must return `1`.
- **Format before you stage.** Run `black` and `isort` on the changed `.py` files, then
  `git add`, then commit, and confirm `git status --porcelain` is empty.

## 1. Startup build and in-line removal

- [x] 1.1 Create `tests/unit/test_service_data_manager_parent_id_index.py` from Appendix A. In `tests/unit/test_parent_nodes.py`, replace the function `test_delete_unreferenced_parents_for_resources_ensures_parent_id_index_first` (the last function in the file) with Appendix B. In `tests/unit/test_vectorstore_manager_hierarchical.py`, replace `test_remove_from_postgres_ensures_parent_id_index_before_parent_delete` with Appendix C1, and rename and edit `test_add_to_postgres_hierarchical_ensures_parent_id_index_before_commit` as Appendix C2 says. Run the fast loop and record the red result: exactly `8 failed, 76 passed` (the 5 tests of the new file and the 3 rewritten tests). Then make the source changes of Appendix D (design D1 and D3). Run the fast loop green: exactly `84 passed`. Run `grep -rn "ensure_chunks_parent_id_index(" src` and confirm it prints exactly two lines: the definition in `src/data_manager/vectorstore/schema.py` and the call in `src/bin/service_data_manager.py`. Run `grep -rn "ensure_chunks_parent_id_index" src/data_manager/vectorstore/manager.py src/data_manager/vectorstore/parent_nodes.py` and confirm it prints nothing. Run `bash scripts/gate.sh` green, then commit tests and source together as `fix(#667): build idx_chunks_parent_id at data-manager start, not in request paths` (the red run and the fix are one commit).

## 2. Docs

- [x] 2.1 In `docs/docs/troubleshooting.md`, section "Orphaned parent nodes after re-ingest", replace the paragraph that starts "First, make sure that the parent-id index exists." with Appendix E. Keep the SQL block and the sentence "The index build blocks writes to `document_chunks` until it completes." below it unchanged. Run `bash scripts/gate.sh` green, then commit as `docs(#667): the data manager builds the parent-id index at start`.

## 3. Publish

- [x] 3.1 Run `bash scripts/gate.sh` one more time on the branch tip and confirm it is green. Tick this box (3.1) in this `tasks.md`, commit the tick through the hook as `chore(#667): mark the publish task done`, and only then push: `git push -u origin fix/issue-667-parent-id-index-at-startup` (the branch was created from `origin/dev` and tracks it, so `-u` is required to set the right upstream). Confirm `git rev-parse HEAD` equals `git rev-parse origin/fix/issue-667-parent-id-index-at-startup`. Open the PR with `gh pr create --repo fasrc/archi --base dev --head fix/issue-667-parent-id-index-at-startup --title "fix: build idx_chunks_parent_id at data-manager start, not in request paths (#667)" --body-file <file>`. The PR body must contain the line `closes #667` (in the body, not only the title), and must state: (a) the three in-line calls removed (`manager.py` `_remove_from_postgres` and `_add_to_postgres`, `parent_nodes.py` `delete_unreferenced_parents_for_resources`); (b) the design D2 choice — a failed build at start logs a WARNING and the service still starts — as a decision for the reviewer to confirm; (c) the design D4 known gap (no index until the next start when `document_parent_nodes` is created after start); (d) that the #411 OpenSpec delta lost its superseded requirement "The committed write paths create the parent-id index". No `Co-Authored-By` trailer. Do not merge. Confirm the PR links the issue: `gh pr view <n> --repo fasrc/archi --json closingIssuesReferences` lists `667`.

## Appendix (reference only — no checkboxes)

### A. New file `tests/unit/test_service_data_manager_parent_id_index.py`

```python
"""The data-manager start builds idx_chunks_parent_id once (issue #667).

No request or ingest path runs DDL. ``build_parent_id_index_at_startup`` in
``src/bin/service_data_manager.py`` is the single owner of the index build, and
``main()`` calls it after ``PostgresServiceFactory.set_instance`` and before it
builds the ``DataManager``.
"""

import logging
from contextlib import contextmanager

import pytest

import src.bin.service_data_manager as service
from src.data_manager.vectorstore import parent_nodes

_INDEX_DDL = "CREATE INDEX IF NOT EXISTS idx_chunks_parent_id"
_PROBE = " ".join(parent_nodes.PARENT_TABLE_EXISTS.split())


class _StartupCursor:
    """Records each statement; answers the parent-table probe with table_exists."""

    def __init__(self, events, table_exists, fail_on_index):
        self.events = events
        self.table_exists = table_exists
        self.fail_on_index = fail_on_index

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.events.append(norm)
        if self.fail_on_index and _INDEX_DDL in norm:
            raise RuntimeError("canceling statement due to lock timeout")

    def fetchone(self):
        return (self.table_exists,)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _StartupConnection:
    def __init__(self, events, cursor):
        self.events = events
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        self.events.append("COMMIT")

    def rollback(self):
        self.events.append("ROLLBACK")


class _StartupPool:
    def __init__(self, connection):
        self._connection = connection

    @contextmanager
    def get_connection(self):
        yield self._connection


class _StartupFactory:
    """Stands in for PostgresServiceFactory: exposes connection_pool only."""

    def __init__(self, table_exists=True, fail_on_index=False):
        self.events = []
        cursor = _StartupCursor(self.events, table_exists, fail_on_index)
        self.connection_pool = _StartupPool(_StartupConnection(self.events, cursor))


def test_startup_builds_parent_id_index_and_commits_when_table_exists():
    factory = _StartupFactory(table_exists=True)

    built = service.build_parent_id_index_at_startup(factory)

    assert built is True
    assert factory.events[0] == _PROBE
    index_positions = [i for i, e in enumerate(factory.events) if _INDEX_DDL in e]
    assert len(index_positions) == 1
    index_sql = factory.events[index_positions[0]]
    assert "document_chunks" in index_sql
    assert "metadata->>'parent_id'" in index_sql
    assert "CONCURRENTLY" not in index_sql
    assert factory.events.count("COMMIT") == 1
    assert index_positions[0] < factory.events.index("COMMIT")


def test_startup_runs_no_ddl_when_parent_table_is_absent(caplog):
    factory = _StartupFactory(table_exists=False)

    with caplog.at_level(logging.INFO, logger=service.__name__):
        built = service.build_parent_id_index_at_startup(factory)

    assert built is False
    assert [e for e in factory.events if e not in ("COMMIT", "ROLLBACK")] == [_PROBE]
    assert "COMMIT" not in factory.events
    assert any(
        r.levelno == logging.INFO and "document_parent_nodes" in r.getMessage()
        for r in caplog.records
    )


def test_startup_logs_and_continues_when_index_build_fails(caplog):
    factory = _StartupFactory(table_exists=True, fail_on_index=True)

    with caplog.at_level(logging.WARNING, logger=service.__name__):
        built = service.build_parent_id_index_at_startup(factory)

    assert built is False
    assert "COMMIT" not in factory.events
    assert any(
        r.levelno == logging.WARNING and "idx_chunks_parent_id" in r.getMessage()
        for r in caplog.records
    )


def test_startup_logs_and_continues_when_no_connection(caplog):
    class _NoPoolFactory:
        @property
        def connection_pool(self):
            raise RuntimeError("could not connect to server")

    with caplog.at_level(logging.WARNING, logger=service.__name__):
        built = service.build_parent_id_index_at_startup(_NoPoolFactory())

    assert built is False
    assert any(
        r.levelno == logging.WARNING and "idx_chunks_parent_id" in r.getMessage()
        for r in caplog.records
    )


def test_service_main_builds_index_after_set_instance_and_before_data_manager(
    monkeypatch, tmp_path
):
    events = []

    class _StopMain(Exception):
        pass

    class _FakeFactory:
        @classmethod
        def from_env(cls, password_override=None):
            return cls()

        @classmethod
        def set_instance(cls, factory):
            events.append(("set_instance", factory))

    def _fake_build(factory):
        events.append(("build_index", factory))
        return True

    class _FakeDataManager:
        def __init__(self, *args, **kwargs):
            events.append(("data_manager", kwargs.get("factory")))
            raise _StopMain

    # main() writes these three keys; setenv first so monkeypatch restores them.
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HUGGING_FACE_HUB_TOKEN"):
        monkeypatch.setenv(key, "unchanged")
    monkeypatch.setattr(service, "setup_logging", lambda: None)
    monkeypatch.setattr(service, "read_secret", lambda name: "secret")
    monkeypatch.setattr(service, "PostgresServiceFactory", _FakeFactory)
    monkeypatch.setattr(service, "get_full_config", lambda: {})
    monkeypatch.setattr(service, "get_services_config", lambda: {})
    monkeypatch.setattr(
        service, "get_global_config", lambda: {"DATA_PATH": str(tmp_path)}
    )
    monkeypatch.setattr(service, "build_parent_id_index_at_startup", _fake_build)
    monkeypatch.setattr(service, "DataManager", _FakeDataManager)

    with pytest.raises(_StopMain):
        service.main()

    assert [name for name, _ in events] == [
        "set_instance",
        "build_index",
        "data_manager",
    ]
    factory = events[0][1]
    assert isinstance(factory, _FakeFactory)
    assert all(arg is factory for _, arg in events)
```

### B. `tests/unit/test_parent_nodes.py` — replaces the last function of the file

```python
def test_delete_unreferenced_parents_for_resources_runs_no_index_ddl():
    """The data-manager start owns the idx_chunks_parent_id build (#667), so the
    plural helper runs no DDL and still deletes the orphans of every hash."""
    documents = [
        {"id": 10, "resource_hash": "hash-a"},
        {"id": 11, "resource_hash": "hash-b"},
    ]
    parents = {
        1: {"document_id": 10, "metadata": {}},
        2: {"document_id": 11, "metadata": {}},
        3: {"document_id": 11, "metadata": {}},
    }
    chunks = [{"collection": "c", "parent_id": 3}]
    cursor = _FakeCursor(parents=parents, chunks=chunks, documents=documents)

    deleted = parent_nodes.delete_unreferenced_parents_for_resources(
        cursor, ["hash-a", "hash-b"]
    )

    assert deleted == 2
    assert set(cursor.parents) == {3}
    sqls = [" ".join(s.split()) for s in cursor.executed_sqls()]
    assert not any("CREATE INDEX" in s for s in sqls)
    assert not any("idx_chunks_parent_id" in s for s in sqls)
```

### C1. `tests/unit/test_vectorstore_manager_hierarchical.py` — replaces `test_remove_from_postgres_ensures_parent_id_index_before_parent_delete`

```python
def test_remove_from_postgres_deletes_parents_without_index_ddl(monkeypatch):
    """The data-manager start owns the idx_chunks_parent_id build (#667): a
    removal-only sync runs no DDL and still deletes the unreferenced parents
    in the transaction that commits."""
    from src.data_manager.vectorstore import parent_nodes

    manager = _make_manager()
    fake_cursor = _RemoveFakeCursor(table_exists=True, parent_rowcount=1)
    fake_conn = _make_remove_conn(fake_cursor)
    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)

    manager._remove_from_postgres(["hash-1"])

    sqls = [s for s, _ in fake_cursor.executed]
    assert not any("CREATE INDEX" in " ".join(s.split()) for s in sqls)
    assert not any("idx_chunks_parent_id" in s for s in sqls)
    assert parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE in sqls
    fake_conn.commit.assert_called_once()
```

### C2. `tests/unit/test_vectorstore_manager_hierarchical.py` — edit `test_add_to_postgres_hierarchical_ensures_parent_id_index_before_commit`

Keep the body (fakes, monkeypatches, the `_add_to_postgres` call). Change only three things:

1. Rename the function to `test_add_to_postgres_hierarchical_runs_no_parent_id_index_ddl(`.
2. Replace its docstring with:

```python
    """The data-manager start owns the idx_chunks_parent_id build (#667): the
    add path runs no index DDL, and its setup step still commits before the
    parent cleanup runs."""
```

3. Replace its final assertion block (from `index_idx = next(` to the end of the function) with:

```python
    assert not any("idx_chunks_parent_id" in s for s in events)
    first_commit = events.index("COMMIT")
    delete_idx = events.index(parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT)
    assert first_commit < delete_idx
```

### D. Source changes

`src/bin/service_data_manager.py` — add two imports directly after
`from src.data_manager.scheduler import CronScheduler`:

```python
from src.data_manager.vectorstore import parent_nodes
from src.data_manager.vectorstore.schema import ensure_chunks_parent_id_index
```

Add this function between `logger = get_logger(__name__)` and `def main() -> None:`:

```python
def build_parent_id_index_at_startup(factory) -> bool:
    """Build ``idx_chunks_parent_id`` once, in its own committed transaction (#667).

    ``init.sql`` creates the index only on a fresh volume. The data-manager start
    is the single owner of the build: no request or ingest path runs DDL. The
    statement is the plain ``CREATE INDEX IF NOT EXISTS`` (no ``CONCURRENTLY``).

    Return ``True`` when the build committed. Return ``False`` and run no DDL
    when ``document_parent_nodes`` does not exist. Return ``False`` and log a
    WARNING when the connection or the build fails: the service still starts,
    and the parent deletes scan ``document_chunks`` until a later start.
    """
    try:
        with factory.connection_pool.get_connection() as conn:
            with conn.cursor() as cursor:
                if not parent_nodes.parent_table_exists(cursor):
                    conn.rollback()
                    logger.info(
                        "document_parent_nodes does not exist; "
                        "skipped the idx_chunks_parent_id build"
                    )
                    return False
                ensure_chunks_parent_id_index(cursor)
            conn.commit()
    except Exception:
        logger.warning(
            "Could not build idx_chunks_parent_id at data-manager start; "
            "parent deletes scan document_chunks until a later start builds it",
            exc_info=True,
        )
        return False
    logger.info("Ensured idx_chunks_parent_id on document_chunks")
    return True
```

In `main()`, add one line directly after `PostgresServiceFactory.set_instance(factory)`:

```python
    build_parent_id_index_at_startup(factory)
```

`src/data_manager/vectorstore/manager.py`:

- The import `from .schema import ensure_chunks_parent_id_index, ensure_hierarchical_schema`
  becomes `from .schema import ensure_hierarchical_schema`.
- In `_remove_from_postgres`, delete the two lines `if table_exists:` and
  `ensure_chunks_parent_id_index(cursor)` that follow
  `table_exists = parent_nodes.parent_table_exists(cursor)`. Keep the `table_exists` line.
- In `_add_to_postgres`, delete the line `ensure_chunks_parent_id_index(cursor)` that follows
  `ensure_hierarchical_schema(cursor)`. Keep `ensure_hierarchical_schema(cursor)` and
  `conn.commit()`.

`src/data_manager/vectorstore/parent_nodes.py`:

- Delete `from .schema import ensure_chunks_parent_id_index` and the blank line after it.
- In `delete_unreferenced_parents_for_resources`, delete these three lines:

```python
    # An upgraded volume can lack this index; without it each NOT EXISTS below
    # scans all of document_chunks. Callers commit, so the build persists.
    ensure_chunks_parent_id_index(cursor)
```

`src/data_manager/vectorstore/schema.py` (docstrings only):

- In `ensure_hierarchical_schema`, the line
  ``    ``ensure_chunks_parent_id_index``, which only committed write paths call.`` becomes:

```text
    ``ensure_chunks_parent_id_index``, which only the data-manager start calls
    (``build_parent_id_index_at_startup`` in ``src/bin/service_data_manager.py``,
    issue #667).
```

- In `ensure_chunks_parent_id_index`, the two lines
  ``full scan of ``document_chunks``. Call it only from a path that commits;`` and
  ``a rolled-back ``CREATE INDEX`` is rebuilt on the next call.`` become:

```text
    full scan of ``document_chunks``. Only the data-manager start calls it,
    in its own committed transaction; no request or ingest path runs DDL.
```

### E. `docs/docs/troubleshooting.md` — the new paragraph

```markdown
First, make sure that the parent-id index exists. Volumes created before this
index was added do not have it. The data manager builds it when it starts, if
`document_parent_nodes` exists, so a restart of the data-manager container
creates it on an upgraded volume. Look for
`Ensured idx_chunks_parent_id on document_chunks` in the data-manager log. A
WARNING that starts with `Could not build idx_chunks_parent_id` means that the
build failed. Without the index, the queries below scan all of
`document_chunks` once for each parent row. To build it by hand, run the
statement below. It does nothing if the index exists:
```
