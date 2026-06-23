"""
Unit tests for ``build_vector_retriever`` (task 3.3).

Covers the config gate: ``hierarchical_rerank.enabled`` selects the hierarchical
cross-encoder retriever; otherwise (disabled, missing, or empty config) retrieval
falls back to ``HybridRetriever`` with the existing hybrid weights. Also asserts
the retriever and factory are exported from the package.
"""

from langchain_core.vectorstores import VectorStore

from src.data_manager.vectorstore.retrievers import (
    HybridRetriever,
    LlamaIndexHierarchicalRetriever,
    build_vector_retriever,
)
from src.data_manager.vectorstore.retrievers.hierarchical_retriever import (
    DEFAULT_RERANKER_MODEL,
)


class _FakeStore(VectorStore):
    """Minimal real VectorStore exposing the PostgresVectorStore surface the
    hierarchical retriever requires (hybrid_search + connection accessors)."""

    def hybrid_search(
        self, query, k, semantic_weight, bm25_weight
    ):  # pragma: no cover - stub
        return []

    def _get_connection(self):  # pragma: no cover - stub (Postgres parity)
        return None

    def _close_connection(self, conn):  # pragma: no cover - stub (Postgres parity)
        return None

    def similarity_search(self, query, k=4, **kwargs):  # pragma: no cover - stub
        return []

    @classmethod
    def from_texts(
        cls, texts, embedding, metadatas=None, **kwargs
    ):  # pragma: no cover - stub
        return cls()


# =============================================================================
# Export surface
# =============================================================================


def test_package_exports_retriever_and_factory():
    from src.data_manager.vectorstore import retrievers

    assert "LlamaIndexHierarchicalRetriever" in retrievers.__all__
    assert "build_vector_retriever" in retrievers.__all__


# =============================================================================
# Fallback to HybridRetriever (feature disabled)
# =============================================================================


def test_disabled_falls_back_to_hybrid():
    cfg = {
        "hybrid_retriever": {
            "num_documents_to_retrieve": 7,
            "bm25_weight": 0.6,
            "semantic_weight": 0.4,
        },
        "hierarchical_rerank": {"enabled": False},
    }
    retriever = build_vector_retriever(_FakeStore(), cfg)

    assert isinstance(retriever, HybridRetriever)
    assert retriever.k == 7
    assert retriever.bm25_weight == 0.6
    assert retriever.semantic_weight == 0.4


def test_missing_hierarchical_config_falls_back_to_hybrid():
    retriever = build_vector_retriever(
        _FakeStore(), {"hybrid_retriever": {"num_documents_to_retrieve": 5}}
    )
    assert isinstance(retriever, HybridRetriever)
    assert retriever.k == 5


def test_empty_config_falls_back_to_hybrid_with_defaults():
    retriever = build_vector_retriever(_FakeStore(), {})
    assert isinstance(retriever, HybridRetriever)
    # Mirrors base-config defaults for the hybrid retriever.
    assert retriever.k == 5
    assert retriever.bm25_weight == 0.6
    assert retriever.semantic_weight == 0.4


def test_none_config_falls_back_to_hybrid():
    retriever = build_vector_retriever(_FakeStore(), None)
    assert isinstance(retriever, HybridRetriever)


# =============================================================================
# Hierarchical retriever (feature enabled)
# =============================================================================


def test_enabled_builds_hierarchical_retriever():
    cfg = {
        "hybrid_retriever": {"bm25_weight": 0.6, "semantic_weight": 0.4},
        "hierarchical_rerank": {
            "enabled": True,
            "candidate_pool_size": 25,
            "num_documents_to_retrieve": 4,
            "reranker": {"model": "ms-marco-TinyBERT-L-2-v2"},
        },
    }
    retriever = build_vector_retriever(_FakeStore(), cfg)

    assert isinstance(retriever, LlamaIndexHierarchicalRetriever)
    assert retriever.candidate_pool_size == 25
    assert retriever.num_documents_to_retrieve == 4
    assert retriever.reranker_model == "ms-marco-TinyBERT-L-2-v2"
    # Candidate-generation weights reuse the hybrid_retriever section.
    assert retriever.bm25_weight == 0.6
    assert retriever.semantic_weight == 0.4


def test_enabled_uses_default_pool_and_reranker_when_unspecified():
    cfg = {"hierarchical_rerank": {"enabled": True}}
    retriever = build_vector_retriever(_FakeStore(), cfg)

    assert isinstance(retriever, LlamaIndexHierarchicalRetriever)
    assert retriever.candidate_pool_size == 20
    assert retriever.num_documents_to_retrieve == 5
    assert retriever.reranker_model == DEFAULT_RERANKER_MODEL
