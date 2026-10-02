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
from src.data_manager.vectorstore.manager import (
    VectorStoreManager,
    _resolve_chunk_overlap,
    _resolve_chunk_sizes,
    _resolve_chunking_strategy,
)
from src.data_manager.vectorstore.node_parsing import (
    CHILD_CHUNK_OVERLAP,
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
        self.rowcount = 0

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
    manager.child_chunk_overlap = CHILD_CHUNK_OVERLAP
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


def test_resolve_chunking_strategy_defaults_to_sentence_when_absent():
    """An absent ``strategy`` key means the shipped default, not legacy flat chunks.

    The CLI template renders ``sentence`` when the key is unset; a hand-authored
    runtime config that omits it must land on the same strategy (PR #402 round 4).
    """
    assert _resolve_chunking_strategy({}) == "sentence"
    assert _resolve_chunking_strategy({"parent_chunk_size": 1024}) == "sentence"


def test_resolve_chunking_strategy_passes_configured_values_through():
    assert _resolve_chunking_strategy({"strategy": "character"}) == "character"
    assert _resolve_chunking_strategy({"strategy": "markdown"}) == "markdown"


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

    def _capture(
        doc, strategy=None, parent_chunk_size=None, child_chunk_size=None, **_kwargs
    ):
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


def test_build_hierarchical_payload_dispatches_markdown_per_file(monkeypatch):
    """strategy=markdown applies only to Markdown files; others get sentence."""
    manager = _make_manager()
    manager.chunking_strategy = "markdown"
    captured = []

    def _capture(document, strategy=None, **_kwargs):
        captured.append(strategy)
        return [
            HierarchicalNode(
                parent_index=0, parent_text="p", child_texts=["c"], metadata={}
            )
        ]

    monkeypatch.setattr(manager_module, "build_hierarchical_nodes", _capture)

    manager._build_hierarchical_payload(
        docs=[SimpleNamespace(page_content="x", metadata={})],
        file_level_metadata={"suffix": "md"},
        filename="guide.md",
        filehash="h1",
        apply_stemming=False,
    )
    manager._build_hierarchical_payload(
        docs=[SimpleNamespace(page_content="x", metadata={})],
        file_level_metadata={"suffix": "py"},
        filename="script.py",
        filehash="h2",
        apply_stemming=False,
    )

    assert captured == ["markdown", "sentence"]


def test_build_hierarchical_payload_sentence_strategy_ignores_markdown_files(
    monkeypatch,
):
    """strategy=sentence never dispatches, not even for a Markdown file."""
    manager = _make_manager()
    manager.chunking_strategy = "sentence"
    captured = []

    def _capture(document, strategy=None, **_kwargs):
        captured.append(strategy)
        return [
            HierarchicalNode(
                parent_index=0, parent_text="p", child_texts=["c"], metadata={}
            )
        ]

    monkeypatch.setattr(manager_module, "build_hierarchical_nodes", _capture)

    manager._build_hierarchical_payload(
        docs=[SimpleNamespace(page_content="x", metadata={})],
        file_level_metadata={"suffix": "md"},
        filename="guide.md",
        filehash="h1",
        apply_stemming=False,
    )

    assert captured == ["sentence"]


def test_hierarchical_children_and_parents_carry_the_embedding_model(monkeypatch):
    manager = _make_manager()
    manager._data_manager_config = {
        "stemming": {"enabled": False},
        "collection_name": "fasrc",
        "embedding_name": "OpenAIEmbeddings",
        "embedding_class_map": {
            "OpenAIEmbeddings": {"kwargs": {"model": "text-embedding-3-small"}}
        },
    }
    nodes = [
        HierarchicalNode(
            parent_index=0,
            parent_text="Parent context.",
            child_texts=["Child one.", "Child two."],
            metadata={},
        )
    ]
    monkeypatch.setattr(
        manager_module,
        "build_hierarchical_nodes",
        lambda doc, strategy=None, **_kwargs: nodes,
    )

    [parent] = manager._build_hierarchical_payload(
        docs=[SimpleNamespace(page_content="ignored", metadata={})],
        file_level_metadata={},
        filename="doc.html",
        filehash="hash-1",
        apply_stemming=False,
    )

    assert parent["parent_metadata"]["embedding_model"] == "text-embedding-3-small"
    assert all(
        md["embedding_model"] == "text-embedding-3-small"
        for md in parent["child_metadatas"]
    )


# ---------------------------------------------------------------------------
# _resolve_chunk_overlap — task 2.1
# ---------------------------------------------------------------------------


def test_resolve_chunk_overlap_absent_key():
    assert _resolve_chunk_overlap({}) == CHILD_CHUNK_OVERLAP


def test_resolve_chunk_overlap_none_resolves_to_default():
    assert _resolve_chunk_overlap({"chunk_overlap": None}) == CHILD_CHUNK_OVERLAP


def test_resolve_chunk_overlap_zero():
    assert _resolve_chunk_overlap({"chunk_overlap": 0}) == 0


def test_resolve_chunk_overlap_explicit_int():
    assert _resolve_chunk_overlap({"chunk_overlap": 64}) == 64


@pytest.mark.parametrize("bad", [-1, True, "20", 2.5])
def test_resolve_chunk_overlap_rejects_invalid(bad):
    with pytest.raises(ValueError, match="data_manager.chunking.chunk_overlap"):
        _resolve_chunk_overlap({"chunk_overlap": bad})


def test_build_hierarchical_payload_passes_child_chunk_overlap(monkeypatch):
    manager = _make_manager()
    manager.child_chunk_overlap = 64

    captured_kwargs = {}

    def _capture(doc, **kwargs):
        captured_kwargs.update(kwargs)
        return []

    monkeypatch.setattr(manager_module, "build_hierarchical_nodes", _capture)

    manager._build_hierarchical_payload(
        docs=[SimpleNamespace(page_content="x", metadata={})],
        file_level_metadata={},
        filename="doc.txt",
        filehash="h1",
        apply_stemming=False,
    )

    assert captured_kwargs.get("child_chunk_overlap") == 64


def test_add_to_postgres_hierarchical_deletes_unreferenced_parents_for_document(
    monkeypatch,
):
    """DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT runs after chunk insert, before RELEASE SAVEPOINT."""
    from src.data_manager.vectorstore import parent_nodes

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
                parent_text="Parent.",
                child_texts=["child."],
                metadata={},
            )
        ],
    )

    fake_cursor = _FakeCursor()

    def _capture_execute_values(cursor, sql, data, template=None):
        cursor.executed.append(("EXECUTE_VALUES document_chunks", None))

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

    sqls = [sql for sql, _ in fake_cursor.executed]
    all_params = [params for _, params in fake_cursor.executed]

    chunk_marker_idx = sqls.index("EXECUTE_VALUES document_chunks")
    delete_idx = next(
        (
            i
            for i, s in enumerate(sqls)
            if s is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT
        ),
        None,
    )
    release_idx = next(
        (
            i
            for i, s in enumerate(sqls)
            if isinstance(s, str) and "RELEASE SAVEPOINT" in s
        ),
        None,
    )

    assert (
        delete_idx is not None
    ), "DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT not found in executed"
    assert release_idx is not None, "RELEASE SAVEPOINT not found in executed"
    assert chunk_marker_idx < delete_idx < release_idx
    assert all_params[delete_idx] == (42,)


def test_add_to_postgres_hierarchical_deletes_parents_by_resource_when_no_document_id(
    monkeypatch,
):
    """When document_id is None, DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE runs with the file hash."""
    from src.data_manager.vectorstore import parent_nodes

    manager = _make_manager()
    catalog = MagicMock()
    catalog.get_document_id.return_value = None
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
                parent_text="Parent.",
                child_texts=["child."],
                metadata={},
            )
        ],
    )

    fake_cursor = _FakeCursor()

    def _capture_execute_values(cursor, sql, data, template=None):
        cursor.executed.append(("EXECUTE_VALUES document_chunks", None))

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

    resource_deletes = [
        params
        for sql, params in fake_cursor.executed
        if sql is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE
    ]
    assert resource_deletes == [("hash-1", "hash-1")]
    assert not any(
        sql is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT
        for sql, _ in fake_cursor.executed
    )


def test_add_to_postgres_hierarchical_marks_failed_when_parent_delete_raises(
    monkeypatch,
):
    """If the parent delete raises, the file rolls back to its savepoint and is marked failed."""
    from src.data_manager.vectorstore import parent_nodes

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
                parent_text="Parent.",
                child_texts=["child."],
                metadata={},
            )
        ],
    )

    class _RaisingCursor(_FakeCursor):
        def execute(self, sql, params=None):
            if sql is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT:
                raise RuntimeError("simulated delete failure")
            super().execute(sql, params)

    fake_cursor = _RaisingCursor()

    def _capture_execute_values(cursor, sql, data, template=None):
        cursor.executed.append(("EXECUTE_VALUES document_chunks", None))

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

    sqls = [sql for sql, _ in fake_cursor.executed]
    assert any(
        isinstance(s, str) and "ROLLBACK TO SAVEPOINT" in s for s in sqls
    ), "expected rollback after delete failure"
    assert any(
        isinstance(sql, str) and "ingestion_status = 'failed'" in sql
        for sql, _ in fake_cursor.executed
    ), "document should be marked failed"


def test_add_to_postgres_hierarchical_logs_deleted_parent_count(monkeypatch, caplog):
    """An INFO record reports the summed count of deleted parent rows."""
    import logging

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
                parent_text="Parent.",
                child_texts=["child."],
                metadata={},
            )
        ],
    )

    fake_cursor = _FakeCursor()
    fake_cursor.rowcount = 3

    def _capture_execute_values(cursor, sql, data, template=None):
        cursor.executed.append(("EXECUTE_VALUES document_chunks", None))

    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.cursor.return_value.__exit__.return_value = False

    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)
    monkeypatch.setattr(
        manager_module.psycopg2.extras, "execute_values", _capture_execute_values
    )
    monkeypatch.setattr(manager_module, "ThreadPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(manager_module, "as_completed", lambda futures: list(futures))

    with caplog.at_level(logging.INFO):
        manager._add_to_postgres({"hash-1": "/tmp/doc.html"})

    assert any(
        "Deleted" in r.message
        and "unreferenced parent nodes after re-ingest" in r.message
        for r in caplog.records
        if r.levelno == logging.INFO
    ), f"expected deletion log; got: {[r.message for r in caplog.records if r.levelno == logging.INFO]}"


def test_add_to_postgres_non_hierarchical_runs_no_parent_delete(monkeypatch):
    """The flat (non-hierarchical) path must not execute any parent-node delete."""
    from src.data_manager.vectorstore import parent_nodes

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

    fake_cursor = _FakeCursor()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.cursor.return_value.__exit__.return_value = False

    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)
    monkeypatch.setattr(
        manager_module.psycopg2.extras,
        "execute_values",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(manager_module, "ThreadPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(manager_module, "as_completed", lambda futures: list(futures))

    manager._add_to_postgres({"hash-1": "/tmp/doc.html"})

    assert not any(
        sql is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT
        or sql is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE
        for sql, _ in fake_cursor.executed
    ), "non-hierarchical path must not execute any parent delete"


# ── _remove_from_postgres + parent-node cleanup (design D5) ─────────────────


class _RemoveFakeCursor:
    """Fake cursor for _remove_from_postgres tests.

    Answers PARENT_TABLE_EXISTS via fetchone(); tracks all execute() calls.
    """

    def __init__(self, table_exists=True, parent_rowcount=2):
        self.executed = []
        self._table_exists = table_exists
        self.rowcount = parent_rowcount

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return (self._table_exists,)

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _make_remove_conn(cursor):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = False
    return conn


def test_remove_from_postgres_parent_delete_ordering_when_table_present(monkeypatch):
    """PARENT_TABLE_EXISTS runs once; per-hash: chunk delete then parent delete; one commit."""
    from src.data_manager.vectorstore import parent_nodes

    manager = _make_manager()
    fake_cursor = _RemoveFakeCursor(table_exists=True, parent_rowcount=1)
    fake_conn = _make_remove_conn(fake_cursor)
    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)

    manager._remove_from_postgres(["hash-1", "hash-2"])

    sqls = [s for s, _ in fake_cursor.executed]
    params_list = [p for _, p in fake_cursor.executed]

    table_check_indices = [
        i for i, s in enumerate(sqls) if s == parent_nodes.PARENT_TABLE_EXISTS
    ]
    assert len(table_check_indices) == 1, "PARENT_TABLE_EXISTS should run exactly once"

    for resource_hash in ["hash-1", "hash-2"]:
        chunk_delete_idx = next(
            (
                i
                for i, (s, p) in enumerate(zip(sqls, params_list))
                if isinstance(s, str)
                and "DELETE FROM document_chunks" in s
                and p is not None
                and p[0] == resource_hash
            ),
            None,
        )
        parent_delete_idx = next(
            (
                i
                for i, (s, p) in enumerate(zip(sqls, params_list))
                if s is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE
                and p == (resource_hash, resource_hash)
            ),
            None,
        )
        assert (
            chunk_delete_idx is not None
        ), f"chunk delete for {resource_hash} not found"
        assert (
            parent_delete_idx is not None
        ), f"parent delete for {resource_hash} not found"
        assert (
            chunk_delete_idx < parent_delete_idx
        ), f"chunk delete must precede parent delete for {resource_hash}"

    fake_conn.commit.assert_called_once()


def test_remove_from_postgres_parent_delete_runs_when_not_hierarchical(monkeypatch):
    """Parent delete runs even when hierarchical_chunking is False."""
    from src.data_manager.vectorstore import parent_nodes

    manager = _make_manager()
    manager.hierarchical_chunking = False
    fake_cursor = _RemoveFakeCursor(table_exists=True)
    fake_conn = _make_remove_conn(fake_cursor)
    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)

    manager._remove_from_postgres(["hash-1"])

    assert any(
        s is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE
        for s, _ in fake_cursor.executed
    ), "parent delete must run regardless of hierarchical_chunking flag"


def test_remove_from_postgres_no_parent_statements_when_table_absent(monkeypatch):
    """With the table absent, no parent delete or truncate runs; chunk deletes and commit proceed."""
    from src.data_manager.vectorstore import parent_nodes

    manager = _make_manager()
    fake_cursor = _RemoveFakeCursor(table_exists=False)
    fake_conn = _make_remove_conn(fake_cursor)
    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)

    manager._remove_from_postgres(["hash-1"])

    assert not any(
        s is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE
        or s is parent_nodes.TRUNCATE_PARENT_NODES
        for s, _ in fake_cursor.executed
    ), "no parent modification statement should run when table is absent"

    assert any(
        isinstance(s, str) and "DELETE FROM document_chunks" in s
        for s, _ in fake_cursor.executed
    ), "chunk delete must still run when parent table is absent"
    fake_conn.commit.assert_called_once()


def test_remove_from_postgres_logs_deleted_parent_count(monkeypatch, caplog):
    """One INFO record reports the summed deleted count and number of removed resources."""
    import logging

    manager = _make_manager()
    fake_cursor = _RemoveFakeCursor(table_exists=True, parent_rowcount=3)
    fake_conn = _make_remove_conn(fake_cursor)
    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)

    with caplog.at_level(logging.INFO):
        manager._remove_from_postgres(["hash-1", "hash-2"])

    matching = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO
        and "unreferenced parent nodes" in r.message
        and "removed resources" in r.message
    ]
    assert (
        len(matching) == 1
    ), f"expected exactly one INFO line; got: {[r.message for r in caplog.records]}"
    # 3 rowcount × 2 hashes = 6 total; 2 removed resources
    assert "6" in matching[0].message
    assert "2" in matching[0].message


def test_remove_from_postgres_closes_connection_when_parent_delete_raises(monkeypatch):
    """conn.close() is called even if the parent delete raises."""
    from src.data_manager.vectorstore import parent_nodes

    manager = _make_manager()

    class _RaisingCursor(_RemoveFakeCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if sql is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE:
                raise RuntimeError("simulated parent delete failure")

    fake_cursor = _RaisingCursor(table_exists=True)
    fake_conn = _make_remove_conn(fake_cursor)
    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)

    with pytest.raises(RuntimeError, match="simulated parent delete failure"):
        manager._remove_from_postgres(["hash-1"])


# ── delete_existing_collection_if_reset + parent-node truncation (design D5b) ─


def test_delete_existing_collection_if_reset_truncates_parents_when_table_present(
    monkeypatch,
):
    """With table present, TRUNCATE_PARENT_NODES runs right after chunk truncate and before commit."""
    from src.data_manager.vectorstore import parent_nodes

    manager = _make_manager()
    manager._data_manager_config = {
        "stemming": {"enabled": False},
        "reset_collection": True,
    }

    fake_cursor = _RemoveFakeCursor(table_exists=True)
    fake_conn = _make_remove_conn(fake_cursor)
    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)

    manager.delete_existing_collection_if_reset()

    sqls = [s for s, _ in fake_cursor.executed]

    truncate_parents_idx = next(
        (i for i, s in enumerate(sqls) if s is parent_nodes.TRUNCATE_PARENT_NODES),
        None,
    )
    assert (
        truncate_parents_idx is not None
    ), "TRUNCATE_PARENT_NODES must run when the parent table is present"

    chunks_idx = next(
        i
        for i, s in enumerate(sqls)
        if isinstance(s, str) and "TRUNCATE TABLE document_chunks" in s
    )
    assert (
        chunks_idx < truncate_parents_idx
    ), "parent truncate must follow chunk truncate"


def test_delete_existing_collection_if_reset_skips_parent_truncate_when_table_absent(
    monkeypatch,
):
    """With table absent, TRUNCATE_PARENT_NODES must not run."""
    from src.data_manager.vectorstore import parent_nodes

    manager = _make_manager()
    manager._data_manager_config = {
        "stemming": {"enabled": False},
        "reset_collection": True,
    }

    fake_cursor = _RemoveFakeCursor(table_exists=False)
    fake_conn = _make_remove_conn(fake_cursor)
    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)

    manager.delete_existing_collection_if_reset()

    sqls = [s for s, _ in fake_cursor.executed]
    assert not any(
        s is parent_nodes.TRUNCATE_PARENT_NODES for s in sqls
    ), "TRUNCATE_PARENT_NODES must not run when the parent table is absent"

    fake_conn.close.assert_called_once()


def test_remove_from_postgres_ensures_parent_id_index_before_parent_delete(
    monkeypatch,
):
    """A removal-only sync on an upgraded volume must create idx_chunks_parent_id
    before the NOT EXISTS cleanup runs, inside the transaction that commits."""
    from src.data_manager.vectorstore import parent_nodes

    manager = _make_manager()
    fake_cursor = _RemoveFakeCursor(table_exists=True, parent_rowcount=1)
    fake_conn = _make_remove_conn(fake_cursor)
    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)

    manager._remove_from_postgres(["hash-1"])

    sqls = [s for s, _ in fake_cursor.executed]
    index_idx = next(
        i
        for i, s in enumerate(sqls)
        if "CREATE INDEX IF NOT EXISTS idx_chunks_parent_id" in " ".join(s.split())
    )
    delete_idx = sqls.index(parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE)
    assert index_idx < delete_idx
    fake_conn.commit.assert_called_once()


def test_remove_from_postgres_skips_parent_id_index_when_table_absent(monkeypatch):
    """No parent table means no cleanup, so the index is not built either."""
    manager = _make_manager()
    fake_cursor = _RemoveFakeCursor(table_exists=False)
    fake_conn = _make_remove_conn(fake_cursor)
    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)

    manager._remove_from_postgres(["hash-1"])

    assert not any("idx_chunks_parent_id" in s for s, _ in fake_cursor.executed)


def test_add_to_postgres_hierarchical_ensures_parent_id_index_before_commit(
    monkeypatch,
):
    """The add path builds idx_chunks_parent_id in the committed setup step,
    before any parent cleanup runs."""
    from src.data_manager.vectorstore import parent_nodes

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
                parent_text="Parent.",
                child_texts=["child."],
                metadata={},
            )
        ],
    )

    events = []

    class _TrackingCursor(_FakeCursor):
        def execute(self, sql, params=None):
            events.append(sql)
            super().execute(sql, params)

    fake_cursor = _TrackingCursor()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.cursor.return_value.__exit__.return_value = False
    fake_conn.commit.side_effect = lambda: events.append("COMMIT")

    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)
    monkeypatch.setattr(
        manager_module.psycopg2.extras, "execute_values", lambda *a, **k: None
    )
    monkeypatch.setattr(manager_module, "ThreadPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(manager_module, "as_completed", lambda futures: list(futures))

    manager._add_to_postgres({"hash-1": "/tmp/doc.html"})

    index_idx = next(
        i
        for i, s in enumerate(events)
        if "CREATE INDEX IF NOT EXISTS idx_chunks_parent_id" in " ".join(s.split())
    )
    first_commit = events.index("COMMIT")
    delete_idx = events.index(parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT)
    assert index_idx < first_commit < delete_idx


def test_add_to_postgres_hierarchical_count_excludes_rolled_back_deletes(
    monkeypatch, caplog
):
    """A file whose savepoint rolls back after the parent delete must not add
    its deleted count to the INFO summary: the rollback restored those rows."""
    import logging

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
                parent_text="Parent.",
                child_texts=["child."],
                metadata={},
            )
        ],
    )

    class _FailOnEmbeddedUpdate(_FakeCursor):
        def execute(self, sql, params=None):
            if "ingestion_status = 'embedded'" in sql:
                raise RuntimeError("simulated update failure")
            super().execute(sql, params)

    fake_cursor = _FailOnEmbeddedUpdate()
    fake_cursor.rowcount = 3
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.cursor.return_value.__exit__.return_value = False

    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)
    monkeypatch.setattr(
        manager_module.psycopg2.extras, "execute_values", lambda *a, **k: None
    )
    monkeypatch.setattr(manager_module, "ThreadPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(manager_module, "as_completed", lambda futures: list(futures))

    with caplog.at_level(logging.INFO):
        manager._add_to_postgres({"hash-1": "/tmp/doc.html"})

    summary = [
        r.message
        for r in caplog.records
        if "unreferenced parent nodes after re-ingest" in r.message
    ]
    assert summary == ["Deleted 0 unreferenced parent nodes after re-ingest"]
