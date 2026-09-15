"""Lifecycle of the per-run memory map (issue #123, Codex review on PR #158).

The ContextVar added for #123 maps *some identity of the agent* to its
``RunMemory``. The first cut keyed that map on ``id(self)`` and never removed an
entry, which has two consequences these tests pin down:

* **Retention.** Worker threads are reused across requests, and a ContextVar
  value set on a thread outlives the request that set it. Every request-local
  pipeline view (``copy.copy`` of the shared pipeline, one per overridden
  request — see ``_build_request_local_pipeline``) therefore left its
  ``RunMemory``, and every document that memory had accumulated, pinned in the
  map for the life of the worker thread.

* **Aliasing.** ``id()`` in CPython is the object's address, and addresses are
  reused once an object is freed. A view allocated at a dead view's address
  would inherit that dead view's slot: ``active_memory`` would hand it the
  previous request's documents, and any callback firing before
  ``start_run_memory()`` would mutate that stale memory instead of failing open
  on ``None`` — precisely the cross-request attribution bug #123 exists to close.

Keying the map on the agent object itself, in a ``WeakKeyDictionary``, removes
both at once: a live object cannot alias another live object, and the entry
disappears when the agent is collected. Note the tests below assert on
*retention*, which is deterministic. The aliasing half is not directly testable
without control of the allocator — 500 allocations after freeing an agent did not
reuse its address — but it is unreachable once entries cannot outlive their
agent, because there is no orphaned slot left for a new object to land on.
"""

from __future__ import annotations

import copy
import gc
import threading

from src.archi.pipelines.agents.base_react import _ACTIVE_MEMORY, BaseReActAgent


class _BareAgent(BaseReActAgent):
    """Agent stub that skips the real ``__init__`` (LLM/prompt/provider setup).

    Deliberately sets no attributes at all. Any per-instance key the memory map
    depends on must therefore be established without ``__init__``'s cooperation —
    both because subclasses like this one bypass it and, more importantly,
    because request-local views are built with ``copy.copy``, which never calls
    it and would otherwise hand the view the source's key.
    """

    def __init__(self) -> None:  # noqa: D107 - intentionally empty
        pass


def _reset_context() -> None:
    _ACTIVE_MEMORY.set(None)


def _map_size() -> int:
    current = _ACTIVE_MEMORY.get()
    return 0 if current is None else len(current)


class TestMemoryMapReleasesCollectedAgents:
    """An agent that is gone must not keep a slot, or its documents, alive."""

    def test_entry_is_released_when_the_agent_is_collected(self):
        _reset_context()
        agent = _BareAgent()
        agent.start_run_memory()
        assert _map_size() == 1

        del agent
        gc.collect()

        assert _map_size() == 0

    def test_surviving_agents_keep_their_entries(self):
        """Release is per-agent, not a wholesale flush of the map."""
        _reset_context()
        keeper = _BareAgent()
        keeper_memory = keeper.start_run_memory()
        doomed = _BareAgent()
        doomed.start_run_memory()
        assert _map_size() == 2

        del doomed
        gc.collect()

        assert _map_size() == 1
        assert keeper.active_memory is keeper_memory

    def test_many_short_lived_agents_do_not_accumulate(self):
        """The worker-thread case: N sequential requests, no unbounded growth."""
        _reset_context()
        for _ in range(50):
            agent = _BareAgent()
            agent.start_run_memory()
            del agent
        gc.collect()

        assert _map_size() == 0


class TestPerInstanceIsolationIsPreserved:
    """The fix must not weaken what #123 and #86 bought.

    A request-local view is a ``copy.copy`` of the shared pipeline, so source and
    view are distinct objects sharing a ``__dict__``'s worth of values. They must
    still land in distinct slots, or an overridden request would record its
    documents into the shared pipeline's memory.
    """

    def test_a_copied_view_does_not_share_the_source_slot(self):
        _reset_context()
        source = _BareAgent()
        source_memory = source.start_run_memory()

        view = copy.copy(source)
        view_memory = view.start_run_memory()

        assert view_memory is not source_memory
        assert source.active_memory is source_memory
        assert view.active_memory is view_memory

    def test_view_started_alone_leaves_the_source_failing_open(self):
        """A view's run must not make the untouched source look like it has one."""
        _reset_context()
        source = _BareAgent()
        view = copy.copy(source)

        view.start_run_memory()

        assert source.active_memory is None

    def test_active_memory_is_none_before_start_run_memory(self):
        """Fail open: no run started means no memory for a callback to mutate."""
        _reset_context()
        agent = _BareAgent()

        assert agent.active_memory is None

    def test_threads_do_not_see_each_others_memory(self):
        """The ContextVar isolation #123 added still holds with the new keying."""
        _reset_context()
        agent = _BareAgent()
        seen = {}
        ready = threading.Barrier(2, timeout=5)

        def run(name):
            memory = agent.start_run_memory()
            ready.wait()
            seen[name] = (memory, agent.active_memory)

        threads = [threading.Thread(target=run, args=(n,)) for n in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert seen["a"][0] is not seen["b"][0]
        assert seen["a"][1] is seen["a"][0]
        assert seen["b"][1] is seen["b"][0]
