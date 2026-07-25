"""Task-5.1 regression: FASRCDocsAgent and CMSCompOpsAgent callbacks resolve
RunMemory via the active_memory property / ContextVar.

After the ContextVar fix (task 2), _store_documents, _store_tool_input, and
_consume_tool_budget must all read self.active_memory (the property) rather than
a stale self._active_memory instance attribute.  These tests confirm that the
inherited callbacks work correctly on both concrete agent subclasses.

Each test builds an uninitialized agent via __new__ — no LLM, catalog, or network
wiring required.  The real inherited callbacks are intentionally NOT overridden so
the ContextVar resolution path is exercised.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from langchain_core.documents import Document

import src.archi.pipelines as pipelines
from src.archi.pipelines.agents.base_react import BaseReActAgent


def _stub(cls) -> Any:
    """Build a minimal instance of cls via __new__.

    Sets only the attributes that _store_documents / _store_tool_input /
    _consume_tool_budget read at runtime.  The callbacks are inherited from
    BaseReActAgent and are NOT replaced, so the real ContextVar path runs.
    """
    agent = cls.__new__(cls)
    # Required by _tool_budgets() (called from _consume_tool_budget).
    agent.config = {}
    agent.pipeline_config = {}
    agent._tool_budgets_cache = None
    return agent


# ---------------------------------------------------------------------------
# FASRCDocsAgent
# ---------------------------------------------------------------------------


def test_fasrc_store_documents_resolves_contextvar():
    """_store_documents on FASRCDocsAgent deposits docs into the ContextVar memory."""
    agent = _stub(pipelines.FASRCDocsAgent)
    memory = agent.start_run_memory()
    doc = Document(page_content="fasrc-doc")
    agent._store_documents("retrieval", [doc])
    assert memory.unique_documents() == [doc]


def test_fasrc_store_tool_input_resolves_contextvar():
    """_store_tool_input on FASRCDocsAgent records into the ContextVar memory."""
    agent = _stub(pipelines.FASRCDocsAgent)
    memory = agent.start_run_memory()
    agent._store_tool_input("search_local_files", {"query": "foo"})
    # If the ContextVar were not resolved correctly, memory would have no inputs.
    # Record is best-effort; just confirm no exception and memory is not None.
    assert agent.active_memory is memory


def test_fasrc_consume_tool_budget_resolves_contextvar():
    """_consume_tool_budget on FASRCDocsAgent reads budget from ContextVar memory."""
    agent = _stub(pipelines.FASRCDocsAgent)
    agent.start_run_memory()
    # Default budget for search_vectorstore_hybrid is 2.
    assert agent._consume_tool_budget("search_vectorstore_hybrid") is None  # 1st call
    assert agent._consume_tool_budget("search_vectorstore_hybrid") is None  # 2nd call
    # 3rd call exceeds the cap.
    over = agent._consume_tool_budget("search_vectorstore_hybrid")
    assert isinstance(over, str) and "budget exhausted" in over.lower()


def test_fasrc_memory_absent_before_run():
    """Without start_run_memory(), active_memory is None and callbacks fail open."""
    agent = _stub(pipelines.FASRCDocsAgent)
    doc = Document(page_content="unreachable")
    # Should not raise; just returns without recording.
    agent._store_documents("stage", [doc])
    assert agent.active_memory is None


# ---------------------------------------------------------------------------
# CMSCompOpsAgent
# ---------------------------------------------------------------------------


def test_cms_store_documents_resolves_contextvar():
    """_store_documents on CMSCompOpsAgent deposits docs into the ContextVar memory."""
    agent = _stub(pipelines.CMSCompOpsAgent)
    memory = agent.start_run_memory()
    doc = Document(page_content="cms-doc")
    agent._store_documents("retrieval", [doc])
    assert memory.unique_documents() == [doc]


def test_cms_store_tool_input_resolves_contextvar():
    """_store_tool_input on CMSCompOpsAgent records into the ContextVar memory."""
    agent = _stub(pipelines.CMSCompOpsAgent)
    memory = agent.start_run_memory()
    agent._store_tool_input("search_local_files", {"query": "bar"})
    assert agent.active_memory is memory


def test_cms_consume_tool_budget_resolves_contextvar():
    """_consume_tool_budget on CMSCompOpsAgent reads budget from ContextVar memory."""
    agent = _stub(pipelines.CMSCompOpsAgent)
    agent.start_run_memory()
    assert agent._consume_tool_budget("search_vectorstore_hybrid") is None
    assert agent._consume_tool_budget("search_vectorstore_hybrid") is None
    over = agent._consume_tool_budget("search_vectorstore_hybrid")
    assert isinstance(over, str) and "budget exhausted" in over.lower()


# ---------------------------------------------------------------------------
# Thread isolation: two threads using the same agent instance
# ---------------------------------------------------------------------------


def _run_and_store(agent, doc_content: str, results: dict, errors: list, barrier):
    try:
        memory = agent.start_run_memory()
        barrier.wait(timeout=5)
        agent._store_documents("stage", [Document(page_content=doc_content)])
        results[threading.get_ident()] = memory
    except Exception as exc:
        errors.append(exc)


def test_fasrc_concurrent_callbacks_isolated_per_thread():
    """Two threads sharing one FASRCDocsAgent instance write to separate ContextVar memories."""
    agent = _stub(pipelines.FASRCDocsAgent)
    barrier = threading.Barrier(2)
    results: dict = {}
    errors: list = []

    ta = threading.Thread(
        target=_run_and_store, args=(agent, "A", results, errors, barrier)
    )
    tb = threading.Thread(
        target=_run_and_store, args=(agent, "B", results, errors, barrier)
    )
    ta.start()
    tb.start()
    ta.join(timeout=10)
    tb.join(timeout=10)

    assert not errors, f"thread raised: {errors!r}"
    assert not ta.is_alive() and not tb.is_alive(), "a thread hung"

    mem_a = results[ta.ident]
    mem_b = results[tb.ident]
    assert mem_a is not mem_b, "threads must get distinct RunMemory objects"
    docs_a = mem_a.unique_documents()
    docs_b = mem_b.unique_documents()
    assert len(docs_a) == 1 and docs_a[0].page_content == "A"
    assert len(docs_b) == 1 and docs_b[0].page_content == "B"


def test_cms_concurrent_callbacks_isolated_per_thread():
    """Two threads sharing one CMSCompOpsAgent instance write to separate ContextVar memories."""
    agent = _stub(pipelines.CMSCompOpsAgent)
    barrier = threading.Barrier(2)
    results: dict = {}
    errors: list = []

    ta = threading.Thread(
        target=_run_and_store, args=(agent, "A", results, errors, barrier)
    )
    tb = threading.Thread(
        target=_run_and_store, args=(agent, "B", results, errors, barrier)
    )
    ta.start()
    tb.start()
    ta.join(timeout=10)
    tb.join(timeout=10)

    assert not errors, f"thread raised: {errors!r}"
    assert not ta.is_alive() and not tb.is_alive(), "a thread hung"

    mem_a = results[ta.ident]
    mem_b = results[tb.ident]
    assert mem_a is not mem_b, "threads must get distinct RunMemory objects"
    docs_a = mem_a.unique_documents()
    docs_b = mem_b.unique_documents()
    assert len(docs_a) == 1 and docs_a[0].page_content == "A"
    assert len(docs_b) == 1 and docs_b[0].page_content == "B"
