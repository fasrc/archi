"""
Configuration-gated retriever selection.

``build_vector_retriever`` is the single config seam (task 3.3) for choosing which
vectorstore retriever to construct. When
``data_manager.retrievers.hierarchical_rerank.enabled`` is true it builds the
``LlamaIndexHierarchicalRetriever`` (hybrid child candidates -> cross-encoder
rerank -> top-N parent context); otherwise it falls back to the existing
``HybridRetriever``. The agent wiring (task 4.1) calls this so feature-flag logic
lives here rather than inside the agent.
"""

from typing import Any, Dict, Optional

from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores.base import VectorStore

from src.utils.logging import get_logger

from .hierarchical_retriever import (
    DEFAULT_RERANKER_MODEL,
    LlamaIndexHierarchicalRetriever,
)
from .hybrid_retriever import HybridRetriever

logger = get_logger(__name__)


def build_vector_retriever(
    vectorstore: VectorStore,
    retrievers_cfg: Optional[Dict[str, Any]] = None,
) -> BaseRetriever:
    """
    Return the configured vectorstore retriever.

    Args:
        vectorstore: backing vectorstore (e.g. ``PostgresVectorStore``).
        retrievers_cfg: the ``data_manager.retrievers`` config section.

    Returns:
        ``LlamaIndexHierarchicalRetriever`` when
        ``hierarchical_rerank.enabled`` is true, otherwise ``HybridRetriever``.

    Candidate generation in both retrievers reuses ``hybrid_search``, so the
    BM25/semantic weights are read from the ``hybrid_retriever`` config section in
    both cases.
    """
    retrievers_cfg = retrievers_cfg or {}
    hybrid_cfg = retrievers_cfg.get("hybrid_retriever", {}) or {}
    bm25_weight = hybrid_cfg.get("bm25_weight", 0.6)
    semantic_weight = hybrid_cfg.get("semantic_weight", 0.4)

    hierarchical_cfg = retrievers_cfg.get("hierarchical_rerank", {}) or {}
    if hierarchical_cfg.get("enabled", False):
        reranker_cfg = hierarchical_cfg.get("reranker", {}) or {}
        retriever = LlamaIndexHierarchicalRetriever(
            vectorstore=vectorstore,
            candidate_pool_size=hierarchical_cfg.get("candidate_pool_size", 20),
            num_documents_to_retrieve=hierarchical_cfg.get(
                "num_documents_to_retrieve", 5
            ),
            bm25_weight=bm25_weight,
            semantic_weight=semantic_weight,
            reranker_model=reranker_cfg.get("model", DEFAULT_RERANKER_MODEL),
        )
        logger.info(
            "hierarchical_rerank enabled: using LlamaIndexHierarchicalRetriever "
            "(candidate_pool_size=%d, top_n=%d, reranker=%s)",
            retriever.candidate_pool_size,
            retriever.num_documents_to_retrieve,
            retriever.reranker_model,
        )
        return retriever

    k = hybrid_cfg.get("num_documents_to_retrieve", 5)
    logger.info(
        "hierarchical_rerank disabled: falling back to HybridRetriever (k=%d)", k
    )
    return HybridRetriever(
        vectorstore=vectorstore,
        k=k,
        bm25_weight=bm25_weight,
        semantic_weight=semantic_weight,
    )
