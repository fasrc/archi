"""
Unit tests for HybridRetriever threading of the filename/title boost weight.

These tests verify that HybridRetriever exposes a configurable ``filename_boost``
and forwards it to ``PostgresVectorStore.hybrid_search()`` alongside the existing
BM25/semantic weights, defaulting to ``0.0`` so prior behavior is preserved.
"""

from typing import List, Tuple
from unittest.mock import MagicMock

from langchain_core.documents import Document
from langchain_core.vectorstores.base import VectorStore

from src.data_manager.vectorstore.retrievers import HybridRetriever


class _BaseFakeVectorStore(VectorStore):
    """Minimal concrete VectorStore so pydantic validation accepts it."""

    def add_texts(self, texts, metadatas=None, **kwargs):  # pragma: no cover
        return []

    def similarity_search(self, query, k=4, **kwargs):  # pragma: no cover
        return []

    @classmethod
    def from_texts(cls, texts, embedding, metadatas=None, **kwargs):  # pragma: no cover
        return cls()


class _HybridVectorStore(_BaseFakeVectorStore):
    """VectorStore exposing hybrid_search() (Postgres-native path)."""

    def __init__(self):
        self.hybrid_search = MagicMock(
            return_value=[(Document(page_content="body"), 0.9)]
        )


class _SemanticOnlyVectorStore(_BaseFakeVectorStore):
    """VectorStore without hybrid_search() (semantic fallback path)."""

    def __init__(self):
        self.similarity_search_with_score = MagicMock(
            return_value=[(Document(page_content="body"), 0.5)]
        )


def test_filename_boost_defaults_to_zero():
    """Default filename_boost is 0.0 so semantic + BM25 behavior is preserved."""
    retriever = HybridRetriever(vectorstore=_HybridVectorStore(), k=5)

    assert retriever.filename_boost == 0.0


def test_filename_boost_stored_from_constructor():
    """An explicit filename_boost is stored on the retriever."""
    retriever = HybridRetriever(
        vectorstore=_HybridVectorStore(), k=5, filename_boost=0.4
    )

    assert retriever.filename_boost == 0.4


def test_filename_boost_threaded_into_hybrid_search():
    """filename_boost is forwarded to the vectorstore hybrid_search() call."""
    vs = _HybridVectorStore()
    retriever = HybridRetriever(
        vectorstore=vs,
        k=3,
        bm25_weight=0.3,
        semantic_weight=0.7,
        filename_boost=0.5,
    )

    results = retriever._get_relevant_documents("quarterly report", run_manager=None)

    assert len(results) == 1
    vs.hybrid_search.assert_called_once_with(
        query="quarterly report",
        k=3,
        semantic_weight=0.7,
        bm25_weight=0.3,
        filename_boost=0.5,
    )


def test_default_filename_boost_threaded_as_zero():
    """When unset, hybrid_search receives filename_boost=0.0."""
    vs = _HybridVectorStore()
    retriever = HybridRetriever(vectorstore=vs, k=5)

    retriever._get_relevant_documents("query", run_manager=None)

    _, kwargs = vs.hybrid_search.call_args
    assert kwargs["filename_boost"] == 0.0


def test_filename_boost_ignored_for_semantic_fallback():
    """A vectorstore without hybrid_search falls back to semantic-only search."""
    vs = _SemanticOnlyVectorStore()
    retriever = HybridRetriever(vectorstore=vs, k=4, filename_boost=0.5)

    results: List[Tuple[Document, float]] = retriever._get_relevant_documents(
        "query", run_manager=None
    )

    assert len(results) == 1
    vs.similarity_search_with_score.assert_called_once_with("query", k=4)
