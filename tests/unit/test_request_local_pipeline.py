"""Unit tests for the request-local pipeline *view* (issue #86).

``_build_request_local_pipeline(pipeline, override_llm)`` returns a shallow copy
of the shared pipeline bound to a per-request override LLM. These tests are the
standing guard for design D1's *zero-writes-to-shared* invariant (the ``is``
identity assertions) and for D1a's document-isolation requirement (a static tool
invoked on the view must record into the view's memory, never the source's).

The pipeline under test bypasses ``BaseReActAgent.__init__`` (LLM/prompt init)
and overrides only ``_create_agent`` (to avoid building a real LangGraph agent)
and ``_build_static_tools`` (to supply a single ``self``-bound static tool), so
these stay pure unit tests while exercising the real ``refresh_agent`` / ``tools``
/ ``RunMemory`` code paths that the view relies on.
"""

import threading
from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document

from src.archi.pipelines.agents.base_react import BaseReActAgent
from src.interfaces.chat_app.app import _build_request_local_pipeline


class _StaticToolPipeline(BaseReActAgent):
    """Minimal real ReAct agent whose one static tool binds to ``self``."""

    def __init__(self, agent_llm):
        # Only the attributes the request-local view path reads/rebuilds.
        self._active_memory = None
        self._tool_budgets_cache = None
        self._static_tools = None
        self._mcp_tools = None
        self._active_tools = []
        self._static_middleware = None
        self._active_middleware = []
        self.agent = None
        self.agent_llm = agent_llm
        self.agent_prompt = ""
        self.selected_tool_names = ["fetch_catalog_document"]
        # Sentinel per-run collaborators a view must reset rather than inherit.
        self._vector_tools = ["shared-vector-tool"]
        self._vector_retrievers = ["shared-retriever"]

    def _create_agent(self, tools, middleware):
        # Per-build sentinel instead of a real LangGraph agent, closing over the
        # LLM/tools so tests can tell one build apart from another.
        return {"llm": self.agent_llm, "tools": list(tools)}

    def _build_static_tools(self):
        def fetch_catalog_document(query="q"):
            # Real static tools record into the memory of the instance that
            # built them, via ``self._store_documents`` (design D1a).
            self._store_documents("catalog", [Document(page_content="doc")])
            return "ok"

        return [fetch_catalog_document]

    def _build_static_middleware(self):
        return []


class _McpPipeline(BaseReActAgent):
    """A pipeline selecting ``mcp`` whose ``_build_mcp_tools`` is counted."""

    def __init__(self):
        self._active_memory = None
        self._tool_budgets_cache = None
        self._static_tools = None
        self._mcp_tools = None
        self._active_tools = []
        self._static_middleware = None
        self._active_middleware = []
        self.agent = None
        self.agent_llm = "default-llm"
        self.agent_prompt = ""
        self.selected_tool_names = ["mcp"]
        self._mcp_lock = threading.Lock()
        self.mcp_client = None
        self.build_calls = 0

    def _create_agent(self, tools, middleware):
        return {"tools": list(tools)}

    def _build_static_tools(self):
        return []

    def _build_static_middleware(self):
        return []

    def _build_mcp_tools(self):
        self.build_calls += 1
        self.mcp_client = object()
        return [lambda: "mcp-result"]


def test_mcp_tools_memoized_on_source_and_shared_by_views():
    """Codex finding (PR #124) / design D6: concurrent overridden requests must
    share ONE MCP build. The memoization must fill the SOURCE's ``_mcp_tools``
    (the one permitted shared write), so building multiple views triggers
    ``_build_mcp_tools`` exactly once and every view reuses that list — rather
    than each view building (and leaking) its own client."""
    source = _McpPipeline()

    view1 = _build_request_local_pipeline(source, "override-1")
    view2 = _build_request_local_pipeline(source, "override-2")

    # Exactly one build, performed on the SOURCE (not once per view).
    assert source.build_calls == 1
    assert source._mcp_tools is not None
    # Every view reuses the source-populated list, never a per-view rebuild.
    assert view1._mcp_tools is source._mcp_tools
    assert view2._mcp_tools is source._mcp_tools


def test_view_is_distinct_and_shared_pipeline_unchanged():
    """Task 2.1: the view is a distinct object bound to the override, and the
    shared pipeline keeps the *identical* ``agent_llm`` / ``agent`` objects."""
    source = _StaticToolPipeline("default-llm")
    source.refresh_agent(force=True)  # prime the shared agent
    original_llm = source.agent_llm
    original_agent = source.agent
    override_llm = object()

    view = _build_request_local_pipeline(source, override_llm)

    assert view is not source
    assert view.agent_llm is override_llm
    # Zero writes to shared: the source keeps the identical objects it had.
    assert source.agent_llm is original_llm
    assert source.agent is original_agent


def test_view_resets_per_run_state():
    """Task 2.2: per-run state and self-bound collaborators are reset/rebuilt on
    the view rather than inherited from the source pipeline."""
    source = _StaticToolPipeline("default-llm")
    source.refresh_agent(force=True)
    source.start_run_memory()  # source now has non-None run memory

    view = _build_request_local_pipeline(source, "override-llm")

    # Per-run memory and vector state are reset to None on the view.
    assert view._active_memory is None
    assert view._vector_tools is None
    assert view._vector_retrievers is None
    # ...while the source's own per-run state is untouched.
    assert source._active_memory is not None

    # Agent, tools, and middleware are rebuilt as distinct objects bound to the
    # view, not the shared lists/instance carried over by the shallow copy.
    assert view.agent is not source.agent
    assert view._active_tools is not source._active_tools
    assert view._active_middleware is not source._active_middleware
    assert view._static_tools is not source._static_tools


def test_view_static_tool_isolates_documents_to_view_memory():
    """Task 2.3: invoking a *static* tool on the view records documents into the
    view's run memory while the source pipeline's ``_active_memory`` stays None."""
    source = _StaticToolPipeline("default-llm")
    source.refresh_agent(force=True)  # build source-bound static tools

    view = _build_request_local_pipeline(source, "override-llm")
    view.start_run_memory()

    # Invoke the static tool the view rebuilt for itself.
    view_tool = view._active_tools[0]
    view_tool()

    # Documents land in the VIEW's memory only.
    assert view.active_memory is not None
    assert len(view.active_memory.unique_documents()) == 1
    # The shared pipeline's run memory is never touched by the view's tool.
    assert source.active_memory is None


# --- Group 7: the in-loop bound follows the model bound to the request -------
#
# The pipelines above override `_build_static_middleware` to return `[]`, so
# they say nothing about the budget a view ends up with. These build the REAL
# bound and read it back off the **compiled agent** rather than off
# `view.middleware` — a rebuilt cache that never reaches `create_agent` is the
# silent no-op these tests exist to catch.


class _BudgetPipeline(BaseReActAgent):
    """A pipeline that derives a real in-loop budget.

    Only `_resolve_provider_context_window` — the provider-registry boundary —
    is stubbed, by the same by-name lookup the real one performs.
    """

    WINDOWS = {"big-model": 200000, "small-model": 32768}

    def __init__(self, agent_llm, *, provider="prov", model="big-model", config=None):
        self.config = config or {}
        self.pipeline_config = {}
        self.default_provider = provider
        self.default_model = model
        self.selected_tool_names = []
        self._active_memory = None
        self._tool_budgets_cache = None
        self._static_tools = None
        self._mcp_tools = None
        self._active_tools = []
        self._static_middleware = None
        self._active_middleware = []
        self.agent = None
        self.agent_llm = agent_llm
        self.agent_prompt = ""

    def _resolve_provider_context_window(self):
        return self.WINDOWS.get(self.default_model)

    def _create_agent(self, tools, middleware):
        return {"middleware": list(middleware)}

    def _build_static_tools(self):
        return []


def _llm(max_tokens=None):
    """A bound model whose configured output cap is `max_tokens`."""
    llm = MagicMock()
    llm.max_tokens = max_tokens
    return llm


def _compiled_budget(pipeline):
    """The budget of the bound the pipeline's **compiled agent** is running."""
    installed = pipeline.agent["middleware"]
    assert len(installed) == 1, f"expected one bound, got {len(installed)}"
    return installed[0].budget


def _primed_source(config=None):
    source = _BudgetPipeline(_llm(), config=config)
    source.refresh_agent(force=True)
    return source


def test_view_budget_derives_from_the_overriding_model():
    """7.1 / 7.7: window AND output cap both describe the override.

    32768 - max(15%, 8192) - 20% = 16384. Deriving the cap from the override
    while leaving the window at the source's 200000 yields 86000 instead —
    a budget six times the window the request will actually be sent to.
    """
    source = _primed_source()
    assert _compiled_budget(source).trigger == 120000

    view = _build_request_local_pipeline(
        source,
        _llm(max_tokens=8192),
        provider="prov",
        model="small-model",
        context_window=32768,
    )

    budget = _compiled_budget(view)
    assert budget.context_window == 32768
    assert budget.generation_reserve == 8192
    assert budget.trigger == 16384


def test_view_builds_its_own_bound_rather_than_inheriting_the_cache():
    """7.2: `_static_middleware` is a cache; the shallow copy carries it over."""
    source = _primed_source()

    view = _build_request_local_pipeline(
        source,
        _llm(max_tokens=8192),
        provider="prov",
        model="small-model",
        context_window=32768,
    )

    assert view._static_middleware is not source._static_middleware
    assert _compiled_budget(view) is not _compiled_budget(source)


def test_building_a_view_leaves_the_shared_budget_untouched():
    """7.3: the issue #86 invariant — zero writes to the shared pipeline."""
    source = _primed_source()
    shared_budget = _compiled_budget(source)
    shared_agent = source.agent
    shared_cache = source._static_middleware

    _build_request_local_pipeline(
        source,
        _llm(max_tokens=8192),
        provider="prov",
        model="small-model",
        context_window=32768,
    )

    assert source.agent is shared_agent
    assert source._static_middleware is shared_cache
    assert _compiled_budget(source) is shared_budget
    assert source.default_model == "big-model"


def test_carried_window_beats_a_name_lookup_that_cannot_resolve():
    """7.4: the custom-provider path.

    `_create_provider_llm` builds a provider from the deployment's YAML, so a
    custom model ID has metadata there and none at all in the by-name registry
    the agent would otherwise consult. The window must come from the model
    actually bound.
    """
    source = _primed_source()

    view = _build_request_local_pipeline(
        source,
        _llm(max_tokens=4096),
        provider="custom",
        model="an-unlisted-model",
        context_window=48000,
    )

    assert _compiled_budget(view).context_window == 48000


def test_unresolvable_override_installs_nothing_rather_than_guessing():
    """7.4: with no window from either route, fail open — never borrow one."""
    source = _primed_source()

    view = _build_request_local_pipeline(
        source,
        _llm(max_tokens=4096),
        provider="custom",
        model="an-unlisted-model",
        context_window=None,
    )

    assert view.agent["middleware"] == []


def test_declared_window_does_not_follow_a_model_override():
    """7.7: `context_editing.context_window` describes the *deployment's* model.

    Applying it to an override is the same defect as inheriting the source's
    window, arriving by a different route: measured, a declared 32768 paired
    with an override's 64000 output cap disables the bound outright.
    """
    config = {"services": {"chat_app": {"context_editing": {"context_window": 32768}}}}
    source = _primed_source(config=config)
    assert _compiled_budget(source).context_window == 32768

    view = _build_request_local_pipeline(
        source,
        _llm(max_tokens=8192),
        provider="prov",
        model="big-model",
        context_window=200000,
    )

    assert _compiled_budget(view).context_window == 200000


@pytest.mark.parametrize("bad", [0, -1, True, "32768", 1.5])
def test_an_unusable_carried_window_falls_back_to_name_resolution(bad):
    """A malformed carried window costs the shortcut, not the bound.

    `True` matters most: it is an `int` in Python, and a one-token window would
    clear every message on every call.
    """
    source = _primed_source()

    view = _build_request_local_pipeline(
        source,
        _llm(max_tokens=8192),
        provider="prov",
        model="small-model",
        context_window=bad,
    )

    # Resolved by name for the view's OWN model, never the source's.
    assert _compiled_budget(view).context_window == 32768
