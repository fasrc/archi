"""The start guard: a run must not score against an empty or mismatched collection.

The harness and the QA workflow call ``collection_readiness`` before the first
question. Zero chunks, zero chunks with a vector, or a chunk embedded by a model
other than the one the run queries with is fatal: the scores would describe a
different system. Chunks without an ``embedding_model`` tag predate the tag, so
they cannot be verified and are recorded as such, with a warning.

The rules are tested against a fake pool; the SQL runs against the scratch
database in ``ARCHI_PROVENANCE_TEST_DSN`` when it is set.
"""

import logging
import os
import uuid
from contextlib import contextmanager

import pytest

from src.utils.benchmark_provenance import (
    CollectionNotReadyError,
    RetrievalIdentity,
    collection_readiness,
    readiness_counts,
    retrieval_record,
)

IDENTITY = RetrievalIdentity(
    collection="fasrc_with_HuggingFaceEmbeddings",
    embedding_name="HuggingFaceEmbeddings",
    embedding_model="Qwen/Q",
)


class FakePool:
    def __init__(self, row):
        self.row = row
        self.calls = []

    @contextmanager
    def get_connection(self):
        pool = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                pool.calls.append((sql, params))

            def fetchone(self):
                return pool.row

        class Connection:
            def cursor(self):
                return Cursor()

        yield Connection()


def _readiness(row):
    return collection_readiness(FakePool(row), IDENTITY)


class TestRefusals:
    def test_zero_chunks_names_the_collection_and_the_embedding(self):
        with pytest.raises(CollectionNotReadyError) as info:
            _readiness((0, 0, 0, None))
        message = str(info.value)
        assert "fasrc_with_HuggingFaceEmbeddings" in message
        assert "HuggingFaceEmbeddings" in message

    def test_rows_without_any_vector_give_both_counts(self):
        with pytest.raises(CollectionNotReadyError) as info:
            _readiness((12, 0, 0, ["Qwen/Q"]))
        assert "chunk_count=12" in str(info.value)
        assert "usable_chunk_count=0" in str(info.value)

    def test_a_differing_tag_names_both_models(self):
        with pytest.raises(CollectionNotReadyError) as info:
            _readiness((5, 5, 0, ["Qwen/Q", "all-MiniLM"]))
        assert "all-MiniLM" in str(info.value)
        assert "Qwen/Q" in str(info.value)


class TestProvenance:
    def test_every_chunk_untagged_is_config_with_a_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _readiness((5, 5, 5, None))
        assert result["embedding_model_source"] == "config (chunks untagged)"
        assert result["untagged_chunk_count"] == 5
        assert any("untagged" in r.getMessage() for r in caplog.records)

    def test_a_partial_tag_counts_the_untagged_with_a_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _readiness((5, 5, 2, ["Qwen/Q"]))
        assert result["embedding_model_source"] == "chunks (2 untagged)"
        assert result["untagged_chunk_count"] == 2
        assert any("2" in r.getMessage() for r in caplog.records)

    def test_every_tag_matching_is_verified(self):
        result = _readiness((5, 4, 0, ["Qwen/Q"]))
        assert result == {
            "chunk_count": 5,
            "usable_chunk_count": 4,
            "untagged_chunk_count": 0,
            "embedding_model_source": "chunks",
        }

    def test_the_query_is_scoped_to_the_collection(self):
        pool = FakePool((5, 5, 0, ["Qwen/Q"]))
        collection_readiness(pool, IDENTITY)
        [(sql, params)] = pool.calls
        assert params == ("fasrc_with_HuggingFaceEmbeddings",)
        assert (
            "(metadata->>'collection' = %s OR metadata->>'collection' IS NULL)" in sql
        )

    def test_an_identity_without_a_collection_is_refused(self):
        with pytest.raises(CollectionNotReadyError, match="collection"):
            collection_readiness(
                FakePool((1, 1, 0, None)), RetrievalIdentity(None, None, None)
            )

    def test_the_record_has_all_seven_fields(self):
        record = retrieval_record(IDENTITY, _readiness((5, 5, 0, ["Qwen/Q"])))
        assert set(record) == {
            "collection",
            "embedding_name",
            "embedding_model",
            "chunk_count",
            "usable_chunk_count",
            "untagged_chunk_count",
            "embedding_model_source",
        }


DSN = os.environ.get("ARCHI_PROVENANCE_TEST_DSN")


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
    cursor.execute(
        "CREATE TABLE document_chunks (id SERIAL PRIMARY KEY, embedding vector(3), "
        "metadata JSONB)"
    )
    try:
        yield cursor
    finally:
        cursor.execute(f"DROP SCHEMA {schema} CASCADE")
        connection.close()


def test_the_counts_come_from_real_sql(pg):
    pg.execute(
        "INSERT INTO document_chunks (embedding, metadata) VALUES "
        '(\'[1,0,0]\', \'{"collection": "C", "embedding_model": "A"}\'), '
        '(NULL, \'{"collection": "C", "embedding_model": "A"}\'), '
        "('[0,1,0]', '{\"collection\": \"C\"}'), "
        "('[0,0,1]', '{\"embedding_model\": \"B\"}'), "
        '(\'[1,1,0]\', \'{"collection": "OTHER", "embedding_model": "Z"}\')'
    )

    chunk_count, usable, untagged, tags = readiness_counts(pg, "C")

    assert (chunk_count, usable, untagged) == (4, 3, 1)
    assert sorted(tags) == ["A", "B"]
