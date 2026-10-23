"""Corpus fingerprint v2: hash what the searched collection's retrieval can reach.

v1 hashed every live document, every chunk joined to a document, and every
parent node, in every collection. So an ingest into another collection, or the
#411 orphan-parent cleanup, moved the pin while the searched corpus stayed the
same, and a chunk with no document link was never covered at all.

Two kinds of test live here:

* shape tests run in the gate: they read the SQL the row layer sends through a
  recording cursor;
* behavior tests run the real SQL against Postgres. They need a scratch
  database in ``ARCHI_PROVENANCE_TEST_DSN`` (pgvector installed) and skip
  without it. Each test builds its own schema and drops it.
"""

import os
import uuid

import pytest

from src.utils.benchmark_provenance import (
    CITATION_FIELDS,
    category_map_rows,
    container_category_map_digest,
    container_corpus_fingerprint,
    corpus_fingerprint,
    corpus_state_rows,
    live_category_map,
    live_corpus_fingerprint,
)


class RecordingCursor:
    def __init__(self, rows=()):
        self.calls = []
        self._rows = list(rows)

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return list(self._rows)


def _sql(function, collection="C"):
    cursor = RecordingCursor()
    function(cursor, collection)
    assert len(cursor.calls) == 1
    return cursor.calls[0]


class TestCorpusStateShape:
    def test_the_collection_is_the_only_parameter(self):
        _, params = _sql(corpus_state_rows, "fasrc_with_HuggingFaceEmbeddings")
        assert params == ("fasrc_with_HuggingFaceEmbeddings",)

    def test_chunks_left_join_documents_under_the_retrieval_filter(self):
        sql, _ = _sql(corpus_state_rows)
        assert "LEFT JOIN documents d ON d.id = c.document_id" in sql
        assert (
            "(c.metadata->>'collection' = %s OR c.metadata->>'collection' IS NULL)"
            in sql
        )
        assert "(d.id IS NULL OR d.is_deleted = FALSE)" in sql

    def test_the_chunk_key_falls_back_to_a_row_identity(self):
        sql, _ = _sql(corpus_state_rows)
        assert (
            "COALESCE(s.resource_hash, s.meta->>'resource_hash', "
            "s.meta->>'chunk_id', 'id:' || s.id::text)"
        ) in sql

    def test_values_are_json_arrays_and_never_size_or_model(self):
        sql, _ = _sql(corpus_state_rows)
        assert sql.count("md5(jsonb_build_array(") == 3
        assert "size_bytes" not in sql
        assert "embedding_model" not in sql

    def test_the_citation_fields_are_the_five_retrieval_returns(self):
        assert CITATION_FIELDS == (
            "url",
            "display_name",
            "source_type",
            "title",
            "filename",
        )

    def test_rows_come_back_as_key_value_pairs(self):
        cursor = RecordingCursor(rows=[("chunk:x:0", "abc")])
        assert corpus_state_rows(cursor, "C") == [("chunk:x:0", "abc")]


class TestCategoryMapShape:
    def test_scoped_to_documents_with_an_in_scope_chunk(self):
        sql, params = _sql(category_map_rows)
        assert params == ("C",)
        assert "EXISTS" in sql
        assert (
            "(c.metadata->>'collection' = %s OR c.metadata->>'collection' IS NULL)"
            in sql
        )
        assert "url IS NOT NULL" in sql

    def test_rows_stay_url_category_tuples(self):
        cursor = RecordingCursor(rows=[("https://a", "jobs")])
        assert category_map_rows(cursor, "C") == [("https://a", "jobs")]


class TestFingerprintVersion:
    def test_v2_digest_carries_the_version_prefix(self):
        assert corpus_fingerprint([("k", "v")], version="v2").startswith("sha256/v2:")

    def test_v1_prefix_is_unchanged_by_default(self):
        digest = corpus_fingerprint([("k", "v")])
        assert digest.startswith("sha256:")
        assert (
            digest.split(":", 1)[1]
            == corpus_fingerprint([("k", "v")], version="v2").split(":", 1)[1]
        )


class FakePool:
    """``ConnectionPool`` stand-in: one connection, one recording cursor."""

    def __init__(self, rows=()):
        self.cursor = RecordingCursor(rows)
        pool = self

        class Connection:
            def cursor(self):
                class Context:
                    def __enter__(self):
                        return pool.cursor

                    def __exit__(self, *exc):
                        return False

                return Context()

        self._connection = Connection()

    def get_connection(self):
        pool = self

        class Context:
            def __enter__(self):
                return pool._connection

            def __exit__(self, *exc):
                return False

        return Context()


CONFIG = {
    "data_manager": {
        "collection_name": "fasrc",
        "embedding_name": "HuggingFaceEmbeddings",
        "embedding_class_map": {
            "HuggingFaceEmbeddings": {"kwargs": {"model_name": "m"}}
        },
    }
}


class TestConvenienceLayer:
    def test_fingerprint_reads_the_configured_collection(self):
        pool = FakePool(rows=[("chunk:x:0", "v")])
        digest = live_corpus_fingerprint(pool, CONFIG)
        assert digest == corpus_fingerprint([("chunk:x:0", "v")], version="v2")
        assert pool.cursor.calls[0][1] == ("fasrc_with_HuggingFaceEmbeddings",)

    def test_category_map_returns_rows_records_and_digest(self):
        pool = FakePool(rows=[("https://a/x/", "jobs")])
        rows, records, digest = live_category_map(pool, CONFIG)
        assert rows == [("https://a/x/", "jobs")]
        assert records == ["https://a/x\tjobs"]
        assert digest.startswith("sha256:")
        assert pool.cursor.calls[0][1] == ("fasrc_with_HuggingFaceEmbeddings",)

    def test_a_config_without_a_collection_is_refused(self):
        with pytest.raises(ValueError, match="collection"):
            live_corpus_fingerprint(FakePool(), {})
        with pytest.raises(ValueError, match="collection"):
            live_category_map(FakePool(), {})

    def test_config_is_a_required_argument(self):
        with pytest.raises(TypeError):
            live_corpus_fingerprint(
                FakePool()
            )  # pylint: disable=no-value-for-parameter


class TestContainerEntryPoints:
    """What ``feature_matrix/lib.sh`` runs inside the stack's data-manager.

    The snippet builds its own factory from the environment. It must install
    that factory before it reads the config: ``get_full_config()`` reads through
    ``PostgresServiceFactory.get_instance()`` and raises without one.
    """

    @pytest.fixture
    def factory(self, monkeypatch):
        from src.utils import config_access
        from src.utils.postgres_service_factory import PostgresServiceFactory

        monkeypatch.setattr(PostgresServiceFactory, "_instance", None)
        pool = FakePool(rows=[("https://a/x/", "jobs")])

        class Factory:
            connection_pool = pool

        built = Factory()
        monkeypatch.setattr(
            PostgresServiceFactory, "from_env", classmethod(lambda cls: built)
        )

        def full_config():
            assert PostgresServiceFactory.get_instance() is built
            return CONFIG

        monkeypatch.setattr(config_access, "get_full_config", full_config)
        return pool

    def test_fingerprint_equals_the_harness_digest(self, factory):
        assert container_corpus_fingerprint() == corpus_fingerprint(
            [("https://a/x/", "jobs")], version="v2"
        )
        assert factory.cursor.calls[0][1] == ("fasrc_with_HuggingFaceEmbeddings",)

    def test_category_digest_equals_the_harness_digest(self, factory):
        from src.utils.benchmark_provenance import live_category_map

        assert container_category_map_digest() == live_category_map(factory, CONFIG)[2]

    def test_without_the_install_the_config_read_fails(self, monkeypatch):
        """Why the install is there: this is the error the old snippet hit."""
        from src.utils.config_access import ConfigNotReadyError, get_full_config
        from src.utils.postgres_service_factory import PostgresServiceFactory

        monkeypatch.setattr(PostgresServiceFactory, "_instance", None)
        with pytest.raises(ConfigNotReadyError):
            get_full_config()


# --------------------------------------------------------------------------
# Behavior against real Postgres
# --------------------------------------------------------------------------

DSN = os.environ.get("ARCHI_PROVENANCE_TEST_DSN")

SCHEMA = """
CREATE TABLE documents (
    id SERIAL PRIMARY KEY,
    resource_hash VARCHAR(64) UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    source_type VARCHAR(50) NOT NULL,
    url TEXT,
    size_bytes BIGINT,
    extra_json JSONB,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE document_chunks (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(3),
    metadata JSONB
);
CREATE TABLE document_parent_nodes (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    parent_index INTEGER NOT NULL,
    parent_text TEXT NOT NULL,
    metadata JSONB
);
INSERT INTO documents (resource_hash, display_name, source_type, url, size_bytes,
                       extra_json)
VALUES ('ha', 'Page A', 'web', 'https://a', 100,
        '{"title": "A", "category": "jobs"}'),
       ('hb', 'Page B', 'web', 'https://b', 200,
        '{"title": "B", "category": "storage"}');
INSERT INTO document_parent_nodes (document_id, parent_index, parent_text, metadata)
VALUES (1, 0, 'parent text A', '{"title": "PA", "url": "https://a"}'),
       (1, 1, 'orphan parent', '{"title": "orphan"}');
INSERT INTO document_chunks (document_id, chunk_index, chunk_text, embedding, metadata)
VALUES (1, 0, 'a zero', '[1,0,0]',
        '{"collection": "C", "parent_id": "1", "embedding_model": "m1"}'),
       (1, 1, 'a one', '[0,1,0]',
        '{"collection": "C", "parent_id": "1", "embedding_model": "m1"}'),
       (2, 0, 'b zero', '[0,0,1]', '{"collection": "OTHER"}'),
       (NULL, 0, 'loose text', '[1,1,0]',
        '{"collection": "C", "chunk_id": "loose-1", "url": "https://loose",
          "title": "Loose", "embedding_model": "m1"}');
"""


@pytest.fixture
def pg():
    if not DSN:
        pytest.skip("ARCHI_PROVENANCE_TEST_DSN is not set")
    import psycopg2

    schema = f"t_{uuid.uuid4().hex[:12]}"
    connection = psycopg2.connect(DSN)
    connection.autocommit = True
    cursor = connection.cursor()
    cursor.execute(f"CREATE SCHEMA {schema}")
    cursor.execute(f"SET search_path TO {schema}, public")
    cursor.execute(SCHEMA)
    try:
        yield cursor
    finally:
        cursor.execute(f"DROP SCHEMA {schema} CASCADE")
        connection.close()


def _digest(cursor, collection="C"):
    return corpus_fingerprint(corpus_state_rows(cursor, collection), version="v2")


def _moves(cursor, sql):
    before = _digest(cursor)
    cursor.execute(sql)
    return _digest(cursor) != before


class TestCorpusStateBehavior:
    def test_digest_is_stable_and_prefixed(self, pg):
        assert _digest(pg) == _digest(pg)
        assert _digest(pg).startswith("sha256/v2:")

    def test_ingest_into_another_collection_does_not_move_it(self, pg):
        assert not _moves(
            pg,
            "INSERT INTO document_chunks (document_id, chunk_index, chunk_text, "
            "metadata) VALUES (2, 1, 'b one', '{\"collection\": \"OTHER\"}')",
        )

    def test_a_collection_tag_change_moves_it(self, pg):
        assert _moves(
            pg,
            "UPDATE document_chunks SET metadata = metadata || "
            "'{\"collection\": null}' WHERE document_id = 1 AND chunk_index = 0",
        )

    def test_a_chunk_leaving_the_collection_moves_it(self, pg):
        assert _moves(
            pg,
            "UPDATE document_chunks SET metadata = metadata || "
            '\'{"collection": "OTHER"}\' WHERE document_id = 1 AND chunk_index = 0',
        )

    def test_orphan_parents_do_not_move_it(self, pg):
        assert not _moves(pg, "DELETE FROM document_parent_nodes WHERE id = 2")
        assert not _moves(
            pg,
            "INSERT INTO document_parent_nodes (document_id, parent_index, "
            "parent_text) VALUES (1, 7, 'new orphan')",
        )

    def test_documentless_chunk_text_moves_it(self, pg):
        assert _moves(
            pg,
            "UPDATE document_chunks SET chunk_text = 'loose edited' "
            "WHERE document_id IS NULL",
        )

    @pytest.mark.parametrize(
        "change",
        [
            "url = 'https://a2'",
            "display_name = 'Page A2'",
            "source_type = 'git'",
            'extra_json = extra_json || \'{"title": "A2"}\'',
        ],
    )
    def test_retrieval_visible_document_fields_move_it(self, pg, change):
        assert _moves(pg, f"UPDATE documents SET {change} WHERE id = 1")

    def test_a_category_only_change_does_not_move_it(self, pg):
        assert not _moves(
            pg,
            "UPDATE documents SET extra_json = extra_json || "
            '\'{"category": "other"}\' WHERE id = 1',
        )

    def test_a_size_only_change_does_not_move_it(self, pg):
        assert not _moves(pg, "UPDATE documents SET size_bytes = 999 WHERE id = 1")

    def test_a_vector_set_to_null_moves_it(self, pg):
        assert _moves(
            pg,
            "UPDATE document_chunks SET embedding = NULL "
            "WHERE document_id = 1 AND chunk_index = 1",
        )

    def test_a_re_embed_under_another_model_does_not_move_it(self, pg):
        assert not _moves(
            pg,
            "UPDATE document_chunks SET embedding = '[0.5,0.5,0.5]', "
            'metadata = metadata || \'{"embedding_model": "m2"}\' '
            "WHERE metadata->>'collection' = 'C'",
        )

    def test_a_title_change_on_a_document_without_a_url_moves_it(self, pg):
        pg.execute("UPDATE documents SET url = NULL WHERE id = 1")
        assert _moves(
            pg,
            "UPDATE documents SET extra_json = extra_json || "
            '\'{"title": "A3"}\' WHERE id = 1',
        )

    def test_documentless_chunk_citation_metadata_moves_it(self, pg):
        assert _moves(
            pg,
            "UPDATE document_chunks SET metadata = metadata || "
            '\'{"url": "https://loose2"}\' WHERE document_id IS NULL',
        )

    def test_referenced_parent_metadata_moves_it(self, pg):
        assert _moves(
            pg,
            "UPDATE document_parent_nodes SET metadata = metadata || "
            '\'{"title": "PA2"}\' WHERE id = 1',
        )

    def test_a_deleted_document_drops_out(self, pg):
        assert _moves(pg, "UPDATE documents SET is_deleted = TRUE WHERE id = 1")

    def test_null_and_empty_string_hash_differently(self, pg):
        pg.execute("UPDATE documents SET url = NULL WHERE id = 1")
        with_null = _digest(pg)
        pg.execute("UPDATE documents SET url = '' WHERE id = 1")
        assert _digest(pg) != with_null

    def test_field_boundaries_are_unambiguous(self, pg):
        pg.execute(
            "UPDATE documents SET display_name = 'a|b', source_type = 'c' "
            "WHERE id = 1"
        )
        first = _digest(pg)
        pg.execute(
            "UPDATE documents SET display_name = 'a', source_type = 'b|c' "
            "WHERE id = 1"
        )
        assert _digest(pg) != first


class TestCategoryMapBehavior:
    def _map(self, cursor):
        return sorted(category_map_rows(cursor, "C"))

    def test_only_documents_with_an_in_scope_chunk(self, pg):
        assert self._map(pg) == [("https://a", "jobs")]

    def test_a_relabel_in_another_collection_is_invisible(self, pg):
        before = self._map(pg)
        pg.execute(
            "UPDATE documents SET extra_json = extra_json || "
            '\'{"category": "x"}\' WHERE id = 2'
        )
        assert self._map(pg) == before

    def test_a_relabel_in_scope_is_visible(self, pg):
        pg.execute(
            "UPDATE documents SET extra_json = extra_json || "
            '\'{"category": "x"}\' WHERE id = 1'
        )
        assert self._map(pg) == [("https://a", "x")]


class TestLinkedChunkCitationMetadata:
    """A linked chunk's own citation fields reach the agent too.

    ``filename`` comes only from the chunk's metadata, and ``_merge_row_metadata``
    overlays a document column only when it is non-empty, so the chunk's own
    ``url``/``title`` surface when the document leaves them empty.
    """

    def test_a_filename_change_on_a_linked_chunk_moves_it(self, pg):
        assert _moves(
            pg,
            "UPDATE document_chunks SET metadata = metadata || "
            '\'{"filename": "renamed.md"}\' WHERE document_id = 1 AND chunk_index = 0',
        )

    def test_a_linked_chunk_url_moves_it_when_the_document_has_none(self, pg):
        pg.execute("UPDATE documents SET url = NULL WHERE id = 1")
        assert _moves(
            pg,
            "UPDATE document_chunks SET metadata = metadata || "
            '\'{"url": "https://chunk-url"}\' WHERE document_id = 1 AND chunk_index = 0',
        )
