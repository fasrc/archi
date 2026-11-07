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
