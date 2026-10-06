"""Tests for the parent-node cleanup helper module (issue #411).

Uses a stateful fake cursor that applies the D1 predicate in Python so
tests can drive the helpers without a real Postgres connection.
"""

import json as _json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.data_manager.vectorstore import parent_nodes


class _FakeCursor:
    """Stateful cursor that simulates document_parent_nodes and document_chunks.

    parents: dict mapping id -> {"document_id": int|None, "metadata": dict}
    chunks:  list of dicts with "collection" and "parent_id" keys
    documents: list of dicts with "id" and "resource_hash" keys
    table_exists: bool returned by PARENT_TABLE_EXISTS query
    rowcount: set by execute() to reflect the number of rows deleted; -1 signals unknown
    """

    def __init__(self, parents=None, chunks=None, documents=None, table_exists=True):
        self.parents = dict(parents or {})
        self.chunks = list(chunks or [])
        self.documents = list(documents or [])
        self.table_exists = table_exists
        self.rowcount = 0
        self._sqls = []
        self._last_fetchone = None

    def execute(self, sql, params=None):
        self._sqls.append((sql, params))

        if sql is parent_nodes.PARENT_TABLE_EXISTS:
            self._last_fetchone = (self.table_exists,)
            self.rowcount = -1
            return

        if sql is parent_nodes.TRUNCATE_PARENT_NODES:
            self.rowcount = 0
            return

        if sql is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT:
            (document_id,) = params
            deleted = self._delete_unreferenced_by_document_id(document_id)
            self.rowcount = deleted
            return

        if sql is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE:
            (resource_hash1, resource_hash2) = params
            assert resource_hash1 == resource_hash2, "both params must match"
            deleted = self._delete_unreferenced_by_resource_hash(resource_hash1)
            self.rowcount = deleted
            return

    def _referenced_parent_ids(self):
        return {c["parent_id"] for c in self.chunks}

    def _delete_unreferenced_by_document_id(self, document_id):
        referenced = self._referenced_parent_ids()
        to_delete = [
            pid
            for pid, p in list(self.parents.items())
            if p["document_id"] == document_id and pid not in referenced
        ]
        for pid in to_delete:
            del self.parents[pid]
        return len(to_delete)

    def _delete_unreferenced_by_resource_hash(self, resource_hash):
        doc_ids = {
            d["id"] for d in self.documents if d["resource_hash"] == resource_hash
        }
        referenced = self._referenced_parent_ids()
        to_delete = [
            pid
            for pid, p in list(self.parents.items())
            if (
                (p["document_id"] in doc_ids)
                or (
                    p["document_id"] is None
                    and p.get("metadata", {}).get("resource_hash") == resource_hash
                )
            )
            and pid not in referenced
        ]
        for pid in to_delete:
            del self.parents[pid]
        return len(to_delete)

    def fetchone(self):
        return self._last_fetchone

    def executed_sqls(self):
        return [sql for sql, _ in self._sqls]


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _parents_doc7():
    """Three parents belonging to document 7; one will be kept by a chunk ref."""
    return {
        1: {"document_id": 7, "metadata": {}},
        2: {"document_id": 7, "metadata": {}},
        3: {"document_id": 7, "metadata": {}},
    }


def _parents_doc7_and_8():
    p = _parents_doc7()
    p[4] = {"document_id": 8, "metadata": {}}
    p[5] = {"document_id": 8, "metadata": {}}
    return p


# ---------------------------------------------------------------------------
# (a) delete_unreferenced_parents — document path
# ---------------------------------------------------------------------------


def test_delete_unreferenced_parents_removes_orphans_of_document():
    """Deletes doc-7 parents not referenced by any chunk in any collection."""
    parents = _parents_doc7_and_8()
    # Parent 1 is referenced by a chunk in "col_a".
    chunks = [{"collection": "col_a", "parent_id": 1}]

    cursor = _FakeCursor(parents=parents, chunks=chunks)
    deleted = parent_nodes.delete_unreferenced_parents(cursor, 7)

    assert deleted == 2  # parents 2 and 3 gone
    assert 1 in cursor.parents  # referenced parent 1 survives
    assert 4 in cursor.parents  # doc-8 parent untouched
    assert 5 in cursor.parents


def test_delete_unreferenced_parents_keeps_parent_referenced_by_other_collection():
    """A doc-7 parent referenced by a chunk in ANOTHER collection is kept."""
    parents = {
        10: {"document_id": 7, "metadata": {}},
        11: {"document_id": 7, "metadata": {}},
    }
    chunks = [{"collection": "other_collection", "parent_id": 10}]

    cursor = _FakeCursor(parents=parents, chunks=chunks)
    deleted = parent_nodes.delete_unreferenced_parents(cursor, 7)

    assert deleted == 1  # only parent 11 gone
    assert 10 in cursor.parents  # still referenced
    assert 11 not in cursor.parents


def test_delete_unreferenced_parents_keeps_all_parents_of_other_document():
    """Parents belonging to document 8 are never touched when deleting for doc 7."""
    parents = {
        1: {"document_id": 7, "metadata": {}},
        2: {"document_id": 8, "metadata": {}},
    }
    cursor = _FakeCursor(parents=parents, chunks=[])
    deleted = parent_nodes.delete_unreferenced_parents(cursor, 7)

    assert deleted == 1
    assert 2 in cursor.parents


def test_delete_unreferenced_parents_returns_deleted_count():
    parents = {i: {"document_id": 7, "metadata": {}} for i in range(1, 6)}
    cursor = _FakeCursor(parents=parents, chunks=[])
    deleted = parent_nodes.delete_unreferenced_parents(cursor, 7)
    assert deleted == 5


# ---------------------------------------------------------------------------
# (b) delete_unreferenced_parents(cursor, None)
# ---------------------------------------------------------------------------


def test_delete_unreferenced_parents_none_returns_zero_and_no_sql():
    """None document_id → return 0, run no SQL."""
    cursor = _FakeCursor(parents={1: {"document_id": None, "metadata": {}}})
    deleted = parent_nodes.delete_unreferenced_parents(cursor, None)

    assert deleted == 0
    assert cursor._sqls == []


# ---------------------------------------------------------------------------
# (c) delete_unreferenced_parents_for_resource
# ---------------------------------------------------------------------------


def test_delete_unreferenced_parents_for_resource_deletes_orphans_for_hash():
    """Deletes unreferenced parents for the document whose resource_hash matches."""
    documents = [
        {"id": 7, "resource_hash": "hash-7"},
        {"id": 8, "resource_hash": "hash-8"},
    ]
    parents = {
        1: {"document_id": 7, "metadata": {}},
        2: {"document_id": 7, "metadata": {}},
        3: {"document_id": 8, "metadata": {}},
    }
    # Parent 1 is referenced.
    chunks = [{"collection": "col_a", "parent_id": 1}]

    cursor = _FakeCursor(parents=parents, chunks=chunks, documents=documents)
    deleted = parent_nodes.delete_unreferenced_parents_for_resource(cursor, "hash-7")

    assert deleted == 1  # parent 2 gone; parent 1 referenced, parent 3 other doc
    assert 1 in cursor.parents
    assert 2 not in cursor.parents
    assert 3 in cursor.parents


def test_delete_unreferenced_parents_for_resource_keeps_other_collection_ref():
    """A parent for hash-7 referenced by another collection's chunk survives."""
    documents = [{"id": 7, "resource_hash": "hash-7"}]
    parents = {
        10: {"document_id": 7, "metadata": {}},
        11: {"document_id": 7, "metadata": {}},
    }
    chunks = [{"collection": "other_col", "parent_id": 10}]

    cursor = _FakeCursor(parents=parents, chunks=chunks, documents=documents)
    deleted = parent_nodes.delete_unreferenced_parents_for_resource(cursor, "hash-7")

    assert deleted == 1
    assert 10 in cursor.parents
    assert 11 not in cursor.parents


def test_delete_unreferenced_parents_for_resource_deletes_null_doc_id_by_metadata():
    """Deletes an unreferenced parent with NULL document_id whose metadata resource_hash matches."""
    documents = [{"id": 7, "resource_hash": "hash-7"}]
    parents = {
        20: {"document_id": None, "metadata": {"resource_hash": "hash-7"}},
        21: {"document_id": None, "metadata": {"resource_hash": "hash-other"}},
    }

    cursor = _FakeCursor(parents=parents, chunks=[], documents=documents)
    deleted = parent_nodes.delete_unreferenced_parents_for_resource(cursor, "hash-7")

    assert deleted == 1
    assert 20 not in cursor.parents
    assert 21 in cursor.parents  # different hash, kept


def test_delete_unreferenced_parents_for_resource_passes_hash_twice():
    """The helper must pass (resource_hash, resource_hash) to satisfy both predicate arms."""
    documents = [{"id": 7, "resource_hash": "hash-7"}]
    cursor = _FakeCursor(parents={}, chunks=[], documents=documents)
    parent_nodes.delete_unreferenced_parents_for_resource(cursor, "hash-7")

    assert len(cursor._sqls) == 1
    sql, params = cursor._sqls[0]
    assert sql is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE
    assert params == ("hash-7", "hash-7")


def test_delete_unreferenced_parents_for_resource_returns_zero_for_unknown_hash():
    documents = [{"id": 7, "resource_hash": "hash-7"}]
    cursor = _FakeCursor(parents={}, chunks=[], documents=documents)
    deleted = parent_nodes.delete_unreferenced_parents_for_resource(
        cursor, "no-such-hash"
    )
    assert deleted == 0


# ---------------------------------------------------------------------------
# (d) rowcount of -1 returns 0
# ---------------------------------------------------------------------------


def test_negative_rowcount_returns_zero():
    """psycopg2 may return -1 for rowcount when the count is unknown; clamp to 0."""
    parents = {1: {"document_id": 7, "metadata": {}}}

    class _NegRowCursor(_FakeCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if sql is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT:
                self.rowcount = -1

    cursor = _NegRowCursor(parents=parents)
    deleted = parent_nodes.delete_unreferenced_parents(cursor, 7)
    assert deleted == 0


# ---------------------------------------------------------------------------
# (e) SQL constants contain NOT EXISTS, correct predicate, no collection text
# ---------------------------------------------------------------------------


def test_for_document_constant_contains_not_exists():
    assert "NOT EXISTS" in parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT


def test_for_document_constant_contains_parent_id_predicate():
    assert (
        "metadata->>'parent_id' = p.id::text"
        in parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT
    )


def test_for_document_constant_has_no_collection_filter():
    assert (
        "collection"
        not in parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT.lower()
    )


def test_for_resource_constant_contains_not_exists():
    assert "NOT EXISTS" in parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE


def test_for_resource_constant_contains_parent_id_predicate():
    assert (
        "metadata->>'parent_id' = p.id::text"
        in parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE
    )


def test_for_resource_constant_has_no_collection_filter():
    assert (
        "collection"
        not in parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE.lower()
    )


# ---------------------------------------------------------------------------
# (f) parent_table_exists returns the fake's flag
# ---------------------------------------------------------------------------


def test_parent_table_exists_returns_true():
    cursor = _FakeCursor(table_exists=True)
    assert parent_nodes.parent_table_exists(cursor) is True


def test_parent_table_exists_returns_false():
    cursor = _FakeCursor(table_exists=False)
    assert parent_nodes.parent_table_exists(cursor) is False


# ---------------------------------------------------------------------------
# (g) truncate_parent_nodes runs TRUNCATE_PARENT_NODES constant
# ---------------------------------------------------------------------------


def test_truncate_parent_nodes_executes_truncate_constant():
    cursor = _FakeCursor()
    parent_nodes.truncate_parent_nodes(cursor)
    assert len(cursor._sqls) == 1
    sql, params = cursor._sqls[0]
    assert sql is parent_nodes.TRUNCATE_PARENT_NODES


# ---------------------------------------------------------------------------
# (h) helpers never call commit
# ---------------------------------------------------------------------------


def test_helpers_never_commit():
    """None of the public helpers should call cursor.commit (callers own the tx)."""
    committed = []

    class _CommitTrackingCursor(_FakeCursor):
        def commit(self):
            committed.append(True)

    documents = [{"id": 7, "resource_hash": "hash-7"}]
    parents = {
        1: {"document_id": 7, "metadata": {}},
        2: {"document_id": None, "metadata": {"resource_hash": "hash-7"}},
    }
    cursor = _CommitTrackingCursor(
        parents=parents, chunks=[], documents=documents, table_exists=True
    )

    parent_nodes.delete_unreferenced_parents(cursor, 7)
    parent_nodes.delete_unreferenced_parents_for_resource(cursor, "hash-7")
    parent_nodes.parent_table_exists(cursor)
    parent_nodes.truncate_parent_nodes(cursor)

    assert committed == []


# ---------------------------------------------------------------------------
# End-to-end tests — drive VectorStoreManager with a fully stateful fake
# ---------------------------------------------------------------------------

# Langchain stubs (guarded so a repeat import in the same pytest session is a no-op).
if "langchain_core" not in sys.modules:
    sys.modules["langchain_core"] = types.ModuleType("langchain_core")

if "langchain_core.documents" not in sys.modules:
    _lc_docs = types.ModuleType("langchain_core.documents")
    _lc_docs.Document = object
    sys.modules["langchain_core.documents"] = _lc_docs

if "langchain_core.embeddings" not in sys.modules:
    _lc_emb = types.ModuleType("langchain_core.embeddings")
    _lc_emb.Embeddings = object
    sys.modules["langchain_core.embeddings"] = _lc_emb

if "langchain_core.vectorstores" not in sys.modules:
    _lc_vs = types.ModuleType("langchain_core.vectorstores")
    _lc_vs.VectorStore = object
    sys.modules["langchain_core.vectorstores"] = _lc_vs

if "nltk" not in sys.modules:
    _nltk = types.ModuleType("nltk")
    _nltk.tokenize = types.SimpleNamespace(word_tokenize=lambda text: text.split())
    _nltk.stem = types.SimpleNamespace(
        PorterStemmer=lambda: types.SimpleNamespace(stem=lambda w: w)
    )
    _nltk.download = lambda *_args, **_kwargs: None
    sys.modules["nltk"] = _nltk

if "langchain_text_splitters" not in sys.modules:
    sys.modules["langchain_text_splitters"] = types.ModuleType(
        "langchain_text_splitters"
    )

if "langchain_text_splitters.character" not in sys.modules:
    _lts_char = types.ModuleType("langchain_text_splitters.character")

    class _DummySplitter:
        def __init__(self, *a, **kw):
            pass

        def split_documents(self, docs):
            return docs

    _lts_char.CharacterTextSplitter = _DummySplitter
    sys.modules["langchain_text_splitters.character"] = _lts_char

if "langchain_community" not in sys.modules:
    sys.modules["langchain_community"] = types.ModuleType("langchain_community")

if "langchain_community.document_loaders" not in sys.modules:
    _lc_loaders = types.ModuleType("langchain_community.document_loaders")

    class _DummyDocLoader:
        def __init__(self, *_a, **_kw):
            pass

        def load(self):
            return []

    _lc_loaders.BSHTMLLoader = _DummyDocLoader
    _lc_loaders.NotebookLoader = _DummyDocLoader
    _lc_loaders.PyPDFLoader = _DummyDocLoader
    _lc_loaders.PythonLoader = _DummyDocLoader
    _lc_loaders.TextLoader = _DummyDocLoader
    sys.modules["langchain_community.document_loaders"] = _lc_loaders

if "langchain_community.document_loaders.text" not in sys.modules:
    _lc_text = types.ModuleType("langchain_community.document_loaders.text")
    _lc_text.TextLoader = sys.modules["langchain_community.document_loaders"].TextLoader
    sys.modules["langchain_community.document_loaders.text"] = _lc_text

from src.data_manager.vectorstore import manager as manager_module  # noqa: E402
from src.data_manager.vectorstore.manager import VectorStoreManager  # noqa: E402
from src.data_manager.vectorstore.node_parsing import (  # noqa: E402
    CHILD_CHUNK_OVERLAP,
    CHILD_EMBEDDING_DIM,
    DEFAULT_CHILD_CHUNK_SIZE,
    DEFAULT_PARENT_CHUNK_SIZE,
    HierarchicalNode,
)

_EMBED_DIM = CHILD_EMBEDDING_DIM


class _InlineFuture:
    def __init__(self, fn, *args, **kwargs):
        self._exc = None
        self._result = None
        try:
            self._result = fn(*args, **kwargs)
        except Exception as exc:  # pragma: no cover
            self._exc = exc

    def result(self):
        if self._exc:
            raise self._exc
        return self._result


class _InlineExecutor:
    def __init__(self, max_workers=1):
        self.max_workers = max_workers

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def submit(self, fn, *args, **kwargs):
        return _InlineFuture(fn, *args, **kwargs)


class _E2EFakeCursor:
    """Stateful cursor combining the D6 predicate fake with the manager SQL surface.

    Handles parent INSERT RETURNING id, chunk DELETE (by resource_hash + collection),
    parent-node delete constants (by identity), TRUNCATE, PARENT_TABLE_EXISTS, and
    treats all other SQL (SAVEPOINT, CREATE, UPDATE …) as no-ops.
    execute_values for chunk inserts is NOT handled here; tests monkeypatch it.
    """

    def __init__(self, table_exists=True):
        self.parents = {}
        self.chunks = []
        self.table_exists = table_exists
        self._parent_seq = 0
        self._next_fetchone = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        if sql is parent_nodes.PARENT_TABLE_EXISTS:
            self._next_fetchone = (self.table_exists,)
            self.rowcount = -1
            return

        if (
            isinstance(sql, str)
            and "INSERT INTO document_parent_nodes" in sql
            and "RETURNING id" in sql
        ):
            self._parent_seq += 1
            doc_id, parent_idx, _parent_text, metadata_json = params
            meta = _json.loads(metadata_json) if isinstance(metadata_json, str) else {}
            self.parents[self._parent_seq] = {
                "document_id": doc_id,
                "parent_index": parent_idx,
                "metadata": meta,
            }
            self._next_fetchone = (self._parent_seq,)
            self.rowcount = 1
            return

        if isinstance(sql, str) and "DELETE FROM document_chunks" in sql and params:
            resource_hash = params[0]
            collection = params[1] if len(params) > 1 else None
            before = len(self.chunks)
            self.chunks = [
                c
                for c in self.chunks
                if not (
                    c.get("resource_hash") == resource_hash
                    and c.get("collection") == collection
                )
            ]
            self.rowcount = before - len(self.chunks)
            return

        if sql is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT:
            (document_id,) = params
            referenced = {c["parent_id"] for c in self.chunks}
            to_delete = [
                pid
                for pid, p in list(self.parents.items())
                if p["document_id"] == document_id and pid not in referenced
            ]
            for pid in to_delete:
                del self.parents[pid]
            self.rowcount = len(to_delete)
            return

        if sql is parent_nodes.DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE:
            resource_hash = params[0]
            doc_ids_for_hash = {
                p["document_id"]
                for p in self.parents.values()
                if p["document_id"] is not None
                and p["metadata"].get("resource_hash") == resource_hash
            }
            referenced = {c["parent_id"] for c in self.chunks}
            to_delete = [
                pid
                for pid, p in list(self.parents.items())
                if (
                    p["document_id"] in doc_ids_for_hash
                    or (
                        p["document_id"] is None
                        and p["metadata"].get("resource_hash") == resource_hash
                    )
                )
                and pid not in referenced
            ]
            for pid in to_delete:
                del self.parents[pid]
            self.rowcount = len(to_delete)
            return

        if sql is parent_nodes.TRUNCATE_PARENT_NODES:
            self.parents.clear()
            self.rowcount = 0
            return

        # SAVEPOINT, RELEASE, ROLLBACK, CREATE, UPDATE, etc. — no-op
        self.rowcount = 0

    def fetchone(self):
        return self._next_fetchone

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _e2e_execute_values(cursor, sql, data, template=None):
    """Fake execute_values: upsert-inserts chunks by (document_id, chunk_index).

    Replacing the old chunk with the same key simulates the re-ingest semantics
    that _remove_from_postgres achieves in a real DB before _add_to_postgres runs.
    """
    if "INSERT INTO document_chunks" not in sql:
        return
    for row in data:
        meta = _json.loads(row[4]) if isinstance(row[4], str) else {}
        new_chunk = {
            "document_id": row[0],
            "chunk_index": row[1],
            "parent_id": meta.get("parent_id"),
            "resource_hash": meta.get("resource_hash"),
            "collection": meta.get("collection"),
        }
        cursor.chunks = [
            c
            for c in cursor.chunks
            if not (
                c.get("document_id") == new_chunk["document_id"]
                and c.get("chunk_index") == new_chunk["chunk_index"]
            )
        ]
        cursor.chunks.append(new_chunk)


def _make_manager_e2e():
    mgr = VectorStoreManager.__new__(VectorStoreManager)
    mgr.parallel_workers = 1
    mgr.collection_name = "test_collection"
    mgr.chunking_strategy = "sentence"
    mgr.hierarchical_chunking = True
    mgr.parent_chunk_size = DEFAULT_PARENT_CHUNK_SIZE
    mgr.child_chunk_size = DEFAULT_CHILD_CHUNK_SIZE
    mgr.child_chunk_overlap = CHILD_CHUNK_OVERLAP
    mgr._data_manager_config = {"stemming": {"enabled": False}}
    mgr._pg_config = {"host": "localhost"}
    mgr.embedding_dimensions = _EMBED_DIM
    mgr.embedding_model = SimpleNamespace(
        embed_documents=lambda texts: [[0.0] * _EMBED_DIM for _ in texts]
    )
    return mgr


def _wire_e2e(monkeypatch, cursor, manager):
    def _fake_connect(**_kwargs):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        conn.cursor.return_value.__exit__.return_value = False
        return conn

    monkeypatch.setattr(manager_module.psycopg2, "connect", _fake_connect)
    monkeypatch.setattr(
        manager_module.psycopg2.extras, "execute_values", _e2e_execute_values
    )
    monkeypatch.setattr(manager_module, "ThreadPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(manager_module, "as_completed", lambda futures: list(futures))


def _single_parent_nodes(document, strategy="sentence", **_kw):
    return [
        HierarchicalNode(
            parent_index=0,
            parent_text="Parent context.",
            child_texts=["child one."],
            metadata={},
        )
    ]


def test_e2e_double_add_leaves_one_parent_per_index(monkeypatch):
    """Running _add_to_postgres twice for one document leaves exactly one parent per
    parent_index, and every remaining chunk's parent_id names a surviving parent."""
    cursor = _E2EFakeCursor()
    manager = _make_manager_e2e()
    catalog = MagicMock()
    catalog.get_document_id.return_value = 42
    catalog.get_metadata_for_hash.return_value = {}
    manager._catalog = catalog
    doc = SimpleNamespace(page_content="text", metadata={})
    manager.loader = lambda _path: SimpleNamespace(load=lambda: [doc])
    monkeypatch.setattr(
        manager_module, "build_hierarchical_nodes", _single_parent_nodes
    )
    _wire_e2e(monkeypatch, cursor, manager)

    manager._add_to_postgres({"hash-1": "/tmp/doc.html"})
    manager._add_to_postgres({"hash-1": "/tmp/doc.html"})

    doc42 = {pid: p for pid, p in cursor.parents.items() if p["document_id"] == 42}
    indices = [p["parent_index"] for p in doc42.values()]
    assert len(indices) == len(set(indices)), "duplicate parent_index after double-add"
    assert set(indices) == {0}

    surviving = set(cursor.parents)
    for chunk in cursor.chunks:
        assert (
            chunk["parent_id"] in surviving
        ), f"chunk has orphaned parent_id {chunk['parent_id']}"


def test_e2e_remove_then_add_leaves_one_parent_per_index(monkeypatch):
    """The _sync_vectorstore order (_remove_from_postgres then _add_to_postgres) leaves
    one parent per parent_index and every chunk's parent_id names a surviving parent."""
    cursor = _E2EFakeCursor()
    manager = _make_manager_e2e()
    catalog = MagicMock()
    catalog.get_document_id.return_value = 42
    catalog.get_metadata_for_hash.return_value = {}
    manager._catalog = catalog
    doc = SimpleNamespace(page_content="text", metadata={})
    manager.loader = lambda _path: SimpleNamespace(load=lambda: [doc])
    monkeypatch.setattr(
        manager_module, "build_hierarchical_nodes", _single_parent_nodes
    )
    _wire_e2e(monkeypatch, cursor, manager)

    manager._add_to_postgres({"hash-1": "/tmp/doc.html"})
    manager._remove_from_postgres(["hash-1"])
    manager._add_to_postgres({"hash-1": "/tmp/doc.html"})

    doc42 = {pid: p for pid, p in cursor.parents.items() if p["document_id"] == 42}
    indices = [p["parent_index"] for p in doc42.values()]
    assert len(indices) == len(set(indices)), "duplicate parent_index after remove+add"
    assert set(indices) == {0}

    surviving = set(cursor.parents)
    for chunk in cursor.chunks:
        assert (
            chunk["parent_id"] in surviving
        ), f"chunk has orphaned parent_id {chunk['parent_id']}"


def test_e2e_cross_collection_parent_survives_both_orders(monkeypatch):
    """A parent referenced by a chunk in another collection is kept through both
    the double-add order and the remove+add order."""
    cursor = _E2EFakeCursor()
    manager = _make_manager_e2e()
    catalog = MagicMock()
    catalog.get_document_id.return_value = 42
    catalog.get_metadata_for_hash.return_value = {}
    manager._catalog = catalog
    doc = SimpleNamespace(page_content="text", metadata={})
    manager.loader = lambda _path: SimpleNamespace(load=lambda: [doc])
    monkeypatch.setattr(
        manager_module, "build_hierarchical_nodes", _single_parent_nodes
    )
    _wire_e2e(monkeypatch, cursor, manager)

    # ── Double-add order ──────────────────────────────────────────────────────
    manager._add_to_postgres({"hash-1": "/tmp/doc.html"})
    first_id = next(pid for pid, p in cursor.parents.items() if p["document_id"] == 42)
    cursor.chunks.append(
        {
            "document_id": 99,
            "chunk_index": 0,
            "parent_id": first_id,
            "resource_hash": "hash-1",
            "collection": "other_collection",
        }
    )

    manager._add_to_postgres({"hash-1": "/tmp/doc.html"})

    assert (
        first_id in cursor.parents
    ), "cross-collection reference must keep parent through double-add"

    # ── Remove + add order ────────────────────────────────────────────────────
    cursor.parents.clear()
    cursor.chunks.clear()

    manager._add_to_postgres({"hash-1": "/tmp/doc.html"})
    first_id = next(pid for pid, p in cursor.parents.items() if p["document_id"] == 42)
    cursor.chunks.append(
        {
            "document_id": 99,
            "chunk_index": 0,
            "parent_id": first_id,
            "resource_hash": "hash-1",
            "collection": "other_collection",
        }
    )

    manager._remove_from_postgres(["hash-1"])
    manager._add_to_postgres({"hash-1": "/tmp/doc.html"})

    assert (
        first_id in cursor.parents
    ), "cross-collection reference must keep parent through remove+add"
