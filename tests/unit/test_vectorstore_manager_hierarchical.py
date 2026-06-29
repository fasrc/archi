"""Tests for the hierarchical (structural parent-child) ingestion path in
``VectorStoreManager`` — task 2.3.

When ``data_manager.chunking.strategy`` is ``sentence``/``markdown`` the manager
parses documents into parent context nodes plus embedded child leaves, persists
parents to ``document_parent_nodes``, and writes children to ``document_chunks``
with a ``metadata.parent_id`` link. The legacy ``CharacterTextSplitter`` path is
left intact (covered by ``test_vectorstore_manager_batch_commit``).
"""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Minimal stubs so the manager module imports without the full langchain stack.
if "langchain_core" not in sys.modules:
    sys.modules["langchain_core"] = types.ModuleType("langchain_core")

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
from src.data_manager.vectorstore.manager import (
    VectorStoreManager,
    _resolve_chunk_sizes,
)
from src.data_manager.vectorstore.node_parsing import (
    CHILD_EMBEDDING_DIM,
    DEFAULT_CHILD_CHUNK_SIZE,
    DEFAULT_PARENT_CHUNK_SIZE,
    HierarchicalNode,
)

EMBED_DIM = CHILD_EMBEDDING_DIM


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


class _FakeCursor:
    """Cursor stub that assigns serial ids to parent-node inserts.

    ``RETURNING id`` on ``document_parent_nodes`` inserts is answered via
    ``fetchone`` with an incrementing id, mirroring the SERIAL primary key.
    """

    def __init__(self):
        self.executed = []
        self._parent_seq = 0
        self._next_id = None

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "document_parent_nodes" in sql and "RETURNING id" in sql:
            self._parent_seq += 1
            self._next_id = self._parent_seq

    def fetchone(self):
        return (self._next_id,)

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _make_manager():
    manager = VectorStoreManager.__new__(VectorStoreManager)
    manager.parallel_workers = 1
    manager.collection_name = "test_collection"
    manager.chunking_strategy = "sentence"
    manager.hierarchical_chunking = True
    manager.parent_chunk_size = DEFAULT_PARENT_CHUNK_SIZE
    manager.child_chunk_size = DEFAULT_CHILD_CHUNK_SIZE
    manager._data_manager_config = {"stemming": {"enabled": False}}
    manager._pg_config = {"host": "localhost"}
    manager.embedding_dimensions = EMBED_DIM
    manager.embedding_model = SimpleNamespace(
        embed_documents=lambda texts: [[0.0] * EMBED_DIM for _ in texts]
    )
    return manager


def test_build_hierarchical_payload_links_children_and_enriches_metadata(monkeypatch):
    manager = _make_manager()

    nodes = [
        HierarchicalNode(
            parent_index=0,
            parent_text="Parent A context covering two sentences.",
            child_texts=["First child sentence.", "Second child sentence."],
            metadata={"source": "fasrc"},
        ),
        HierarchicalNode(
            parent_index=1,
            parent_text="Parent B context.",
            child_texts=["Only child of B."],
            metadata={"source": "fasrc"},
        ),
    ]
    monkeypatch.setattr(
        manager_module,
        "build_hierarchical_nodes",
        lambda doc, strategy=None, **_kwargs: nodes,
    )

    parents = manager._build_hierarchical_payload(
        docs=[SimpleNamespace(page_content="ignored", metadata={})],
        file_level_metadata={"resource_hash": "should-be-overwritten"},
        filename="doc.html",
        filehash="hash-1",
        apply_stemming=False,
    )

    assert [p["parent_index"] for p in parents] == [0, 1]
    # Each parent carries >= 1 child (spec: child references exactly one parent).
    assert [len(p["child_texts"]) for p in parents] == [2, 1]

    for p in parents:
        assert len(p["child_metadatas"]) == len(p["child_texts"])
        for md in p["child_metadatas"]:
            assert md["filename"] == "doc.html"
            assert md["resource_hash"] == "hash-1"
            assert md["collection"] == "test_collection"
            # parent_id is stamped at insert time, not in the payload.
            assert "parent_id" not in md
        assert p["parent_metadata"]["parent_index"] == p["parent_index"]


def test_build_hierarchical_payload_drops_parents_without_usable_children(monkeypatch):
    manager = _make_manager()

    nodes = [
        HierarchicalNode(
            parent_index=0,
            parent_text="   ",
            child_texts=["   ", "\x00"],
            metadata={},
        ),
        HierarchicalNode(
            parent_index=1,
            parent_text="Real parent.",
            child_texts=["Real child."],
            metadata={},
        ),
    ]
    monkeypatch.setattr(
        manager_module,
        "build_hierarchical_nodes",
        lambda doc, strategy=None, **_kwargs: nodes,
    )

    parents = manager._build_hierarchical_payload(
        docs=[SimpleNamespace(page_content="ignored", metadata={})],
        file_level_metadata={},
        filename="doc.html",
        filehash="hash-2",
        apply_stemming=False,
    )

    # Empty-child parent dropped; surviving parent re-indexed from 0.
    assert len(parents) == 1
    assert parents[0]["parent_index"] == 0
    assert parents[0]["child_texts"] == ["Real child."]


def test_insert_hierarchical_file_writes_parents_and_links_children(monkeypatch):
    manager = _make_manager()

    captured = {}

    def _capture_execute_values(cursor, sql, data, template=None):
        captured["sql"] = sql
        captured["data"] = data
        captured["template"] = template

    monkeypatch.setattr(
        manager_module.psycopg2.extras, "execute_values", _capture_execute_values
    )

    parents = [
        {
            "parent_index": 0,
            "parent_text": "Parent A.",
            "parent_metadata": {"parent_index": 0},
            "child_texts": ["c0", "c1"],
            "child_metadatas": [{"k": "a"}, {"k": "a"}],
        },
        {
            "parent_index": 1,
            "parent_text": "Parent B.",
            "parent_metadata": {"parent_index": 1},
            "child_texts": ["c2"],
            "child_metadatas": [{"k": "b"}],
        },
    ]

    cursor = _FakeCursor()
    inserted = manager._insert_hierarchical_file(cursor, document_id=7, parents=parents)

    assert inserted == 3

    # Two parent-node inserts, each returning a serial id.
    parent_inserts = [
        sql for sql, _ in cursor.executed if "INSERT INTO document_parent_nodes" in sql
    ]
    assert len(parent_inserts) == 2

    rows = captured["data"]
    assert len(rows) == 3
    # Row shape: (document_id, chunk_index, chunk_text, embedding, metadata_json)
    chunk_indexes = [row[1] for row in rows]
    assert chunk_indexes == [0, 1, 2]  # unique, sequential per document
    assert all(row[0] == 7 for row in rows)
    assert all(len(row[3]) == EMBED_DIM for row in rows)

    import json

    metadatas = [json.loads(row[4]) for row in rows]
    # First two children belong to parent id 1, the third to parent id 2.
    assert metadatas[0]["parent_id"] == 1
    assert metadatas[1]["parent_id"] == 1
    assert metadatas[2]["parent_id"] == 2
    assert [m["chunk_index"] for m in metadatas] == [0, 1, 2]


def test_insert_hierarchical_file_raises_on_embedding_dim_mismatch():
    manager = _make_manager()
    # Embedder returns a wrong-dimension vector; the dim guard must reject it.
    manager.embedding_model = SimpleNamespace(
        embed_documents=lambda texts: [[0.0] * 16 for _ in texts]
    )

    parents = [
        {
            "parent_index": 0,
            "parent_text": "Parent.",
            "parent_metadata": {"parent_index": 0},
            "child_texts": ["c0"],
            "child_metadatas": [{}],
        }
    ]

    with pytest.raises(ValueError):
        manager._insert_hierarchical_file(_FakeCursor(), document_id=1, parents=parents)


def test_insert_hierarchical_file_accepts_configured_non_minilm_dimension(monkeypatch):
    """The dim guard follows the deployment's configured embedding_dimensions.

    A 1536-dim backend (e.g. OpenAIEmbeddings) ingests cleanly when the manager's
    configured ``embedding_dimensions`` matches, rather than being rejected
    against a hardcoded 384.
    """
    manager = _make_manager()
    manager.embedding_dimensions = 1536
    manager.embedding_model = SimpleNamespace(
        embed_documents=lambda texts: [[0.0] * 1536 for _ in texts]
    )

    captured = {}

    def _capture_execute_values(cursor, sql, data, template=None):
        captured["data"] = data

    monkeypatch.setattr(
        manager_module.psycopg2.extras, "execute_values", _capture_execute_values
    )

    parents = [
        {
            "parent_index": 0,
            "parent_text": "Parent.",
            "parent_metadata": {"parent_index": 0},
            "child_texts": ["c0"],
            "child_metadatas": [{}],
        }
    ]

    inserted = manager._insert_hierarchical_file(
        _FakeCursor(), document_id=1, parents=parents
    )

    assert inserted == 1
    assert all(len(row[3]) == 1536 for row in captured["data"])


def test_insert_hierarchical_file_raises_when_dim_differs_from_configured():
    """A vector whose dimension differs from the configured one still fails loudly."""
    manager = _make_manager()
    manager.embedding_dimensions = 1536
    # Embedder yields 384-dim vectors against a 1536-dim configured column.
    manager.embedding_model = SimpleNamespace(
        embed_documents=lambda texts: [[0.0] * 384 for _ in texts]
    )

    parents = [
        {
            "parent_index": 0,
            "parent_text": "Parent.",
            "parent_metadata": {"parent_index": 0},
            "child_texts": ["c0"],
            "child_metadatas": [{}],
        }
    ]

    with pytest.raises(ValueError, match="expected 1536"):
        manager._insert_hierarchical_file(_FakeCursor(), document_id=1, parents=parents)


def test_add_to_postgres_hierarchical_persists_parents_and_children(monkeypatch):
    manager = _make_manager()

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

    captured = {}

    def _capture_execute_values(cursor, sql, data, template=None):
        captured["data"] = data

    fake_cursor = _FakeCursor()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.cursor.return_value.__exit__.return_value = False

    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)
    monkeypatch.setattr(
        manager_module.psycopg2.extras, "execute_values", _capture_execute_values
    )
    monkeypatch.setattr(manager_module, "ThreadPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(manager_module, "as_completed", lambda futures: list(futures))

    manager._add_to_postgres({"hash-1": "/tmp/doc.html"})

    # A parent node was persisted to document_parent_nodes.
    parent_inserts = [
        sql
        for sql, _ in fake_cursor.executed
        if "INSERT INTO document_parent_nodes" in sql
    ]
    assert len(parent_inserts) == 1

    # Children written to document_chunks, each linked to the parent.
    import json

    rows = captured["data"]
    assert len(rows) == 2
    metadatas = [json.loads(row[4]) for row in rows]
    assert all(m["parent_id"] == 1 for m in metadatas)
    assert all(m["resource_hash"] == "hash-1" for m in metadatas)

    # Document marked embedded, not failed.
    status_updates = [
        params
        for sql, params in fake_cursor.executed
        if "ingestion_status = 'embedded'" in sql
    ]
    assert status_updates, "document should be marked embedded"


def test_add_to_postgres_hierarchical_ensures_schema_before_writes(monkeypatch):
    """The hierarchical write path runs the idempotent schema-ensure step before
    inserting parents, so an upgraded deployment on a pre-existing volume does not
    fail with an undefined-table error (task 1.5)."""
    manager = _make_manager()

    catalog = MagicMock()
    catalog.get_document_id.return_value = 42
    catalog.get_metadata_for_hash.return_value = {}
    manager._catalog = catalog

    doc = SimpleNamespace(page_content="some text", metadata={})
    manager.loader = lambda _path: SimpleNamespace(load=lambda: [doc])

    monkeypatch.setattr(
        manager_module,
        "build_hierarchical_nodes",
        lambda document, strategy="sentence", **_kwargs: [
            HierarchicalNode(
                parent_index=0,
                parent_text="Parent context.",
                child_texts=["child one."],
                metadata={},
            )
        ],
    )

    def _capture_execute_values(cursor, sql, data, template=None):
        pass

    fake_cursor = _FakeCursor()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.cursor.return_value.__exit__.return_value = False

    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)
    monkeypatch.setattr(
        manager_module.psycopg2.extras, "execute_values", _capture_execute_values
    )
    monkeypatch.setattr(manager_module, "ThreadPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(manager_module, "as_completed", lambda futures: list(futures))

    manager._add_to_postgres({"hash-1": "/tmp/doc.html"})

    statements = [" ".join(sql.split()) for sql, _ in fake_cursor.executed]
    ensure_idx = next(
        i
        for i, sql in enumerate(statements)
        if "CREATE TABLE IF NOT EXISTS document_parent_nodes" in sql
    )
    insert_idx = next(
        i
        for i, sql in enumerate(statements)
        if "INSERT INTO document_parent_nodes" in sql
    )
    # Ensure step precedes the first parent-node insert.
    assert ensure_idx < insert_idx
    assert any(
        "CREATE INDEX IF NOT EXISTS idx_parent_nodes_document" in sql
        for sql in statements
    )


def test_add_to_postgres_skips_schema_ensure_when_not_hierarchical(monkeypatch):
    """The legacy CharacterTextSplitter path must not touch document_parent_nodes."""
    manager = _make_manager()
    manager.chunking_strategy = "character"
    manager.hierarchical_chunking = False

    catalog = MagicMock()
    catalog.get_document_id.return_value = 42
    catalog.get_metadata_for_hash.return_value = {}
    manager._catalog = catalog

    doc = SimpleNamespace(page_content="some text", metadata={})
    manager.loader = lambda _path: SimpleNamespace(load=lambda: [doc])
    manager.text_splitter = SimpleNamespace(split_documents=lambda docs: docs)

    monkeypatch.setattr(
        manager_module.psycopg2.extras,
        "execute_values",
        lambda *a, **k: None,
    )

    fake_cursor = _FakeCursor()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.cursor.return_value.__exit__.return_value = False

    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)
    monkeypatch.setattr(manager_module, "ThreadPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(manager_module, "as_completed", lambda futures: list(futures))

    manager._add_to_postgres({"hash-1": "/tmp/doc.html"})

    assert not any(
        "document_parent_nodes" in sql for sql, _ in fake_cursor.executed
    ), "non-hierarchical path must not reference the parent-node table"


def test_resolve_chunk_sizes_defaults_when_absent():
    """Omitting the keys reproduces the built-in defaults (backward compatible)."""
    assert _resolve_chunk_sizes({}) == (
        DEFAULT_PARENT_CHUNK_SIZE,
        DEFAULT_CHILD_CHUNK_SIZE,
    )
    # A missing ``chunking`` section (None coerced to {}) behaves identically.
    assert _resolve_chunk_sizes({"strategy": "sentence"}) == (
        DEFAULT_PARENT_CHUNK_SIZE,
        DEFAULT_CHILD_CHUNK_SIZE,
    )


def test_resolve_chunk_sizes_reads_configured_values():
    """Configured parent/child sizes override the defaults."""
    assert _resolve_chunk_sizes(
        {"parent_chunk_size": 1024, "child_chunk_size": 256}
    ) == (1024, 256)
    # Each key falls back independently when only one is provided.
    assert _resolve_chunk_sizes({"parent_chunk_size": 4096}) == (
        4096,
        DEFAULT_CHILD_CHUNK_SIZE,
    )


def test_build_hierarchical_payload_passes_configured_chunk_sizes(monkeypatch):
    """The configured chunk sizes reach ``build_hierarchical_nodes`` at the call
    site, so a benchmark arm's chunk-size config actually drives the parser."""
    manager = _make_manager()
    manager.parent_chunk_size = 1024
    manager.child_chunk_size = 256

    captured = {}

    def _capture(doc, strategy=None, parent_chunk_size=None, child_chunk_size=None):
        captured["strategy"] = strategy
        captured["parent_chunk_size"] = parent_chunk_size
        captured["child_chunk_size"] = child_chunk_size
        return []

    monkeypatch.setattr(manager_module, "build_hierarchical_nodes", _capture)

    manager._build_hierarchical_payload(
        docs=[SimpleNamespace(page_content="x", metadata={})],
        file_level_metadata={},
        filename="doc.html",
        filehash="h",
        apply_stemming=False,
    )

    assert captured["strategy"] == "sentence"
    assert captured["parent_chunk_size"] == 1024
    assert captured["child_chunk_size"] == 256
