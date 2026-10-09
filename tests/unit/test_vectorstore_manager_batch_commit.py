import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Minimal stubs so tests can run without langchain-core installed.
if "langchain_core" not in sys.modules:
    langchain_core = types.ModuleType("langchain_core")
    sys.modules["langchain_core"] = langchain_core

if "langchain_core.documents" not in sys.modules:
    documents_module = types.ModuleType("langchain_core.documents")
    documents_module.Document = object
    sys.modules["langchain_core.documents"] = documents_module

if "langchain_core.embeddings" not in sys.modules:
    embeddings_module = types.ModuleType("langchain_core.embeddings")
    embeddings_module.Embeddings = object
    sys.modules["langchain_core.embeddings"] = embeddings_module

if "langchain_core.vectorstores" not in sys.modules:
    vectorstores_module = types.ModuleType("langchain_core.vectorstores")
    vectorstores_module.VectorStore = object
    sys.modules["langchain_core.vectorstores"] = vectorstores_module

if "nltk" not in sys.modules:
    nltk_module = types.ModuleType("nltk")
    nltk_module.tokenize = types.SimpleNamespace(
        word_tokenize=lambda text: text.split()
    )
    nltk_module.stem = types.SimpleNamespace(
        PorterStemmer=lambda: types.SimpleNamespace(stem=lambda w: w)
    )
    nltk_module.download = lambda *_args, **_kwargs: None
    sys.modules["nltk"] = nltk_module

if "langchain_text_splitters" not in sys.modules:
    sys.modules["langchain_text_splitters"] = types.ModuleType(
        "langchain_text_splitters"
    )

if "langchain_text_splitters.character" not in sys.modules:
    character_module = types.ModuleType("langchain_text_splitters.character")

    class _DummyCharacterTextSplitter:
        def __init__(self, *args, **kwargs):
            pass

        def split_documents(self, docs):
            return docs

    character_module.CharacterTextSplitter = _DummyCharacterTextSplitter
    sys.modules["langchain_text_splitters.character"] = character_module

if "langchain_community" not in sys.modules:
    sys.modules["langchain_community"] = types.ModuleType("langchain_community")

if "langchain_community.document_loaders" not in sys.modules:
    loaders_module = types.ModuleType("langchain_community.document_loaders")

    class _DummyLoader:
        def __init__(self, *_args, **_kwargs):
            pass

        def load(self):
            return []

    loaders_module.BSHTMLLoader = _DummyLoader
    loaders_module.NotebookLoader = _DummyLoader
    loaders_module.PyPDFLoader = _DummyLoader
    loaders_module.PythonLoader = _DummyLoader
    loaders_module.TextLoader = _DummyLoader
    sys.modules["langchain_community.document_loaders"] = loaders_module

if "langchain_community.document_loaders.text" not in sys.modules:
    text_module = types.ModuleType("langchain_community.document_loaders.text")
    text_module.TextLoader = sys.modules[
        "langchain_community.document_loaders"
    ].TextLoader
    sys.modules["langchain_community.document_loaders.text"] = text_module

from src.data_manager.vectorstore import manager as manager_module
from src.data_manager.vectorstore.manager import VectorStoreManager
from src.data_manager.vectorstore.node_parsing import (
    CHILD_EMBEDDING_DIM,
    HierarchicalNode,
)


class _InlineFuture:
    def __init__(self, fn, *args, **kwargs):
        self._exc = None
        self._result = None
        try:
            self._result = fn(*args, **kwargs)
        except Exception as exc:  # pragma: no cover - defensive
            self._exc = exc

    def result(self):
        if self._exc:
            raise self._exc
        return self._result


class _InlineExecutor:
    def __init__(self, max_workers=1):
        self.max_workers = max_workers
        self.futures = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, fn, *args, **kwargs):
        fut = _InlineFuture(fn, *args, **kwargs)
        self.futures.append(fut)
        return fut


def test_add_to_postgres_commits_every_25_files(monkeypatch):
    manager = VectorStoreManager.__new__(VectorStoreManager)
    manager.parallel_workers = 1
    manager.collection_name = "test_collection"
    manager.hierarchical_chunking = False
    manager._data_manager_config = {"stemming": {"enabled": False}}
    manager._pg_config = {"host": "localhost"}

    catalog = MagicMock()
    catalog.get_document_id.return_value = 1
    catalog.get_metadata_for_hash.return_value = {}
    manager._catalog = catalog

    split_doc = SimpleNamespace(page_content="hello world", metadata={})
    manager.text_splitter = SimpleNamespace(split_documents=lambda docs: [split_doc])
    manager.embedding_model = SimpleNamespace(
        embed_documents=lambda chunks: [[0.1, 0.2, 0.3] for _ in chunks]
    )
    manager.loader = lambda _path: SimpleNamespace(load=lambda: [split_doc])

    fake_cursor = MagicMock()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.cursor.return_value.__exit__.return_value = False

    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)
    monkeypatch.setattr(
        manager_module.psycopg2.extras, "execute_values", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(manager_module, "ThreadPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(manager_module, "as_completed", lambda futures: list(futures))

    files_to_add = {f"hash-{i}": f"/tmp/file-{i}.txt" for i in range(26)}
    manager._add_to_postgres(files_to_add)

    # First commit at 25 files, second for final remainder.
    assert fake_conn.commit.call_count == 2
    # All documents are marked embedding at start of run.
    assert catalog.update_ingestion_status.call_count >= 26


class _FakeCursorH:
    """Cursor stub that returns serial ids for parent-node RETURNING id queries."""

    def __init__(self):
        self._parent_seq = 0
        self.rowcount = 0

    def execute(self, sql, params=None):
        if "document_parent_nodes" in sql and "RETURNING id" in sql:
            self._parent_seq += 1

    def fetchone(self):
        return (self._parent_seq,)

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _setup_flat_manager(monkeypatch, embed_fn=None, loader_fn=None):
    manager = VectorStoreManager.__new__(VectorStoreManager)
    manager.parallel_workers = 1
    manager.collection_name = "test_collection"
    manager.hierarchical_chunking = False
    manager._data_manager_config = {"stemming": {"enabled": False}}
    manager._pg_config = {"host": "localhost"}

    catalog = MagicMock()
    catalog.get_document_id.return_value = 1
    catalog.get_metadata_for_hash.return_value = {}
    manager._catalog = catalog

    split_doc = SimpleNamespace(page_content="hello world", metadata={})
    manager.text_splitter = SimpleNamespace(split_documents=lambda docs: [split_doc])
    if embed_fn is None:
        embed_fn = lambda chunks: [[0.1, 0.2, 0.3] for _ in chunks]
    manager.embedding_model = SimpleNamespace(embed_documents=embed_fn)
    if loader_fn is None:
        loader_fn = lambda _path: SimpleNamespace(load=lambda: [split_doc])
    manager.loader = loader_fn

    fake_cursor = MagicMock()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.cursor.return_value.__exit__.return_value = False

    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)
    monkeypatch.setattr(
        manager_module.psycopg2.extras, "execute_values", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(manager_module, "ThreadPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(manager_module, "as_completed", lambda futures: list(futures))

    return manager, fake_conn, catalog


def test_embedding_progress_callback_reports_at_each_batch(monkeypatch):
    manager, _, _ = _setup_flat_manager(monkeypatch)
    calls = []

    def cb(done, total):
        calls.append((done, total))

    files_to_add = {f"hash-{i}": f"/tmp/file-{i}.txt" for i in range(26)}
    manager._add_to_postgres(files_to_add, embedding_progress=cb)

    assert calls == [(0, 26), (25, 26), (26, 26)]


def test_embedding_progress_counts_embed_failure_files(monkeypatch):
    call_count = [0]

    def embed_fn(chunks):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("embed fail")
        return [[0.1, 0.2, 0.3] for _ in chunks]

    manager, _, _ = _setup_flat_manager(monkeypatch, embed_fn=embed_fn)
    calls = []

    def cb(done, total):
        calls.append((done, total))

    files_to_add = {f"hash-{i}": f"/tmp/file-{i}.txt" for i in range(26)}
    manager._add_to_postgres(files_to_add, embedding_progress=cb)

    assert calls[-1] == (26, 26)


def test_embedding_progress_callback_hierarchical(monkeypatch):
    manager = VectorStoreManager.__new__(VectorStoreManager)
    manager.parallel_workers = 1
    manager.collection_name = "test_collection"
    manager.chunking_strategy = "sentence"
    manager.hierarchical_chunking = True
    manager.parent_chunk_size = 2048
    manager.child_chunk_size = 512
    manager.child_chunk_overlap = 20
    manager._data_manager_config = {"stemming": {"enabled": False}}
    manager._pg_config = {"host": "localhost"}
    manager.embedding_dimensions = CHILD_EMBEDDING_DIM
    manager.embedding_model = SimpleNamespace(
        embed_documents=lambda texts: [[0.0] * CHILD_EMBEDDING_DIM for _ in texts]
    )

    catalog = MagicMock()
    catalog.get_document_id.return_value = 42
    catalog.get_metadata_for_hash.return_value = {}
    manager._catalog = catalog

    doc = SimpleNamespace(page_content="some text", metadata={})
    manager.loader = lambda _path: SimpleNamespace(load=lambda: [doc])

    def _fake_nodes(document, strategy="sentence", **_kwargs):
        return [
            HierarchicalNode(
                parent_index=0,
                parent_text="Parent context.",
                child_texts=["child one.", "child two."],
                metadata={},
            )
        ]

    monkeypatch.setattr(manager_module, "build_hierarchical_nodes", _fake_nodes)

    fake_cursor = _FakeCursorH()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.cursor.return_value.__exit__.return_value = False

    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)
    monkeypatch.setattr(
        manager_module.psycopg2.extras, "execute_values", lambda *a, **k: None
    )
    monkeypatch.setattr(manager_module, "ThreadPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(manager_module, "as_completed", lambda futures: list(futures))

    calls = []

    def cb(done, total):
        calls.append((done, total))

    manager._add_to_postgres({"hash-1": "/tmp/doc.html"}, embedding_progress=cb)

    assert calls == [(0, 1), (1, 1)]


def test_embedding_progress_skipped_files_not_counted(monkeypatch):
    split_doc = SimpleNamespace(page_content="hello world", metadata={})

    def loader_fn(path):
        if "file-1" in path:
            return None
        return SimpleNamespace(load=lambda: [split_doc])

    manager, _, _ = _setup_flat_manager(monkeypatch, loader_fn=loader_fn)

    calls = []

    def cb(done, total):
        calls.append((done, total))

    files_to_add = {f"hash-{i}": f"/tmp/file-{i}.txt" for i in range(3)}
    manager._add_to_postgres(files_to_add, embedding_progress=cb)

    assert calls[-1] == (2, 3)


def test_embedding_progress_callback_exception_does_not_abort(monkeypatch, caplog):
    import logging

    manager, fake_conn, _ = _setup_flat_manager(monkeypatch)

    def raising_cb(done, total):
        raise RuntimeError("cb fail")

    files_to_add = {f"hash-{i}": f"/tmp/file-{i}.txt" for i in range(26)}
    with caplog.at_level(
        logging.WARNING, logger="src.data_manager.vectorstore.manager"
    ):
        manager._add_to_postgres(files_to_add, embedding_progress=raising_cb)

    assert fake_conn.commit.call_count == 2
    assert any(
        "Embedding progress callback failed" in r.getMessage() for r in caplog.records
    )


def _tagging_config(embedding_name="HuggingFaceEmbeddings", kwargs=None):
    return {
        "stemming": {"enabled": False},
        "collection_name": "fasrc",
        "embedding_name": embedding_name,
        "embedding_class_map": {
            embedding_name: {
                "class": embedding_name,
                "kwargs": {"model_name": "Qwen/Q"} if kwargs is None else kwargs,
            }
        },
    }


def _flat_chunk_metadata(monkeypatch, data_manager_config):
    import json

    manager = VectorStoreManager.__new__(VectorStoreManager)
    manager.parallel_workers = 1
    manager.collection_name = "fasrc_with_HuggingFaceEmbeddings"
    manager.hierarchical_chunking = False
    manager._data_manager_config = data_manager_config
    manager._pg_config = {"host": "localhost"}
    catalog = MagicMock()
    catalog.get_document_id.return_value = 1
    catalog.get_metadata_for_hash.return_value = {}
    manager._catalog = catalog
    split_doc = SimpleNamespace(page_content="hello world", metadata={})
    manager.text_splitter = SimpleNamespace(split_documents=lambda docs: [split_doc])
    manager.embedding_model = SimpleNamespace(
        embed_documents=lambda chunks: [[0.1, 0.2, 0.3] for _ in chunks]
    )
    manager.loader = lambda _path: SimpleNamespace(load=lambda: [split_doc])
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = MagicMock()
    fake_conn.cursor.return_value.__exit__.return_value = False
    written = []
    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)
    monkeypatch.setattr(
        manager_module.psycopg2.extras,
        "execute_values",
        lambda cursor, sql, rows, *a, **k: written.extend(rows),
    )
    monkeypatch.setattr(manager_module, "ThreadPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(manager_module, "as_completed", lambda futures: list(futures))

    manager._add_to_postgres({"hash-0": "/tmp/file-0.txt"})

    metadatas = []
    for row in written:
        for value in row:
            if isinstance(value, str) and value.startswith("{"):
                metadatas.append(json.loads(value))
    assert metadatas, written
    return metadatas


def test_flat_chunks_carry_the_embedding_model(monkeypatch):
    for metadata in _flat_chunk_metadata(monkeypatch, _tagging_config()):
        assert metadata["embedding_model"] == "Qwen/Q"
        assert metadata["collection"] == "fasrc_with_HuggingFaceEmbeddings"


def test_a_class_without_a_model_kwarg_tags_the_class_name(monkeypatch):
    config = _tagging_config("FakeEmbeddings", kwargs={})
    for metadata in _flat_chunk_metadata(monkeypatch, config):
        assert metadata["embedding_model"] == "FakeEmbeddings"


def test_fetch_collection_passes_the_configured_model(monkeypatch):
    built = {}

    class FakeStore:
        def __init__(self, **kwargs):
            built.update(kwargs)

        def count(self):
            return 0

    monkeypatch.setattr(manager_module, "PostgresVectorStore", FakeStore)
    manager = VectorStoreManager.__new__(VectorStoreManager)
    manager._data_manager_config = _tagging_config()
    manager._pg_config = {"host": "localhost"}
    manager.embedding_model = object()
    manager.collection_name = "fasrc_with_HuggingFaceEmbeddings"
    manager.distance_metric = "cosine"

    manager.fetch_collection()

    assert built["embedding_model"] == "Qwen/Q"
