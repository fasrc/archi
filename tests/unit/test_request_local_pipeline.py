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
from src.archi.pipelines.agents.utils.thinking_gate import provider_emits_thinking
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

    # A genuinely different model from the source's configured "big-model".
    view = _build_request_local_pipeline(
        source,
        _llm(max_tokens=8192),
        provider="other",
        model="some-other-model",
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


def test_selecting_the_deployments_own_model_keeps_its_declared_window():
    """The chat UI sends provider+model on **every** message, not only when the
    user switches models — `chat.js` reads `state.selectedProvider` and posts it
    with each send. So the request-local path is the normal path, and treating
    it as a model *change* discards the operator's declared window.

    On a self-hosted deployment that is fatal rather than merely conservative:
    nothing resolves the window by name, so the bound is not installed at all
    and the whole feature ships inert on the deployment it was written for.

    The declared window describes a model the operator named. When the request
    names that same model, it still describes it.
    """
    config = {"services": {"chat_app": {"context_editing": {"context_window": 32768}}}}
    source = _BudgetPipeline(
        _llm(), provider="local", model="a-self-hosted-model", config=config
    )
    source.refresh_agent(force=True)
    assert _compiled_budget(source).context_window == 32768
    # The provider cannot resolve this name — the case the declaration exists for.
    assert source._resolve_provider_context_window() is None

    view = _build_request_local_pipeline(
        source,
        _llm(),
        provider="local",
        model="a-self-hosted-model",
        context_window=None,
    )

    assert _compiled_budget(view).context_window == 32768


OVERRIDE_MODEL = "palmfuture/Qwen3.8-27B-GPTQ-Int4"


def test_an_override_named_in_the_per_model_map_gets_a_bound():
    """Issue #262: the defect this closes, through the real builder.

    The override is a model the provider cannot resolve — the self-hosted case —
    and the deployment-wide ``context_window`` is correctly withdrawn because it
    describes a different model. Before ``context_windows`` existed there was no
    way to declare the override's own window, so the view ran with no bound at
    all. An entry keyed by the override's own id supplies it.
    """
    config = {
        "services": {
            "chat_app": {
                "context_editing": {
                    "context_window": 200000,
                    "context_windows": {OVERRIDE_MODEL: 32768},
                }
            }
        }
    }
    source = _BudgetPipeline(_llm(), provider="local", model="big-model", config=config)
    source.refresh_agent(force=True)
    # The override is unresolvable by name: exactly why a declaration is needed.
    assert _BudgetPipeline.WINDOWS.get(OVERRIDE_MODEL) is None

    view = _build_request_local_pipeline(
        source,
        _llm(),
        provider="local",
        model=OVERRIDE_MODEL,
        context_window=None,
    )

    assert _compiled_budget(view).context_window == 32768
    assert _compiled_budget(source).context_window == 200000, "source untouched"


def test_an_override_absent_from_the_per_model_map_still_installs_nothing():
    """The fail-open path stays exactly as it is: the map narrows nothing it
    does not name, and a window describing another model is never borrowed."""
    config = {
        "services": {
            "chat_app": {
                "context_editing": {
                    "context_window": 200000,
                    "context_windows": {"some/other-model": 32768},
                }
            }
        }
    }
    source = _BudgetPipeline(_llm(), provider="local", model="big-model", config=config)
    source.refresh_agent(force=True)

    view = _build_request_local_pipeline(
        source,
        _llm(),
        provider="local",
        model=OVERRIDE_MODEL,
        context_window=None,
    )

    assert view.agent["middleware"] == []


def test_a_declaration_applies_to_its_model_under_every_provider():
    """Pins today's contract and its known limitation (#344).

    The chat app takes ``provider`` and ``model`` as independent request fields,
    so a view can be bound to the same model id under a different provider. A
    declaration is keyed on the model id alone, so it answers for both. That is
    correct where a model id has one provider — every deployment in use — and
    is the case #344 exists to let an operator narrow. Pinning it here means
    the day the key gains a provider scope, this test is what says so.
    """
    config = {
        "services": {
            "chat_app": {
                "context_editing": {"context_windows": {OVERRIDE_MODEL: 32768}}
            }
        }
    }
    source = _BudgetPipeline(_llm(), provider="local", model="big-model", config=config)
    source.refresh_agent(force=True)

    same_provider = _build_request_local_pipeline(
        source, _llm(), provider="local", model=OVERRIDE_MODEL, context_window=None
    )
    other_provider = _build_request_local_pipeline(
        source, _llm(), provider="elsewhere", model=OVERRIDE_MODEL, context_window=None
    )

    assert _compiled_budget(same_provider).context_window == 32768
    assert _compiled_budget(other_provider).context_window == 32768


def test_a_pipeline_map_agent_keeps_its_window_when_the_ui_resends_its_model():
    """A pipeline-map agent must recognise its own deployed model.

    The chat app posts `provider` and `model` with **every** message, not only
    when the user switches, so `adopt_request_local_model()` decides on each
    turn whether this is a real model change. It compared against
    `default_provider`/`default_model`, which the pipeline-map initialisation
    path leaves at `None` — so an agent built that way read every ordinary turn
    as an override onto a different model, and the deployment-wide declared
    window was withdrawn by design. On a self-hosted model no provider can
    resolve by name, that leaves the normal chat path with no bound at all.
    """
    config = {
        "services": {
            "chat_app": {"context_editing": {"context_window": 32768}},
        }
    }
    source = _BudgetPipeline(_llm(), provider=None, model=None, config=config)
    source.pipeline_config = {
        "models": {"required": {"chat_model": "local/a-self-hosted-model"}}
    }
    source.refresh_agent(force=True)

    view = _build_request_local_pipeline(
        source,
        _llm(),
        provider="local",
        model="a-self-hosted-model",
        context_window=None,
    )

    assert view._is_request_local is False, "the UI re-sent the configured model"
    assert _compiled_budget(view).context_window == 32768


def test_a_pipeline_map_agent_still_treats_a_real_switch_as_an_override():
    """The other half of the contract: a different model is still an override.

    Recognising the configured model must not blunt the rule it exists beside —
    a window describing the deployment's own model is never lent to a model the
    request switched to.
    """
    config = {
        "services": {
            "chat_app": {"context_editing": {"context_window": 32768}},
        }
    }
    source = _BudgetPipeline(_llm(), provider=None, model=None, config=config)
    source.pipeline_config = {
        "models": {"required": {"chat_model": "local/a-self-hosted-model"}}
    }
    source.refresh_agent(force=True)

    view = _build_request_local_pipeline(
        source,
        _llm(),
        provider="local",
        model=OVERRIDE_MODEL,
        context_window=None,
    )

    assert view._is_request_local is True
    assert view.agent["middleware"] == [], "no window describes the override"


# --- the streamed-reasoning gate follows the view's provider (issue #122) ---


def _thinking_config():
    """Two providers: the deployment default is unset, ``thinker`` enables it."""
    return {
        "services": {
            "chat_app": {
                "default_provider": "prov",
                "providers": {
                    "prov": {},
                    "thinker": {
                        "extra_kwargs": {
                            "extra_body": {
                                "chat_template_kwargs": {"enable_thinking": True}
                            }
                        }
                    },
                },
            }
        }
    }


def test_the_thinking_gate_follows_a_request_local_provider_override():
    """The gate resolves against the provider the request will actually call.

    ``resolved_enable_thinking()`` reads ``services.chat_app.default_provider``,
    which is the configured default and not this request's provider, so reusing
    it would resolve the gate against the wrong provider entirely — the same
    class of defect as issue #262. Reading ``self.default_provider``, which
    ``adopt_request_local_model()`` rewrites, tracks the override for free.
    """
    source = _primed_source(config=_thinking_config())
    assert provider_emits_thinking(source.config, source.default_provider) is False

    view = _build_request_local_pipeline(
        source,
        _llm(max_tokens=8192),
        provider="thinker",
        model="small-model",
        context_window=32768,
    )

    assert provider_emits_thinking(view.config, view.default_provider) is True
    # And the shared pipeline is still unchanged (issue #86's invariant).
    assert provider_emits_thinking(source.config, source.default_provider) is False


def test_the_thinking_gate_is_unchanged_by_a_same_provider_model_switch():
    """The gate is provider-granular, which matches the mechanism.

    ``chat_template_kwargs`` is spread verbatim into the request body for every
    model called through a provider, and the schema carries no per-model
    ``enable_thinking``. Switching model within one provider therefore leaves the
    transmitted kwarg unchanged, and must leave the gate unchanged too.
    """
    source = _BudgetPipeline(
        _llm(), provider="thinker", model="big-model", config=_thinking_config()
    )
    source.refresh_agent(force=True)
    assert provider_emits_thinking(source.config, source.default_provider) is True

    view = _build_request_local_pipeline(
        source,
        _llm(max_tokens=8192),
        provider="thinker",
        model="small-model",
        context_window=32768,
    )

    assert view.default_model == "small-model"
    assert provider_emits_thinking(view.config, view.default_provider) is True
