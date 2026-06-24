"""Tests for the runtime hierarchical schema-ensure step (task 1.5).

``init.sql`` only runs when Postgres initializes a fresh data directory, so a
deployment upgraded over a pre-existing volume never gets ``document_parent_nodes``
and would fail with an undefined-table error when the hierarchical write/read
paths run. ``ensure_hierarchical_schema`` is an idempotent
``CREATE TABLE/INDEX IF NOT EXISTS`` step invoked before those paths touch the
table. These tests exercise the real function: it creates the table/index when
absent and is a no-op (no error, no row changes) when already present.
"""

from src.data_manager.vectorstore.schema import ensure_hierarchical_schema


class _FakeSchemaDB:
    """In-memory simulation of CREATE ... IF NOT EXISTS semantics.

    Tracks which schema objects exist and records the ones a run actually
    creates, plus per-table rows so a test can assert ensure never alters data.
    """

    def __init__(self, tables=None, indexes=None):
        self.tables = set(tables or [])
        self.indexes = set(indexes or [])
        self.rows = {}
        self.created = []


class _FakeCursor:
    """DB-API-ish cursor applying IF-NOT-EXISTS semantics against a fake DB."""

    def __init__(self, db):
        self.db = db
        self.statements = []

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.statements.append(norm)
        if "CREATE TABLE IF NOT EXISTS document_parent_nodes" in norm:
            if "document_parent_nodes" in self.db.tables:
                return  # no-op: table already present
            self.db.tables.add("document_parent_nodes")
            self.db.created.append("document_parent_nodes")
        elif "CREATE INDEX IF NOT EXISTS idx_parent_nodes_document" in norm:
            if "idx_parent_nodes_document" in self.db.indexes:
                return  # no-op: index already present
            self.db.indexes.add("idx_parent_nodes_document")
            self.db.created.append("idx_parent_nodes_document")
        else:  # pragma: no cover - guards against unexpected DDL drift
            raise AssertionError(f"unexpected SQL issued by ensure step: {norm}")


def test_creates_table_and_index_when_absent():
    db = _FakeSchemaDB()
    cursor = _FakeCursor(db)

    ensure_hierarchical_schema(cursor)

    assert "document_parent_nodes" in db.tables
    assert "idx_parent_nodes_document" in db.indexes
    # Table is created before its index (index depends on the table existing).
    assert db.created == ["document_parent_nodes", "idx_parent_nodes_document"]


def test_uses_if_not_exists_ddl():
    db = _FakeSchemaDB()
    cursor = _FakeCursor(db)

    ensure_hierarchical_schema(cursor)

    assert any(
        "CREATE TABLE IF NOT EXISTS document_parent_nodes" in s
        for s in cursor.statements
    )
    assert any(
        "CREATE INDEX IF NOT EXISTS idx_parent_nodes_document" in s
        for s in cursor.statements
    )


def test_idempotent_noop_when_already_present():
    db = _FakeSchemaDB(
        tables={"document_parent_nodes"},
        indexes={"idx_parent_nodes_document"},
    )
    db.rows["document_parent_nodes"] = [{"id": 1, "parent_text": "existing"}]
    cursor = _FakeCursor(db)

    # Must not raise against a database that already has the schema.
    ensure_hierarchical_schema(cursor)

    # Nothing was (re)created and existing rows are untouched.
    assert db.created == []
    assert db.rows["document_parent_nodes"] == [{"id": 1, "parent_text": "existing"}]


def test_repeated_calls_are_stable():
    db = _FakeSchemaDB()
    cursor = _FakeCursor(db)

    ensure_hierarchical_schema(cursor)
    first_created = list(db.created)
    ensure_hierarchical_schema(cursor)

    # Second run creates nothing new; the schema converges and stays put.
    assert first_created == ["document_parent_nodes", "idx_parent_nodes_document"]
    assert db.created == first_created
