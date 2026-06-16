"""Real-code-path tests for ``VectorStoreManager`` sync/catalog helpers.

These exercise the manager methods that surround the ingestion path touched by
the title-aware-retrieval change, using mocked Postgres connections and catalog
services so the genuine code paths run without a live database.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.data_manager.vectorstore import manager as manager_module
from src.data_manager.vectorstore.manager import VectorStoreManager


def _bare_manager():
    manager = VectorStoreManager.__new__(VectorStoreManager)
    manager.collection_name = "test_collection"
    manager._pg_config = {"host": "localhost"}
    return manager


def _fake_conn_cursor():
    fake_cursor = MagicMock()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.cursor.return_value.__exit__.return_value = False
    return fake_conn, fake_cursor


def test_collect_indexed_documents_filters_missing_and_dirs(tmp_path):
    manager = _bare_manager()

    real_file = tmp_path / "doc.txt"
    real_file.write_text("hello")
    a_dir = tmp_path / "subdir"
    a_dir.mkdir()
    missing = tmp_path / "gone.txt"

    sources = {
        "h-file": str(real_file),
        "h-dir": str(a_dir),
        "h-missing": str(missing),
    }

    result = manager._collect_indexed_documents(sources)

    assert result == {"h-file": str(real_file)}


def test_collect_indexed_documents_keeps_first_duplicate(tmp_path):
    manager = _bare_manager()
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    file_b = tmp_path / "b.txt"
    file_b.write_text("b")

    # Same hash pointing at two different paths -> first wins.
    sources = {"dup": str(file_a)}
    result = manager._collect_indexed_documents(sources)
    assert result == {"dup": str(file_a)}


def test_load_file_metadata_sanitizes_values():
    manager = _bare_manager()
    catalog = MagicMock()
    catalog.get_metadata_for_hash.return_value = {
        "display_name": "Title",
        "skip_none": None,
        5: "coerced_key",
    }
    manager._catalog = catalog

    result = manager._load_file_metadata("hash-1")

    assert result == {"display_name": "Title", "5": "coerced_key"}


def test_load_file_metadata_handles_no_metadata():
    manager = _bare_manager()
    catalog = MagicMock()
    catalog.get_metadata_for_hash.return_value = None
    manager._catalog = catalog

    assert manager._load_file_metadata("hash-1") == {}


def test_collect_postgres_hashes(monkeypatch):
    manager = _bare_manager()
    fake_conn, fake_cursor = _fake_conn_cursor()
    fake_cursor.fetchall.return_value = [("hash-a",), ("hash-b",)]
    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)

    result = manager._collect_postgres_hashes()

    assert result == {"hash-a", "hash-b"}
    fake_conn.close.assert_called_once()


def test_remove_from_postgres(monkeypatch):
    manager = _bare_manager()
    fake_conn, fake_cursor = _fake_conn_cursor()
    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)

    manager._remove_from_postgres(["hash-a", "hash-b"])

    assert fake_cursor.execute.call_count == 2
    fake_conn.commit.assert_called_once()
    fake_conn.close.assert_called_once()


def test_delete_existing_collection_noop_when_disabled():
    manager = _bare_manager()
    manager._data_manager_config = {"reset_collection": False}
    # Should return without ever opening a connection.
    manager.delete_existing_collection_if_reset()


def test_delete_existing_collection_truncates_when_enabled(monkeypatch):
    manager = _bare_manager()
    manager._data_manager_config = {"reset_collection": True}
    fake_conn, fake_cursor = _fake_conn_cursor()
    fake_cursor.rowcount = 3
    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)

    manager.delete_existing_collection_if_reset()

    executed = " ".join(
        str(call.args[0]) for call in fake_cursor.execute.call_args_list
    )
    assert "TRUNCATE TABLE document_chunks" in executed
    assert "VACUUM FULL document_chunks" in executed
    fake_conn.commit.assert_called()
    fake_conn.close.assert_called_once()


def test_update_vectorstore_adds_and_removes(monkeypatch):
    manager = _bare_manager()
    manager.data_path = "/tmp/data"

    store = SimpleNamespace(count=lambda: 1)
    manager.fetch_collection = lambda: store
    monkeypatch.setattr(
        manager_module.PostgresCatalogService,
        "load_sources_catalog",
        staticmethod(lambda _data_path, _pg_config: {"new": "/tmp/new.txt"}),
    )
    # One stale hash present in the store, one fresh document on disk.
    manager._collect_postgres_hashes = lambda: {"stale"}
    manager._collect_indexed_documents = lambda _sources: {"new": "/tmp/new.txt"}
    manager._remove_from_postgres = MagicMock()
    manager._add_to_postgres = MagicMock()

    manager.update_vectorstore()

    manager._remove_from_postgres.assert_called_once_with(["stale"])
    manager._add_to_postgres.assert_called_once()
    added = manager._add_to_postgres.call_args.args[0]
    assert added == {"new": "/tmp/new.txt"}


def test_update_vectorstore_noop_when_in_sync(monkeypatch):
    manager = _bare_manager()
    manager.data_path = "/tmp/data"
    manager.fetch_collection = lambda: SimpleNamespace(count=lambda: 1)
    monkeypatch.setattr(
        manager_module.PostgresCatalogService,
        "load_sources_catalog",
        staticmethod(lambda _data_path, _pg_config: {"same": "/tmp/same.txt"}),
    )
    manager._collect_postgres_hashes = lambda: {"same"}
    manager._collect_indexed_documents = lambda _sources: {"same": "/tmp/same.txt"}
    manager._remove_from_postgres = MagicMock()
    manager._add_to_postgres = MagicMock()

    manager.update_vectorstore()

    manager._remove_from_postgres.assert_not_called()
    manager._add_to_postgres.assert_not_called()


def test_loader_returns_none_for_unsupported(monkeypatch):
    manager = _bare_manager()
    monkeypatch.setattr(manager_module, "select_loader", lambda _path: None)
    assert manager.loader("/tmp/file.xyz") is None


def test_loader_returns_selected_loader(monkeypatch):
    manager = _bare_manager()
    sentinel = SimpleNamespace(load=lambda: [])
    monkeypatch.setattr(manager_module, "select_loader", lambda _path: sentinel)
    assert manager.loader("/tmp/file.txt") is sentinel


def test_fetch_collection_builds_store(monkeypatch):
    manager = _bare_manager()
    manager.distance_metric = "ip"
    manager.embedding_model = SimpleNamespace()

    captured = {}

    class _FakeStore:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def count(self):
            return 7

    monkeypatch.setattr(manager_module, "PostgresVectorStore", _FakeStore)

    store = manager.fetch_collection()

    assert store.count() == 7
    # "ip" distance maps to inner_product.
    assert captured["distance_metric"] == "inner_product"
    assert captured["collection_name"] == "test_collection"
