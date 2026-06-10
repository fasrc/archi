"""Unit tests for BaseReActAgent's tool-budget machinery.

Tests the spec scenarios that live on the agent side rather than the closure:
- the three-layer config lookup (pipeline_config > services.chat_app > class default),
- the class default for search_vectorstore_hybrid,
- the synthetic over-budget string returned by _consume_tool_budget,
- fail-open semantics when active_memory is None,
- per-turn reset via start_run_memory(),
- preservation of counter state across simulated recursion-handler retries.

Full LangGraph end-to-end (real LLM, real tool routing) is out of scope for
unit tests; that is the live verification step in the openspec change tasks.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from src.archi.pipelines.agents.base_react import BaseReActAgent


class _TestableAgent(BaseReActAgent):
    """Subclass that skips LLM/prompt initialization so we can test budget machinery alone."""

    def __init__(self, config: Dict[str, Any], *, pipeline_config: Dict[str, Any] | None = None) -> None:
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
        config={"services": {"chat_app": {"tool_budgets": {"search_vectorstore_hybrid": 5}}}},
    )
    assert agent._tool_budgets().get("search_vectorstore_hybrid") == 5


def test_pipeline_config_overrides_chat_app_config():
    """pipeline_config.tool_budgets wins over services.chat_app.tool_budgets."""
    agent = _TestableAgent(
        config={"services": {"chat_app": {"tool_budgets": {"search_vectorstore_hybrid": 5}}}},
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
        config={"services": {"chat_app": {"tool_budgets": {"search_vectorstore_hybrid": "not-a-number"}}}},
    )
    # Falls back to the class default since the override was rejected.
    assert agent._tool_budgets().get("search_vectorstore_hybrid") == 2


@pytest.mark.parametrize("bad", [0, -1, -5])
def test_non_positive_budget_is_ignored_not_unbounded(bad):
    """0/negative would be treated as 'no budget' downstream (silently disabling the
    cap); such values must be rejected so the default cap stands."""
    agent = _TestableAgent(
        config={"services": {"chat_app": {"tool_budgets": {"search_vectorstore_hybrid": bad}}}},
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
    msg = agent._consume_tool_budget("search_vectorstore_hybrid")            # call 3
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
    assert agent._consume_tool_budget("search_vectorstore_hybrid") is None  # call 1 in new turn


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
    assert isinstance(msg, str) and msg.startswith("Search budget exhausted:"), (
        "an already-exhausted budget continues to short-circuit during recursion-handler retry"
    )


# --- 4.x: search_vectorstore_hybrid default ---------------------------------


def test_search_vectorstore_hybrid_is_in_default_tool_budgets():
    """Class-level DEFAULT_TOOL_BUDGETS includes the search_vectorstore_hybrid entry."""
    assert "search_vectorstore_hybrid" in BaseReActAgent.DEFAULT_TOOL_BUDGETS
    assert BaseReActAgent.DEFAULT_TOOL_BUDGETS["search_vectorstore_hybrid"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
