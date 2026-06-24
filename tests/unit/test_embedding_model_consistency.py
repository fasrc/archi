"""Embedding-model consistency tests (task 5.2).

Spec requirement "Embedding-model consistency": child nodes and query text are
embedded with archi's single configured embedding model
(``sentence-transformers/all-MiniLM-L6-v2``, 384 dimensions). Two scenarios:

* *Child embedding dimension matches the column* — a wrong-dimension child
  embedding raises rather than reaching ``document_chunks.embedding``.
* *Query embedded with the same model* — retrieval embeds the query with the
  exact same configured embedder instance used to embed child nodes.

The first scenario is exercised through the real ``embed_child_nodes`` dimension
guard; the second by sharing one embedder instance across both the child path
(``embed_child_nodes``) and the query path (``PostgresVectorStore.hybrid_search``)
and asserting the same object embeds both.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.data_manager.vectorstore.node_parsing import (
    CHILD_EMBEDDING_DIM,
    embed_child_nodes,
)
from src.data_manager.vectorstore.postgres_vectorstore import PostgresVectorStore


class _RecordingEmbedder:
    """A single configured embedder recording both child and query embeds.

    Stands in for archi's LangChain ``Embeddings`` model. ``embed_documents``
    serves the ingestion (child) path; ``embed_query`` serves the retrieval
    (query) path. Both produce :data:`CHILD_EMBEDDING_DIM`-dimensional vectors so
    a shared instance can be driven through both paths in one test.
    """

    def __init__(self, dim: int = CHILD_EMBEDDING_DIM):
        self.dim = dim
        self.document_calls: list = []
        self.query_calls: list = []

    def embed_documents(self, texts):
        self.document_calls.append(list(texts))
        return [[0.0] * self.dim for _ in texts]

    def embed_query(self, text):
        self.query_calls.append(text)
        return [0.0] * self.dim


# ---------------------------------------------------------------------------
# Scenario: child embedding dimension matches the column (mismatch raises)
# ---------------------------------------------------------------------------


def test_child_embedding_dimension_mismatch_raises():
    """A wrong-dimension child embedding fails loudly, not silently stored."""
    embedder = _RecordingEmbedder(dim=CHILD_EMBEDDING_DIM + 1)

    with pytest.raises(ValueError, match="expected 384"):
        embed_child_nodes(embedder, ["a child sentence."])


def test_child_embedding_correct_dimension_is_accepted():
    """A 384-dim child embedding passes the guard."""
    embedder = _RecordingEmbedder()

    embeddings = embed_child_nodes(embedder, ["child a.", "child b."])

    assert [len(vec) for vec in embeddings] == [CHILD_EMBEDDING_DIM] * 2
    assert embedder.document_calls == [["child a.", "child b."]]


# ---------------------------------------------------------------------------
# Scenario: query embedded with the same model used for child nodes
# ---------------------------------------------------------------------------


def _mock_connection_for_hybrid_search():
    """Build a mock connection that satisfies ``hybrid_search``.

    The BM25 index lookup (``fetchone``) reports an index exists; the scored
    query (``fetchall``) returns one child row so a Document is produced.
    """
    conn = MagicMock()
    cursor = MagicMock()
    cursor_context = MagicMock()
    cursor_context.__enter__ = MagicMock(return_value=cursor)
    cursor_context.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor_context

    cursor.fetchone.return_value = {"relname": "document_chunks_bm25_idx"}
    cursor.fetchall.return_value = [
        {
            "id": 1,
            "chunk_text": "a matching child chunk",
            "metadata": json.dumps({"parent_id": 7}),
            "semantic_score": 0.9,
            "bm25_score": 0.5,
            "combined_score": 0.8,
            "resource_hash": "hash-1",
            "display_name": "doc.html",
            "source_type": "web",
            "url": "http://example/doc",
        }
    ]
    return conn


def test_query_and_child_embedded_by_the_same_model():
    """One configured embedder serves both the child and query paths."""
    shared = _RecordingEmbedder()

    # Child (ingestion) path embeds children with the shared model.
    embed_child_nodes(shared, ["child one.", "child two."])

    # Query (retrieval) path: the vectorstore is wired with the SAME instance.
    conn = _mock_connection_for_hybrid_search()
    with patch.object(PostgresVectorStore, "_get_connection", return_value=conn):
        store = PostgresVectorStore(
            pg_config={"host": "localhost"},
            embedding_function=shared,
            collection_name="test_collection",
            distance_metric="cosine",
        )
        # The store exposes exactly the configured embedder (same object).
        assert store.embeddings is shared

        results = store.hybrid_search("what is the answer?", k=5)

    # The query was embedded by the same instance that embedded the children.
    assert shared.query_calls == ["what is the answer?"]
    assert shared.document_calls == [["child one.", "child two."]]
    # Sanity: hybrid_search returned the child document for parent expansion.
    assert results and results[0][0].metadata["parent_id"] == 7


def test_query_embedding_dimension_matches_child_embedding_dimension():
    """Query and child vectors share the 384-dim column width."""
    shared = _RecordingEmbedder()

    child_vectors = embed_child_nodes(shared, ["only child."])
    query_vector = shared.embed_query("a query")

    assert len(query_vector) == CHILD_EMBEDDING_DIM
    assert len(child_vectors[0]) == len(query_vector)
