"""Regression test for the shared _active_memory race (issue #123).

Two concurrent DEFAULT (no-override) requests run through the same shared
BaseReActAgent-derived pipeline instance.  Each calls a stub document-retrieving
static tool.  Each request's RunMemory must contain ONLY its own documents —
no cross-attribution from the other request.

Task 1: written FIRST (red-only phase) so the assertions fail against the
current shared-attribute implementation, establishing the RED baseline.  The
green phase (task 2) makes it pass by moving active_memory onto a
contextvars.ContextVar.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from src.archi.pipelines.agents.base_react import BaseReActAgent


class _FakeGraph:
    """Stub compiled LangGraph agent for concurrency testing.

    Waits at a barrier so both concurrent threads have committed their
    ``start_run_memory()`` call before either's tool fires, then invokes the
    pipeline's first cached static tool with the request-specific query
    extracted from the agent input messages.  Yields a single AIMessage so
    the outer ``stream()`` loop runs to completion.
    """

    def __init__(
        self, pipeline: "_MemoryRaceAgent", barrier: threading.Barrier
    ) -> None:
        self._pipeline = pipeline
        self._barrier = barrier

    def stream(self, inputs: Dict[str, Any], stream_mode=None, config=None):
        messages = inputs.get("messages", [])
        query = "default"
        if messages:
            content = getattr(messages[-1], "content", "")
            if isinstance(content, str) and content:
                query = content

        # Both threads have called _prepare_agent_inputs() (and thus
        # start_run_memory()) before either reaches this barrier.  The barrier
        # guarantees that whichever thread arrives second has already overwritten
        # self._active_memory, exposing the race to whichever tool fires next.
        self._barrier.wait(timeout=10)

        # Invoke the first cached static tool with this request's unique query.
        active_tools = list(self._pipeline._active_tools)
        if active_tools:
            active_tools[0](query=query)

        yield [AIMessage(content=f"answer:{query}")]


class _MemoryRaceAgent(BaseReActAgent):
    """Minimal BaseReActAgent subclass for concurrent-memory-isolation testing.

    Bypasses the real ``__init__`` (LLM/prompt init) and overrides
    ``_create_agent`` / ``_build_static_tools`` so no network or provider is
    needed.  Instruments ``start_run_memory`` to capture the per-thread
    RunMemory reference so post-run assertions can verify isolation.
    """

    def __init__(self, graph: _FakeGraph) -> None:
        # All attributes that BaseReActAgent.__init__ would normally set.
        self.config = {}
        self.archi_config = {}
        self.dm_config = {}
        self.pipeline_config = {}
        self.agent_spec = None
        self.default_provider = None
        self.default_model = None
        self.selected_tool_names = ["fetch_doc"]
        self._active_memory = None
        self._tool_budgets_cache = None
        self._static_tools = None
        self._mcp_tools = None
        self._mcp_lock = threading.Lock()
        self._active_tools = []
        self._static_middleware = None
        self._active_middleware = []
        self.agent = graph
        self.agent_llm = object()
        self.agent_prompt = "test"
        self.mcp_client = None
        self.llms = {}
        self.prompts = {}

        # Memory capture: populated by the instrumented start_run_memory.
        self._captured_memories: Dict[int, Any] = {}
        self._capture_lock = threading.Lock()

    def start_run_memory(self):
        """Instrument to capture each thread's RunMemory reference."""
        mem = super().start_run_memory()
        with self._capture_lock:
            self._captured_memories[threading.get_ident()] = mem
        return mem

    def _create_agent(self, tools, middleware):
        # Return the pre-wired fake graph; do not build a real LangGraph agent.
        return self.agent

    def _build_static_tools(self):
        def fetch_doc(query="q"):
            """Stub retrieval tool — records a document labelled by the query."""
            self._store_documents("fetch", [Document(page_content=query)])
            return f"doc:{query}"

        return [fetch_doc]

    def _build_static_middleware(self):
        return []


def _run_stream(agent: _MemoryRaceAgent, query: str, errors: List[Exception]) -> None:
    """Drain the stream() iterator for one request; capture any exception."""
    try:
        for _ in agent.stream(history=[("human", query)]):
            pass
    except Exception as exc:
        errors.append(exc)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "RED baseline: self._active_memory is a shared instance attribute. "
        "The second start_run_memory() overwrites the first, so both tool "
        "callbacks write into the same RunMemory.  Passes once task 2 moves "
        "active_memory onto a contextvars.ContextVar."
    ),
)
def test_concurrent_default_requests_memory_isolation():
    """RED: two concurrent default requests must keep isolated RunMemory.

    With the current shared ``self._active_memory`` implementation the second
    call to ``start_run_memory()`` overwrites the first, so both tool callbacks
    write into the same RunMemory object.  This test therefore FAILS with the
    current implementation and will pass only once active_memory is moved to a
    ``contextvars.ContextVar`` (task 2).

    The threading.Barrier guarantees that both threads have called
    ``start_run_memory()`` before either's tool fires, making the race window
    deterministic without relying on sleep-based timing.
    """
    barrier = threading.Barrier(2)
    agent = _MemoryRaceAgent(graph=None)  # graph wired below (needs agent ref)
    graph = _FakeGraph(pipeline=agent, barrier=barrier)
    agent.agent = graph

    errors: List[Exception] = []

    ta = threading.Thread(target=_run_stream, args=(agent, "query-A", errors))
    tb = threading.Thread(target=_run_stream, args=(agent, "query-B", errors))
    ta.start()
    tb.start()
    ta.join(timeout=30)
    tb.join(timeout=30)

    assert not errors, f"stream raised: {errors!r}"
    assert not ta.is_alive() and not tb.is_alive(), "a thread hung"

    memory_a = agent._captured_memories.get(ta.ident)
    memory_b = agent._captured_memories.get(tb.ident)

    assert memory_a is not None, "Thread A did not capture a RunMemory"
    assert memory_b is not None, "Thread B did not capture a RunMemory"
    assert memory_a is not memory_b, "Both threads received the same RunMemory object"

    docs_a = memory_a.unique_documents()
    docs_b = memory_b.unique_documents()

    # Isolation invariant: each RunMemory holds exactly its own document.
    # With the bug, the second thread's start_run_memory() overwrites
    # self._active_memory, so both tool callbacks write into the same object:
    # one memory gets 2 docs and the other gets 0.
    assert len(docs_a) == 1, (
        f"Memory A holds {len(docs_a)} doc(s) (expected 1 — its own only). "
        f"Contents: {[d.page_content for d in docs_a]!r}"
    )
    assert (
        docs_a[0].page_content == "query-A"
    ), f"Memory A contains the wrong document: {docs_a[0].page_content!r}"

    assert len(docs_b) == 1, (
        f"Memory B holds {len(docs_b)} doc(s) (expected 1 — its own only). "
        f"Contents: {[d.page_content for d in docs_b]!r}"
    )
    assert (
        docs_b[0].page_content == "query-B"
    ), f"Memory B contains the wrong document: {docs_b[0].page_content!r}"
