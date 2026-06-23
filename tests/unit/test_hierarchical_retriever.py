"""
Unit tests for LlamaIndexHierarchicalRetriever (tasks 3.1 + 3.2).

Covers candidate generation via hybrid_search, FlashRank cross-encoder
reranking, child->parent mapping by metadata.parent_id, parent deduplication,
and top-N truncation. Config gating / fallback (task 3.3) is tested separately.

The FlashRank ranker is stubbed so tests neither download nor load the ONNX
model. ``_IdentityRanker`` preserves candidate order (descending synthetic
scores); ``_ReverseRanker`` inverts it to prove the retriever honours rerank
order rather than hybrid-search order.
"""

from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStore

from src.data_manager.vectorstore.postgres_vectorstore import PostgresVectorStore
from src.data_manager.vectorstore.retrievers.hierarchical_retriever import (
    LlamaIndexHierarchicalRetriever,
)

# =============================================================================
# Fixtures
# =============================================================================


def _child(text, parent_id, score=1.0, **extra):
    """Build a (Document, score) child candidate tuple as hybrid_search returns."""
    metadata = {"parent_id": parent_id}
    metadata.update(extra)
    return (Document(page_content=text, metadata=metadata), score)


class _IdentityRanker:
    """Stub FlashRank ranker: preserves passage order, descending scores."""

    def rerank(self, request):
        n = len(request.passages)
        return [
            {"id": p["id"], "score": float(n - i), "text": p["text"]}
            for i, p in enumerate(request.passages)
        ]


class _ReverseRanker:
    """Stub FlashRank ranker: reverses passage order (best = last input)."""

    def rerank(self, request):
        passages = list(request.passages)
        n = len(passages)
        return [
            {"id": p["id"], "score": float(i + 1), "text": p["text"]}
            for i, p in enumerate(reversed(passages))
        ]


def _parent_row(pid, text, **doc_fields):
    """Build a document_parent_nodes row (RealDictCursor style mapping)."""
    row = {
        "id": pid,
        "parent_text": text,
        "metadata": {"section": f"sec-{pid}"},
        "document_id": 10,
        "resource_hash": None,
        "display_name": None,
        "source_type": None,
        "url": None,
    }
    row.update(doc_fields)
    return row


@pytest.fixture
def mock_vectorstore():
    """A real PostgresVectorStore (a VectorStore) with stubbed I/O methods."""
    store = PostgresVectorStore(
        pg_config={
            "host": "localhost",
            "port": 5432,
            "dbname": "archi_test",
            "user": "postgres",
            "password": "testpass",
        },
        embedding_function=MagicMock(),
        collection_name="test_collection",
    )
    # Connection / cursor plumbing for _fetch_parents.
    conn = MagicMock()
    cursor = MagicMock()
    cursor_ctx = MagicMock()
    cursor_ctx.__enter__ = MagicMock(return_value=cursor)
    cursor_ctx.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor_ctx

    store.hybrid_search = MagicMock()
    store._get_connection = MagicMock(return_value=conn)
    store._close_connection = MagicMock(return_value=None)
    store._mock_cursor = cursor  # expose for assertions
    return store


# =============================================================================
# Construction
# =============================================================================


class _NoHybridStore(VectorStore):
    """Minimal real VectorStore that lacks hybrid_search()."""

    def similarity_search(self, query, k=4, **kwargs):  # pragma: no cover - stub
        return []

    @classmethod
    def from_texts(
        cls, texts, embedding, metadatas=None, **kwargs
    ):  # pragma: no cover - stub
        return cls()


def test_requires_hybrid_search():
    """A vectorstore without hybrid_search() is rejected."""
    with pytest.raises(ValueError, match="hybrid_search"):
        LlamaIndexHierarchicalRetriever(vectorstore=_NoHybridStore())


# =============================================================================
# Candidate generation
# =============================================================================


def test_candidate_pool_size_passed_to_hybrid_search(mock_vectorstore):
    mock_vectorstore.hybrid_search.return_value = []
    retriever = LlamaIndexHierarchicalRetriever(
        vectorstore=mock_vectorstore,
        candidate_pool_size=20,
        semantic_weight=0.4,
        bm25_weight=0.6,
    )

    retriever.invoke("how do I request an account")

    mock_vectorstore.hybrid_search.assert_called_once()
    _, kwargs = mock_vectorstore.hybrid_search.call_args
    assert kwargs["k"] == 20
    assert kwargs["semantic_weight"] == 0.4
    assert kwargs["bm25_weight"] == 0.6


def test_empty_candidates_returns_empty(mock_vectorstore):
    mock_vectorstore.hybrid_search.return_value = []
    retriever = LlamaIndexHierarchicalRetriever(vectorstore=mock_vectorstore)

    assert retriever.invoke("nothing") == []
    # No parent lookup when there are no candidates.
    mock_vectorstore._get_connection.assert_not_called()


# =============================================================================
# Child -> parent mapping + dedupe
# =============================================================================


def test_child_hit_returns_parent_context(mock_vectorstore):
    mock_vectorstore.hybrid_search.return_value = [_child("child snippet", parent_id=1)]
    mock_vectorstore._mock_cursor.fetchall.return_value = [
        _parent_row(1, "the full parent paragraph")
    ]
    retriever = LlamaIndexHierarchicalRetriever(
        vectorstore=mock_vectorstore, reranker=_IdentityRanker()
    )

    docs = retriever.invoke("q")

    assert len(docs) == 1
    assert docs[0].page_content == "the full parent paragraph"
    assert docs[0].metadata["parent_id"] == 1


def test_duplicate_parents_are_merged(mock_vectorstore):
    # Three child hits, two share parent 1.
    mock_vectorstore.hybrid_search.return_value = [
        _child("c1", parent_id=1, score=0.9),
        _child("c2", parent_id=2, score=0.8),
        _child("c3", parent_id=1, score=0.7),
    ]
    mock_vectorstore._mock_cursor.fetchall.return_value = [
        _parent_row(1, "parent one"),
        _parent_row(2, "parent two"),
    ]
    retriever = LlamaIndexHierarchicalRetriever(
        vectorstore=mock_vectorstore, reranker=_IdentityRanker()
    )

    docs = retriever.invoke("q")

    assert len(docs) == 2
    contents = [d.page_content for d in docs]
    assert contents == ["parent one", "parent two"]  # first-seen order preserved
    # Only the unique parent ids were queried.
    _, params = mock_vectorstore._mock_cursor.execute.call_args[0]
    assert sorted(params[0]) == [1, 2]


def test_parent_carries_document_source_metadata(mock_vectorstore):
    mock_vectorstore.hybrid_search.return_value = [_child("c", parent_id=5)]
    mock_vectorstore._mock_cursor.fetchall.return_value = [
        _parent_row(
            5,
            "parent text",
            resource_hash="abc123",
            display_name="Account Guide",
            source_type="links",
            url="https://docs.example/account",
        )
    ]
    retriever = LlamaIndexHierarchicalRetriever(
        vectorstore=mock_vectorstore, reranker=_IdentityRanker()
    )

    doc = retriever.invoke("q")[0]

    assert doc.metadata["resource_hash"] == "abc123"
    assert doc.metadata["display_name"] == "Account Guide"
    assert doc.metadata["source_type"] == "links"
    assert doc.metadata["url"] == "https://docs.example/account"


def test_candidate_without_parent_id_passes_through(mock_vectorstore):
    """Legacy rows lacking parent_id are returned as their own child document."""
    legacy = (Document(page_content="legacy chunk", metadata={}), 0.5)
    mock_vectorstore.hybrid_search.return_value = [
        _child("c1", parent_id=1),
        legacy,
    ]
    mock_vectorstore._mock_cursor.fetchall.return_value = [_parent_row(1, "parent one")]
    retriever = LlamaIndexHierarchicalRetriever(
        vectorstore=mock_vectorstore, reranker=_IdentityRanker()
    )

    docs = retriever.invoke("q")

    assert [d.page_content for d in docs] == ["parent one", "legacy chunk"]


def test_missing_parent_row_is_skipped(mock_vectorstore):
    """If a referenced parent id has no row, it is dropped, not errored."""
    mock_vectorstore.hybrid_search.return_value = [
        _child("c1", parent_id=1),
        _child("c2", parent_id=99),
    ]
    mock_vectorstore._mock_cursor.fetchall.return_value = [_parent_row(1, "parent one")]
    retriever = LlamaIndexHierarchicalRetriever(
        vectorstore=mock_vectorstore, reranker=_IdentityRanker()
    )

    docs = retriever.invoke("q")

    assert [d.page_content for d in docs] == ["parent one"]
    mock_vectorstore._close_connection.assert_called_once()


# =============================================================================
# Cross-encoder rerank + top-N truncation (task 3.2)
# =============================================================================


def test_rerank_passes_query_and_child_text_to_ranker(mock_vectorstore):
    """Each child's page_content is handed to the cross-encoder with the query."""
    mock_vectorstore.hybrid_search.return_value = [
        _child("child alpha", parent_id=1),
        _child("child beta", parent_id=2),
    ]
    mock_vectorstore._mock_cursor.fetchall.return_value = [
        _parent_row(1, "parent one"),
        _parent_row(2, "parent two"),
    ]
    ranker = MagicMock()
    ranker.rerank.return_value = [
        {"id": 0, "score": 9.0},
        {"id": 1, "score": 1.0},
    ]
    retriever = LlamaIndexHierarchicalRetriever(
        vectorstore=mock_vectorstore, reranker=ranker
    )

    retriever.invoke("how do I reset my password")

    ranker.rerank.assert_called_once()
    request = ranker.rerank.call_args[0][0]
    assert request.query == "how do I reset my password"
    assert [p["text"] for p in request.passages] == ["child alpha", "child beta"]
    assert [p["id"] for p in request.passages] == [0, 1]


def test_rerank_reorders_parents(mock_vectorstore):
    """Parent order follows the cross-encoder ranking, not hybrid order."""
    mock_vectorstore.hybrid_search.return_value = [
        _child("c1", parent_id=1, score=0.9),
        _child("c2", parent_id=2, score=0.8),
        _child("c3", parent_id=3, score=0.7),
    ]
    mock_vectorstore._mock_cursor.fetchall.return_value = [
        _parent_row(1, "parent one"),
        _parent_row(2, "parent two"),
        _parent_row(3, "parent three"),
    ]
    # _ReverseRanker makes the last hybrid candidate the top-ranked one.
    retriever = LlamaIndexHierarchicalRetriever(
        vectorstore=mock_vectorstore, reranker=_ReverseRanker()
    )

    docs = retriever.invoke("q")

    assert [d.page_content for d in docs] == [
        "parent three",
        "parent two",
        "parent one",
    ]


def test_rerank_truncates_to_num_documents_to_retrieve(mock_vectorstore):
    """At most num_documents_to_retrieve parents are returned after rerank."""
    children = [_child(f"c{i}", parent_id=i) for i in range(8)]
    mock_vectorstore.hybrid_search.return_value = children
    mock_vectorstore._mock_cursor.fetchall.return_value = [
        _parent_row(i, f"parent {i}") for i in range(8)
    ]
    retriever = LlamaIndexHierarchicalRetriever(
        vectorstore=mock_vectorstore,
        num_documents_to_retrieve=3,
        reranker=_IdentityRanker(),
    )

    docs = retriever.invoke("q")

    assert len(docs) == 3
    assert [d.page_content for d in docs] == ["parent 0", "parent 1", "parent 2"]


def test_rerank_dedupes_parents_before_truncating(mock_vectorstore):
    """Duplicate parents collapse to one before the top-N cut is applied."""
    mock_vectorstore.hybrid_search.return_value = [
        _child("c1", parent_id=1),
        _child("c2", parent_id=1),
        _child("c3", parent_id=2),
        _child("c4", parent_id=3),
    ]
    mock_vectorstore._mock_cursor.fetchall.return_value = [
        _parent_row(1, "parent one"),
        _parent_row(2, "parent two"),
        _parent_row(3, "parent three"),
    ]
    retriever = LlamaIndexHierarchicalRetriever(
        vectorstore=mock_vectorstore,
        num_documents_to_retrieve=2,
        reranker=_IdentityRanker(),
    )

    docs = retriever.invoke("q")

    # Two distinct parents from the first three reranked children, then stop.
    assert [d.page_content for d in docs] == ["parent one", "parent two"]


def test_rerank_score_attached_to_results(mock_vectorstore):
    """Each returned parent carries its cross-encoder score in metadata."""
    mock_vectorstore.hybrid_search.return_value = [_child("c1", parent_id=1)]
    mock_vectorstore._mock_cursor.fetchall.return_value = [_parent_row(1, "parent one")]
    ranker = MagicMock()
    ranker.rerank.return_value = [{"id": 0, "score": 4.2}]
    retriever = LlamaIndexHierarchicalRetriever(
        vectorstore=mock_vectorstore, reranker=ranker
    )

    doc = retriever.invoke("q")[0]

    assert doc.metadata["rerank_score"] == 4.2


def test_default_reranker_model_constant(mock_vectorstore):
    """Retriever defaults to the configured FlashRank model."""
    from src.data_manager.vectorstore.retrievers.hierarchical_retriever import (
        DEFAULT_RERANKER_MODEL,
    )

    assert DEFAULT_RERANKER_MODEL == "ms-marco-MiniLM-L-12-v2"
    retriever = LlamaIndexHierarchicalRetriever(vectorstore=mock_vectorstore)
    assert retriever.reranker_model == DEFAULT_RERANKER_MODEL
