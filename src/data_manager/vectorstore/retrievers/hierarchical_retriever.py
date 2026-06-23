"""
Hierarchical retriever: hybrid child-candidate generation + parent expansion.

This retriever generates a pool of small embedded *child* candidates via the
existing Postgres-native hybrid (BM25 + vector) search, then maps each child hit
back to its larger *parent* context node in ``document_parent_nodes`` (linked via
``metadata.parent_id``), deduplicating parents so multiple child hits under one
parent collapse to a single context document.

This module implements task 3.1 (candidate generation, parent lookup, parent
dedupe). The cross-encoder rerank step and top-N truncation are layered on top in
task 3.2; configuration gating / fallback to ``HybridRetriever`` lands in 3.3.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

import psycopg2.extras
from langchain_core.callbacks.manager import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores.base import VectorStore

from src.utils.logging import get_logger

logger = get_logger(__name__)


class LlamaIndexHierarchicalRetriever(BaseRetriever):
    """
    Hierarchical retriever returning parent-context documents.

    Pipeline:
    1. Generate ``candidate_pool_size`` (~20) child candidates via the
       vectorstore's native ``hybrid_search`` (BM25 + vector).
    2. Map each child hit to its parent node by ``metadata.parent_id`` and fetch
       the parent rows from ``document_parent_nodes``.
    3. Deduplicate parents (first-seen order preserved) and return them as
       LangChain ``Document`` objects.

    The reranking / top-N narrowing is added in a later task; for now the
    retriever returns the full deduplicated parent set in candidate order.
    """

    vectorstore: VectorStore
    candidate_pool_size: int = 20
    bm25_weight: float = 0.5
    semantic_weight: float = 0.5

    def __init__(
        self,
        vectorstore: VectorStore,
        candidate_pool_size: int = 20,
        bm25_weight: float = 0.5,
        semantic_weight: float = 0.5,
        **kwargs,
    ):
        super().__init__(
            vectorstore=vectorstore,
            candidate_pool_size=candidate_pool_size,
            bm25_weight=bm25_weight,
            semantic_weight=semantic_weight,
            **kwargs,
        )

        if not hasattr(vectorstore, "hybrid_search"):
            raise ValueError(
                "LlamaIndexHierarchicalRetriever requires a vectorstore exposing "
                "hybrid_search() (e.g. PostgresVectorStore)."
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

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: Optional[CallbackManagerForRetrieverRun] = None,
    ) -> List[Document]:
        """Return deduplicated parent-context documents for the query."""
        candidates = self._generate_candidates(query)
        if not candidates:
            return []

        # Preserve first-seen (hybrid-score) ordering while collecting the unique
        # parent ids referenced by the child candidates. Candidates lacking a
        # parent_id (e.g. legacy non-hierarchical rows) pass through as their own
        # child document so recall is not silently dropped.
        ordered_keys: List[Tuple[str, Any]] = []
        seen: set = set()
        parent_ids: List[Any] = []
        passthrough: Dict[Tuple[str, Any], Document] = {}

        for position, (doc, _score) in enumerate(candidates):
            parent_id = doc.metadata.get("parent_id")
            if parent_id is not None:
                key = ("parent", parent_id)
                if key not in seen:
                    seen.add(key)
                    ordered_keys.append(key)
                    parent_ids.append(parent_id)
            else:
                key = ("child", position)
                seen.add(key)
                ordered_keys.append(key)
                passthrough[key] = doc

        parents = self._fetch_parents(parent_ids)

        results: List[Document] = []
        for kind, ref in ordered_keys:
            if kind == "parent":
                parent_doc = parents.get(ref)
                if parent_doc is not None:
                    results.append(parent_doc)
            else:
                results.append(passthrough[(kind, ref)])

        logger.debug(
            "Hierarchical retrieval: %d candidates -> %d parent documents",
            len(candidates),
            len(results),
        )
        return results
