"""Tests for the parent-node cleanup helper module (issue #411).

Uses a stateful fake cursor that applies the D1 predicate in Python so
tests can drive the helpers without a real Postgres connection.
"""

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
