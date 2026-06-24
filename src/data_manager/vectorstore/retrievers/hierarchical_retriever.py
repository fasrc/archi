"""
Hierarchical retriever: hybrid child-candidate generation + parent expansion.

This retriever generates a pool of small embedded *child* candidates via the
existing Postgres-native hybrid (BM25 + vector) search, reranks that pool with a
CPU cross-encoder (FlashRank), then maps each child hit back to its larger
*parent* context node in ``document_parent_nodes`` (linked via
``metadata.parent_id``), deduplicating parents so multiple child hits under one
parent collapse to a single context document, and returns the top-N parents.

This module implements task 3.1 (candidate generation, parent lookup, parent
dedupe) and task 3.2 (FlashRank cross-encoder rerank + top-N truncation).
Configuration gating / fallback to ``HybridRetriever`` lands in 3.3.
"""

import json
import threading
from typing import Any, Dict, List, Optional, Tuple

import psycopg2.extras
from langchain_core.callbacks.manager import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores.base import VectorStore

from src.data_manager.vectorstore.schema import ensure_hierarchical_schema
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Default FlashRank ONNX cross-encoder (CPU). Mirrors the config default in
# ``base-config.yaml`` (data_manager.retrievers.hierarchical_rerank.reranker.model).
DEFAULT_RERANKER_MODEL = "ms-marco-MiniLM-L-12-v2"

# Module-level cache of FlashRank rankers keyed by model name. Building a Ranker
# downloads/loads the ONNX model, so we share one instance per model across
# retriever instances rather than rebuilding it per query.
_RANKER_CACHE: Dict[str, Any] = {}
# Guards _RANKER_CACHE. A Flask chat service can call _get_cached_ranker from
# multiple request threads concurrently, and building a Ranker downloads/loads the
# ONNX model — double-checked locking ensures it loads once per model name rather
# than racing into duplicate loads or a partially-initialized entry.
_RANKER_CACHE_LOCK = threading.Lock()


def _get_cached_ranker(model_name: str) -> Any:  # pragma: no cover - loads model
    """Build (once) and return a FlashRank ``Ranker`` for ``model_name``."""
    ranker = _RANKER_CACHE.get(model_name)
    if ranker is None:
        with _RANKER_CACHE_LOCK:
            ranker = _RANKER_CACHE.get(model_name)
            if ranker is None:
                from flashrank import Ranker

                ranker = Ranker(model_name=model_name)
                _RANKER_CACHE[model_name] = ranker
    return ranker


class LlamaIndexHierarchicalRetriever(BaseRetriever):
    """
    Hierarchical retriever returning parent-context documents.

    Pipeline:
    1. Generate ``candidate_pool_size`` (~20) child candidates via the
       vectorstore's native ``hybrid_search`` (BM25 + vector).
    2. Rerank the child candidate pool with a CPU cross-encoder (FlashRank),
       scoring each child against the query.
    3. In reranked order, map each child hit to its parent node by
       ``metadata.parent_id`` and fetch the parent rows from
       ``document_parent_nodes``, deduplicating parents (best-ranked first).
    4. Return the top ``num_documents_to_retrieve`` (~5) parent nodes as
       LangChain ``Document`` objects, each carrying its cross-encoder
       ``rerank_score`` in metadata.
    """

    vectorstore: VectorStore
    candidate_pool_size: int = 20
    num_documents_to_retrieve: int = 5
    bm25_weight: float = 0.5
    semantic_weight: float = 0.5
    reranker_model: str = DEFAULT_RERANKER_MODEL
    # Injectable FlashRank ranker; built lazily from ``reranker_model`` when None
    # (kept out of construction so unit tests can supply a stub without loading
    # the ONNX model).
    reranker: Optional[Any] = None

    def __init__(
        self,
        vectorstore: VectorStore,
        candidate_pool_size: int = 20,
        num_documents_to_retrieve: int = 5,
        bm25_weight: float = 0.5,
        semantic_weight: float = 0.5,
        reranker_model: str = DEFAULT_RERANKER_MODEL,
        reranker: Optional[Any] = None,
        **kwargs,
    ):
        super().__init__(
            vectorstore=vectorstore,
            candidate_pool_size=candidate_pool_size,
            num_documents_to_retrieve=num_documents_to_retrieve,
            bm25_weight=bm25_weight,
            semantic_weight=semantic_weight,
            reranker_model=reranker_model,
            reranker=reranker,
            **kwargs,
        )

        # The retriever calls hybrid_search() for candidate generation and
        # _get_connection()/_close_connection() for parent lookup — all
        # PostgresVectorStore methods. Validate them at construction so an
        # incompatible store fails fast here rather than with an AttributeError
        # mid-query.
        required = ("hybrid_search", "_get_connection", "_close_connection")
        missing = [name for name in required if not hasattr(vectorstore, name)]
        if missing:
            raise ValueError(
                "LlamaIndexHierarchicalRetriever requires a Postgres-style "
                "vectorstore exposing hybrid_search(), _get_connection(), and "
                "_close_connection() (e.g. PostgresVectorStore); missing: "
                + ", ".join(missing)
            )

    def _generate_candidates(self, query: str) -> List[Tuple[Document, float]]:
        """Fetch the child-candidate pool via the native hybrid search."""
        logger.debug(
            "Hierarchical candidate generation: k=%d, semantic_weight=%.2f, bm25_weight=%.2f",
            self.candidate_pool_size,
            self.semantic_weight,
            self.bm25_weight,
        )
        return self.vectorstore.hybrid_search(
            query=query,
            k=self.candidate_pool_size,
            semantic_weight=self.semantic_weight,
            bm25_weight=self.bm25_weight,
        )

    def _fetch_parents(self, parent_ids: List[Any]) -> Dict[Any, Document]:
        """
        Load parent context nodes from ``document_parent_nodes`` by id.

        Returns a mapping ``parent_id -> Document`` whose ``page_content`` is the
        parent text and whose metadata merges the stored parent metadata with the
        owning document's source fields (so sources populate downstream).
        """
        if not parent_ids:
            return {}

        conn = self.vectorstore._get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                # init.sql only runs on a fresh Postgres volume; ensure the
                # parent-node table/index exist before reading so retrieval on an
                # upgraded deployment over a pre-existing volume does not fail
                # with an undefined-table error. Idempotent and a no-op once the
                # table exists.
                ensure_hierarchical_schema(cursor)
                cursor.execute(
                    """
                    SELECT
                        p.id,
                        p.parent_text,
                        p.metadata,
                        p.document_id,
                        d.resource_hash,
                        d.display_name,
                        d.source_type,
                        d.url
                    FROM document_parent_nodes p
                    LEFT JOIN documents d ON p.document_id = d.id
                    WHERE p.id = ANY(%s)
                    """,
                    (list(parent_ids),),
                )
                rows = cursor.fetchall()
        finally:
            self.vectorstore._close_connection(conn)

        parents: Dict[Any, Document] = {}
        for row in rows:
            metadata = row["metadata"] or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            else:
                metadata = dict(metadata)

            metadata["parent_id"] = row["id"]
            if row.get("resource_hash"):
                metadata["resource_hash"] = row["resource_hash"]
            if row.get("display_name"):
                metadata["display_name"] = row["display_name"]
            if row.get("source_type"):
                metadata["source_type"] = row["source_type"]
            if row.get("url"):
                metadata["url"] = row["url"]

            parents[row["id"]] = Document(
                page_content=row["parent_text"],
                metadata=metadata,
            )
        return parents

    def _rerank(
        self, query: str, candidates: List[Tuple[Document, float]]
    ) -> List[Tuple[int, float]]:
        """
        Rerank the child candidate pool with the FlashRank cross-encoder.

        Returns a list of ``(candidate_index, rerank_score)`` tuples ordered by
        descending cross-encoder score. The indices refer back into
        ``candidates`` so callers can recover the original child documents.
        """
        from flashrank import RerankRequest

        ranker = self.reranker
        if ranker is None:
            ranker = _get_cached_ranker(self.reranker_model)

        passages = [
            {"id": index, "text": doc.page_content, "meta": {}}
            for index, (doc, _score) in enumerate(candidates)
        ]
        ranked = ranker.rerank(RerankRequest(query=query, passages=passages))
        return [(int(item["id"]), float(item["score"])) for item in ranked]

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: Optional[CallbackManagerForRetrieverRun] = None,
    ) -> List[Document]:
        """Return the top reranked, deduplicated parent-context documents."""
        candidates = self._generate_candidates(query)
        if not candidates:
            return []

        # Cross-encoder reranks the child pool; we then walk children in
        # reranked order. Mapping child -> parent and deduplicating in this order
        # means each parent inherits its best-ranked child's position/score.
        # Candidates lacking a parent_id (e.g. legacy non-hierarchical rows) pass
        # through as their own child document so recall is not silently dropped.
        ranked = self._rerank(query, candidates)

        ordered: List[Tuple[Tuple[str, Any], float]] = []
        seen: set = set()
        parent_ids: List[Any] = []
        passthrough: Dict[Tuple[str, Any], Document] = {}

        for index, score in ranked:
            doc, _hybrid_score = candidates[index]
            parent_id = doc.metadata.get("parent_id")
            if parent_id is not None:
                key = ("parent", parent_id)
                if key in seen:
                    continue
                seen.add(key)
                ordered.append((key, score))
                parent_ids.append(parent_id)
            else:
                key = ("child", index)
                seen.add(key)
                ordered.append((key, score))
                passthrough[key] = doc

        parents = self._fetch_parents(parent_ids)

        results: List[Document] = []
        for (kind, ref), score in ordered:
            if kind == "parent":
                doc = parents.get(ref)
                if doc is None:
                    continue
            else:
                doc = passthrough[(kind, ref)]
            doc.metadata["rerank_score"] = score
            results.append(doc)
            if len(results) >= self.num_documents_to_retrieve:
                break

        logger.debug(
            "Hierarchical retrieval: %d candidates -> %d parent documents "
            "(top %d after rerank)",
            len(candidates),
            len(results),
            self.num_documents_to_retrieve,
        )
        return results
