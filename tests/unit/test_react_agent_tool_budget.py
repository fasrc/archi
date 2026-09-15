"""Unit tests for BaseReActAgent's tool-budget machinery.

Tests the spec scenarios that live on the agent side rather than the closure:
- the three-layer config lookup (pipeline_config > services.chat_app > class default),
- the class default for search_vectorstore_hybrid,
- the synthetic over-budget string returned by _consume_tool_budget,
- fail-open semantics when active_memory is None,
- per-turn reset via start_run_memory(),
- preservation of counter state across simulated recursion-handler retries,
- the in-loop context bound `_build_static_middleware` installs from that same
  call budget, and its agreement with the retrieval tool's own output ceiling.

Full LangGraph end-to-end (real LLM, real tool routing) is out of scope for
unit tests; that is the live verification step in the openspec change tasks.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document
from langchain_core.messages import ToolMessage

import src.archi.pipelines.agents.fasrc_docs_agent as fasrc_docs_agent
from src.archi.pipelines.agents.base_react import BaseReActAgent
from src.archi.pipelines.agents.fasrc_docs_agent import FASRCDocsAgent
from src.archi.pipelines.agents.tools.retriever import DEFAULT_RETRIEVER_RESULT_CHARS
from src.archi.pipelines.agents.utils.context_budget import ContextBudget
from src.archi.pipelines.agents.utils.context_middleware import (
    ContextBudgetMiddleware,
    clamp_tool_results,
)


class _TestableAgent(BaseReActAgent):
    """Subclass that skips LLM/prompt initialization so we can test budget machinery alone."""

    def __init__(
        self, config: Dict[str, Any], *, pipeline_config: Dict[str, Any] | None = None
    ) -> None:
        # Replicate the parts of BaseReActAgent.__init__ that are required for the budget
        # machinery to work, without touching providers, prompts, or LangGraph wiring.
        self.config = config
        self.archi_config = config.get("archi") or {}
        self.dm_config = config.get("data_manager", {})
        # Skip the normal pipeline_map lookup so tests can inject pipeline_config directly.
        self.pipeline_config = pipeline_config or {}
        self.agent_spec = None
        self.default_provider = None
        self.default_model = None
        self.selected_tool_names = []
        self._active_memory = None
        self._tool_budgets_cache = None
        self._static_tools = None
        self._mcp_tools = None
        self._active_tools = []
        self._static_middleware = None
        self._active_middleware = []
        self.agent = None
        self.agent_llm = MagicMock()
        self.agent_prompt = "test prompt"
        self.mcp_client = None


# --- 3.x: config lookup hierarchy ------------------------------------------


def test_class_default_applies_when_no_config():
    """search_vectorstore_hybrid default budget of 2 applies with no config overrides."""
    agent = _TestableAgent(config={})
    budgets = agent._tool_budgets()
    assert budgets.get("search_vectorstore_hybrid") == 2


def test_chat_app_config_overrides_class_default():
    """services.chat_app.tool_budgets overrides DEFAULT_TOOL_BUDGETS."""
    agent = _TestableAgent(
        config={
            "services": {"chat_app": {"tool_budgets": {"search_vectorstore_hybrid": 5}}}
        },
    )
    assert agent._tool_budgets().get("search_vectorstore_hybrid") == 5


def test_pipeline_config_overrides_chat_app_config():
    """pipeline_config.tool_budgets wins over services.chat_app.tool_budgets."""
    agent = _TestableAgent(
        config={
            "services": {"chat_app": {"tool_budgets": {"search_vectorstore_hybrid": 5}}}
        },
        pipeline_config={"tool_budgets": {"search_vectorstore_hybrid": 7}},
    )
    assert agent._tool_budgets().get("search_vectorstore_hybrid") == 7


def test_tool_without_configured_budget_is_unbounded():
    """A tool name absent from every layer returns None (interpreted as no budget downstream)."""
    agent = _TestableAgent(config={})
    budgets = agent._tool_budgets()
    assert budgets.get("an_unbudgeted_tool") is None


def test_tool_budgets_cached_after_first_call():
    """Repeated _tool_budgets() calls return the cached dict, not a fresh merge."""
    agent = _TestableAgent(config={})
    first = agent._tool_budgets()
    second = agent._tool_budgets()
    assert first is second, "_tool_budgets must cache after the first call"


def test_invalid_budget_value_is_logged_and_skipped():
    """Non-int budget values are ignored without raising."""
    agent = _TestableAgent(
        config={
            "services": {
                "chat_app": {
                    "tool_budgets": {"search_vectorstore_hybrid": "not-a-number"}
                }
            }
        },
    )
    # Falls back to the class default since the override was rejected.
    assert agent._tool_budgets().get("search_vectorstore_hybrid") == 2


@pytest.mark.parametrize("bad", [0, -1, -5])
def test_non_positive_budget_is_ignored_not_unbounded(bad):
    """0/negative would be treated as 'no budget' downstream (silently disabling the
    cap); such values must be rejected so the default cap stands."""
    agent = _TestableAgent(
        config={
            "services": {
                "chat_app": {"tool_budgets": {"search_vectorstore_hybrid": bad}}
            }
        },
    )
    # Override rejected -> class default (2) preserved, cap NOT disabled.
    assert agent._tool_budgets().get("search_vectorstore_hybrid") == 2


def test_non_positive_pipeline_budget_is_ignored():
    """Same positivity guard on the pipeline_config layer."""
    agent = _TestableAgent(
        config={},
        pipeline_config={"tool_budgets": {"search_vectorstore_hybrid": 0}},
    )
    assert agent._tool_budgets().get("search_vectorstore_hybrid") == 2


# --- 1.x + 2.x: _consume_tool_budget behavior ------------------------------


def test_consume_tool_budget_returns_none_when_no_active_memory():
    """Fail-open: with no active turn, the budget check returns None (allow the call)."""
    agent = _TestableAgent(config={})
    assert agent.active_memory is None
    assert agent._consume_tool_budget("search_vectorstore_hybrid") is None


def test_consume_tool_budget_short_circuits_after_cap_with_default():
    """Calls 1 and 2 return None; call 3+ return the synthetic string under default budget=2."""
    agent = _TestableAgent(config={})
    agent.start_run_memory()

    assert agent._consume_tool_budget("search_vectorstore_hybrid") is None  # call 1
    assert agent._consume_tool_budget("search_vectorstore_hybrid") is None  # call 2
    msg = agent._consume_tool_budget("search_vectorstore_hybrid")  # call 3
    assert isinstance(msg, str)
    assert msg.startswith("Search budget exhausted:")
    assert "search_vectorstore_hybrid" in msg
    assert "limit=2" in msg


def test_consume_tool_budget_for_unconfigured_tool_never_short_circuits():
    """Tool with no budget at any layer never returns the synthetic string."""
    agent = _TestableAgent(config={})
    agent.start_run_memory()
    for _ in range(20):
        assert agent._consume_tool_budget("an_unbudgeted_tool") is None


# --- 2.x: per-turn reset ---------------------------------------------------


def test_new_turn_resets_budget():
    """start_run_memory() creates a fresh RunMemory, which resets per-tool counters."""
    agent = _TestableAgent(config={})

    # Turn 1: burn the budget.
    agent.start_run_memory()
    for _ in range(3):
        agent._consume_tool_budget("search_vectorstore_hybrid")
    assert agent.active_memory is not None
    assert agent.active_memory.tool_call_count("search_vectorstore_hybrid") == 3

    # Turn 2: brand-new RunMemory restores the budget from scratch.
    agent.start_run_memory()
    assert agent.active_memory is not None
    assert agent.active_memory.tool_call_count("search_vectorstore_hybrid") == 0
    assert (
        agent._consume_tool_budget("search_vectorstore_hybrid") is None
    )  # call 1 in new turn


def test_recursion_handler_retry_preserves_counter():
    """Reusing the same active_memory (as the recursion-handler retry does) preserves the counter.

    The recursion-handler code path does NOT call start_run_memory(); it reuses the existing
    active_memory because the retry is still inside the same user turn. So a tool that was
    already at the cap should continue to short-circuit during the retry.
    """
    agent = _TestableAgent(config={})
    agent.start_run_memory()
    for _ in range(3):
        agent._consume_tool_budget("search_vectorstore_hybrid")

    # Simulate the recursion-handler reusing the same memory (no new start_run_memory).
    assert agent.active_memory is not None
    pre_retry_count = agent.active_memory.tool_call_count("search_vectorstore_hybrid")
    msg = agent._consume_tool_budget("search_vectorstore_hybrid")

    assert pre_retry_count == 3, "counter must carry over into the retry"
    assert isinstance(msg, str) and msg.startswith(
        "Search budget exhausted:"
    ), "an already-exhausted budget continues to short-circuit during recursion-handler retry"


# --- 4.x: search_vectorstore_hybrid default ---------------------------------


def test_search_vectorstore_hybrid_is_in_default_tool_budgets():
    """Class-level DEFAULT_TOOL_BUDGETS includes the search_vectorstore_hybrid entry."""
    assert "search_vectorstore_hybrid" in BaseReActAgent.DEFAULT_TOOL_BUDGETS
    assert BaseReActAgent.DEFAULT_TOOL_BUDGETS["search_vectorstore_hybrid"] == 2


# --- 6.x: the in-loop context bound is wired into the agent ------------------
#
# These assert on the *resolved numbers*, not merely that a list is non-empty.
# Nothing in the rest of the suite reaches `_build_static_middleware`: every
# other test double overrides it, so it returned `[]` unchallenged. A test that
# only checked `len(...) == 1` would pass against an implementation that
# installed a middleware sized from the wrong window.


def _agent_with_window(
    window: Any,
    *,
    config: Dict[str, Any] | None = None,
    pipeline_config: Dict[str, Any] | None = None,
) -> _TestableAgent:
    """A testable agent whose provider reports *window*.

    Replaces `_get_model_context_window` only — the boundary to the provider
    layer, which needs a live provider registry to answer. Everything under
    test (`_build_static_middleware`, the budget arithmetic, the config merge)
    runs for real.
    """
    agent = _TestableAgent(config=config or {}, pipeline_config=pipeline_config)
    agent._get_model_context_window = lambda: window  # type: ignore[method-assign]
    return agent


def _installed_budget(agent: BaseReActAgent) -> ContextBudget:
    """The single bound the agent installs, unwrapped to its resolved numbers."""
    installed = agent._build_static_middleware()
    assert len(installed) == 1
    bound = installed[0]
    assert isinstance(bound, ContextBudgetMiddleware)
    return bound.budget


def test_static_middleware_trigger_derives_from_the_provider_window():
    """6.1: a known window installs one bound whose trigger is the resolved budget.

    200000 - max(15% reserve, output cap) - 20% counting margin = 120000. The
    MagicMock LLM exposes no usable `max_tokens`, so the percentage reserve wins.
    """
    agent = _agent_with_window(200000)

    installed = agent._build_static_middleware()

    assert len(installed) == 1
    bound = installed[0]
    assert isinstance(bound, ContextBudgetMiddleware)
    budget = bound.budget
    assert budget.context_window == 200000
    assert budget.trigger == 120000
    assert budget.generation_reserve == 30000
    assert budget.counting_margin == 50000


def test_static_middleware_exemption_sized_from_the_configured_call_budget():
    """6.1: the per-turn retrieval call budget reaches the exemption arithmetic."""
    agent = _agent_with_window(
        200000,
        config={
            "services": {"chat_app": {"tool_budgets": {"search_vectorstore_hybrid": 4}}}
        },
    )

    budget = _installed_budget(agent)

    assert budget.exempt_count == 4
    assert budget.exempt_floor_tokens == 4 * budget.per_result_tokens


def test_reserve_sized_from_the_bound_model_not_the_percentage():
    """6.1: the agent's own LLM is what the output cap is read from.

    A model carrying a 64000-token cap needs a reserve that large: a 15%
    reserve would permit a 170000-token prompt while the provider is
    simultaneously allowed 64000 of generation, which the 200000-token window
    rejects before the trigger is ever consulted.
    """
    agent = _agent_with_window(200000)
    agent.agent_llm.max_tokens = 64000

    budget = _installed_budget(agent)

    assert budget.generation_reserve == 64000
    assert budget.trigger == 86000


def test_static_middleware_empty_when_context_window_undeterminable():
    """6.2: behaviour is unchanged from today when the provider reports nothing."""
    agent = _TestableAgent(config={})

    # The real method, not a stub: no provider/model configured means no window.
    assert agent._get_model_context_window() is None
    assert agent._build_static_middleware() == []


def test_declared_window_overrides_the_provider_window():
    """6.1: `services.chat_app.context_editing` reaches the factory from the agent.

    32768 - int(32768*0.15) - int(32768*0.20) = 19661. Without the config being
    forwarded this would resolve against the provider's 200000 instead.
    """
    agent = _agent_with_window(
        200000,
        config={
            "services": {"chat_app": {"context_editing": {"context_window": 32768}}}
        },
    )

    budget = _installed_budget(agent)

    assert budget.context_window == 32768
    assert budget.trigger == 19661


def test_disabled_in_config_installs_nothing():
    """`context_editing.enabled: false` is an operator rollback of the in-loop bound."""
    agent = _agent_with_window(
        200000,
        config={"services": {"chat_app": {"context_editing": {"enabled": False}}}},
    )

    assert agent._build_static_middleware() == []


def test_middleware_reaches_create_agent():
    """6.3: the built list is what `create_agent(...)` is actually handed.

    Asserting only on `_build_static_middleware`'s return value would pass even
    if `refresh_agent` dropped it on the floor.
    """
    agent = _agent_with_window(200000)
    agent._create_agent = MagicMock(return_value="compiled-graph")  # type: ignore[method-assign]

    agent.refresh_agent()

    assert agent._create_agent.call_count == 1
    middleware = agent._create_agent.call_args[0][1]
    assert [type(m) for m in middleware] == [ContextBudgetMiddleware]
    assert middleware[0].budget.trigger == 120000


# --- 6.6: the deployed agent's tool and bound agree on the same numbers ------
#
# Against the real FASRCDocsAgent, not a helper stub. The tool's own output
# ceiling and the bound's per-result ceiling are set in different modules and
# nothing else forces them to agree; a bound sized below the tool's ceiling
# re-truncates every full-size retrieval result on every model call.


class _FixedRetriever:
    """Stands in for the vector store, not for any agent code.

    ``_update_vector_retrievers`` — the real method — still builds the tool.
    """

    def __init__(self, docs):
        self._docs = docs

    def invoke(self, query):  # pragma: no cover - trivial
        return self._docs


def _oversized_docs(count: int = 4) -> list:
    """Documents whose *metadata* alone blows past the tool's ceiling.

    ``max_chars`` bounds page content only; the snippet header interpolates
    title/url/hash uncapped, which is why the tool clamps its assembled result.
    """
    return [
        Document(
            page_content="C" * 5_000,
            metadata={
                "title": "T" * 5_000,
                "url": "https://example.org/" + "u" * 5_000,
                "resource_hash": "h" * 100,
            },
        )
        for _ in range(count)
    ]


def _fasrc_agent(monkeypatch, retriever, *, call_budget: int, window: int = 200000):
    """A real FASRCDocsAgent wired just far enough to build tools and its bound."""
    agent = FASRCDocsAgent.__new__(FASRCDocsAgent)
    agent.config = {
        "services": {
            "chat_app": {"tool_budgets": {"search_vectorstore_hybrid": call_budget}}
        }
    }
    agent.pipeline_config = {}
    agent.dm_config = {}
    agent.default_provider = "local"
    agent.default_model = "a-self-hosted-model"
    agent.agent_llm = MagicMock()
    agent.enable_vector_tools = True
    agent.catalog_service = object()
    agent._active_memory = None
    agent._tool_budgets_cache = None
    agent._static_middleware = None
    agent._get_model_context_window = lambda: window
    monkeypatch.setattr(
        fasrc_docs_agent, "build_vector_retriever", lambda _vs, _cfg: retriever
    )
    agent._update_vector_retrievers(object())
    return agent


def test_real_agent_result_survives_its_own_bound_untouched(monkeypatch):
    """6.6: a full-size result from the real tool passes the real bound unmodified."""
    agent = _fasrc_agent(monkeypatch, _FixedRetriever(_oversized_docs()), call_budget=3)
    agent.start_run_memory()
    tool = agent._vector_tools[0]
    assert tool.name == "search_vectorstore_hybrid"

    result = tool.invoke({"query": "gpu partitions"})

    # The tool bounds its complete serialized output...
    assert len(result) <= DEFAULT_RETRIEVER_RESULT_CHARS

    budget = _installed_budget(agent)
    # ...and the bound's per-result ceiling sits above it, so the middleware
    # leaves that output alone rather than truncating it a second time.
    message = ToolMessage(
        content=result, tool_call_id="call-1", name="search_vectorstore_hybrid"
    )
    (retained,) = clamp_tool_results([message], budget.per_result_tokens)
    assert retained.content == result


def test_real_agent_exemption_matches_its_own_call_budget(monkeypatch):
    """6.6: the tool stops at the configured cap and the bound exempts that many."""
    agent = _fasrc_agent(
        monkeypatch, _FixedRetriever(_oversized_docs(1)), call_budget=2
    )
    agent.start_run_memory()
    tool = agent._vector_tools[0]

    outcomes = [tool.invoke({"query": "q"}) for _ in range(3)]

    exhausted = [o.startswith("Search budget exhausted:") for o in outcomes]
    assert exhausted == [False, False, True]
    # The exemption covers exactly the results that call budget can produce:
    # exempting fewer leaves evidence clearable, exempting more protects the
    # synthetic refusals that follow the cap.
    assert _installed_budget(agent).exempt_count == 2


# --- 6.7: the per-model map reaches pipeline-map-configured agents (#343 review)


def test_a_pipeline_map_agent_gets_its_declared_per_model_window():
    """A pipeline built from `models` still resolves its own model id.

    `_init_llms()` supports two initialisation paths. The constructor path sets
    `default_provider`/`default_model`; the `archi.pipeline_map.<agent>.models`
    path leaves both at `None` and builds the model from a `provider/model`
    reference instead. A call site reading only `default_model` therefore hands
    the middleware `model_id=None`, every `context_windows` lookup misses, and
    the agents that need a declaration most — self-hosted models a provider
    cannot resolve by name — install no bound at all. `_streamed_provider()`
    already had to learn this same lesson for issue #122.
    """
    agent = _agent_with_window(
        None,
        config={
            "services": {
                "chat_app": {
                    "context_editing": {
                        "context_windows": {"a-self-hosted-model": 32768}
                    }
                }
            }
        },
        pipeline_config={
            "models": {"required": {"chat_model": "local/a-self-hosted-model"}}
        },
    )

    assert _installed_budget(agent).context_window == 32768


def test_a_pipeline_map_agent_reports_its_model_in_the_absence_warning(caplog):
    """The log line that exposes an unprotected deployment must name the model.

    `model_label` is built from the same two attributes, so on this path the
    warning read `None/None` — the one message an operator gets when nothing is
    installed, naming nothing they can act on.
    """
    agent = _agent_with_window(
        None,
        pipeline_config={
            "models": {"required": {"chat_model": "local/a-self-hosted-model"}}
        },
    )

    with caplog.at_level("WARNING"):
        assert agent._build_static_middleware() == []

    assert "local/a-self-hosted-model" in caplog.text
    assert "None/None" not in caplog.text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
