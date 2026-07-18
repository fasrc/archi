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
