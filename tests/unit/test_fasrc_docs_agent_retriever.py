"""Unit tests for the agent-side retriever seam (task 5.5).

``FASRCDocsAgent._update_vector_retrievers`` delegates retriever selection to
``build_vector_retriever``. With the hierarchical-rerank feature disabled (or
simply unconfigured), the agent must fall back to the existing
``HybridRetriever`` while still wiring it into a tool named
``search_vectorstore_hybrid`` with an unchanged name/contract — the
drop-in-behind-the-tool-seam guarantee from the spec.

These exercise the real ``_update_vector_retrievers`` code path on an agent
shell built with ``object.__new__`` (so the heavy ``__init__`` / model loading
is skipped), mirroring ``test_forced_retrieval.py``.
"""

from langchain_core.vectorstores import VectorStore

from src.archi.pipelines.agents.fasrc_docs_agent import FASRCDocsAgent
from src.data_manager.vectorstore.retrievers import (
    HybridRetriever,
    LlamaIndexHierarchicalRetriever,
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


def _make_agent(retrievers_cfg):
    """Build a FASRCDocsAgent shell without running its heavy __init__."""
    agent = object.__new__(FASRCDocsAgent)
    agent.enable_vector_tools = True
    agent.dm_config = {"retrievers": retrievers_cfg}
    agent._store_documents = lambda *a, **k: None
    agent._store_tool_input = lambda *a, **k: None
    agent._consume_tool_budget = lambda *a, **k: None
    return agent


# =============================================================================
# Feature disabled -> fall back to HybridRetriever, tool name preserved
# =============================================================================


def test_disabled_falls_back_to_hybrid_retriever():
    agent = _make_agent(
        {
            "hybrid_retriever": {
                "num_documents_to_retrieve": 5,
                "bm25_weight": 0.6,
                "semantic_weight": 0.4,
            },
            "hierarchical_rerank": {"enabled": False},
        }
    )

    agent._update_vector_retrievers(_FakeStore())

    assert agent._vector_retrievers is not None
    assert len(agent._vector_retrievers) == 1
    assert isinstance(agent._vector_retrievers[0], HybridRetriever)
    assert not isinstance(agent._vector_retrievers[0], LlamaIndexHierarchicalRetriever)


def test_disabled_still_exposes_search_vectorstore_hybrid_tool():
    agent = _make_agent({"hierarchical_rerank": {"enabled": False}})

    agent._update_vector_retrievers(_FakeStore())

    assert agent._vector_tools is not None
    assert len(agent._vector_tools) == 1
    assert agent._vector_tools[0].name == "search_vectorstore_hybrid"


def test_missing_hierarchical_config_falls_back_to_hybrid():
    """No hierarchical_rerank section at all also falls back."""
    agent = _make_agent({"hybrid_retriever": {"num_documents_to_retrieve": 3}})

    agent._update_vector_retrievers(_FakeStore())

    assert isinstance(agent._vector_retrievers[0], HybridRetriever)
    assert agent._vector_tools[0].name == "search_vectorstore_hybrid"


def test_disabled_via_vector_tools_flag_clears_retrievers():
    """When vector tools are off entirely, no retriever/tool is built."""
    agent = _make_agent({"hierarchical_rerank": {"enabled": False}})
    agent.enable_vector_tools = False

    agent._update_vector_retrievers(_FakeStore())

    assert agent._vector_retrievers is None
    assert agent._vector_tools is None
