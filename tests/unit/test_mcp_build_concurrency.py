"""Concurrency test for the lazy MCP-tool memoization (issue #86, design D6).

Building a request-local pipeline view calls ``refresh_agent(force=True)`` on the
shared pipeline instance, whose ``refresh_agent`` lazily builds the MCP tool list
with an unguarded ``if self._mcp_tools is None`` check-then-assign
(``base_react.py:1200-1204``) and overwrites ``self.mcp_client`` inside
``_build_mcp_tools``. Flask serves requests on threads, so the A/B comparison
pair — two overridden requests in flight at once — can both observe ``None``,
both enter ``_build_mcp_tools``, and construct (then leak) two MCP clients.

Design D6 guards the check-and-build with an instance ``threading.Lock`` so any
number of concurrent builds yields exactly one ``_build_mcp_tools`` call and one
MCP client per pipeline instance. This test drives the race deterministically:
``_build_mcp_tools`` blocks until released, and it is released only once both
threads have provably entered it (the unguarded case) or a bounded wait elapses
(the guarded case, where the second thread never enters). The assertion outcome
therefore does not depend on timing — unguarded builds twice, guarded builds once.

RED: the D6 lock (task 2.6) is not implemented yet, so ``refresh_agent``'s
check-and-build still races and this test fails with two builds. It is marked
``xfail(strict=True)`` — it starts passing once the lock lands, at which point the
strict marker fails the gate and must be removed in the GREEN phase (task 2.6).
"""

import threading

import pytest

from src.archi.pipelines.agents.base_react import BaseReActAgent


class _McpPipeline(BaseReActAgent):
    """Minimal real ReAct agent selecting only the ``mcp`` tool.

    Bypasses ``BaseReActAgent.__init__`` (LLM/prompt init) and stubs
    ``_create_agent`` / ``_build_static_tools`` so the test exercises the real
    ``refresh_agent`` MCP-memoization path without a LangGraph agent or servers.
    ``_build_mcp_tools`` is instrumented to count calls, count constructed
    clients, and block on a release event so the race window stays open.
    """

    def __init__(self):
        self._active_memory = None
        self._tool_budgets_cache = None
        self._static_tools = None
        self._mcp_tools = None
        self._active_tools = []
        self._static_middleware = None
        self._active_middleware = []
        self.agent = None
        self.agent_llm = object()
        self.agent_prompt = ""
        self.mcp_client = None
        self.selected_tool_names = ["mcp"]

        # Design D6 adds this lock in ``BaseReActAgent.__init__``; mirror it here
        # so the guarded ``refresh_agent`` has it. It is unused until D6 lands.
        self._mcp_lock = threading.Lock()

        # Instrumentation for the concurrent build.
        self.build_calls = 0
        self.clients_constructed = 0
        self._counter_lock = threading.Lock()
        self._both_entered = threading.Event()
        self._release = threading.Event()

    def _create_agent(self, tools, middleware):
        return {"tools": list(tools)}

    def _build_static_tools(self):
        return []

    def _build_static_middleware(self):
        return []

    def _build_mcp_tools(self):
        with self._counter_lock:
            self.build_calls += 1
            if self.build_calls == 2:
                self._both_entered.set()
        # Hold the race window open until released, then "construct the client".
        self._release.wait(timeout=5)
        self.mcp_client = object()
        with self._counter_lock:
            self.clients_constructed += 1

        def _mcp_tool():
            return "ok"

        return [_mcp_tool]


@pytest.mark.xfail(
    strict=True,
    reason="D6 lock (task 2.6) not implemented yet: refresh_agent races and builds twice",
)
def test_concurrent_builds_initialize_mcp_exactly_once():
    """Task 2.5: two threads concurrently building the shared pipeline's agent,
    with ``_mcp_tools is None``, must trigger exactly one ``_build_mcp_tools``
    call and construct exactly one MCP client."""
    pipeline = _McpPipeline()

    def build():
        pipeline.refresh_agent(force=True)

    t1 = threading.Thread(target=build)
    t2 = threading.Thread(target=build)
    t1.start()
    t2.start()

    # Unguarded: both threads enter _build_mcp_tools and set _both_entered fast.
    # Guarded (D6): only one thread ever enters, so this waits out the bound; the
    # second thread then finds _mcp_tools already populated and skips the build.
    pipeline._both_entered.wait(timeout=2)
    pipeline._release.set()

    t1.join(timeout=10)
    t2.join(timeout=10)

    assert pipeline.build_calls == 1
    assert pipeline.clients_constructed == 1
