"""Unit tests for the per-turn tool-call budget on create_retriever_tool.

Exercises the closure-level mechanic in
src/archi/pipelines/agents/tools/retriever.py: when enforce_budget is wired in
and returns a non-None string, the closure short-circuits without invoking the
underlying retriever. When enforce_budget is absent or returns None, behavior
matches the pre-change codepath exactly.

These tests do NOT instantiate BaseReActAgent; the BaseReActAgent-side budget
machinery (lookup hierarchy, _consume_tool_budget, per-turn reset) lives in
test_react_agent_tool_budget.py.
"""

from __future__ import annotations

from typing import Any, List
from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from src.archi.pipelines.agents.tools.retriever import create_retriever_tool
from src.archi.pipelines.agents.utils.run_memory import RunMemory


def _make_retriever(documents: List[Document] | None = None) -> MagicMock:
    """Return a MagicMock that quacks like a BaseRetriever with controllable invoke()."""
    docs = documents if documents is not None else [
        Document(page_content="hit", metadata={"filename": "f.md", "resource_hash": "abc"})
    ]
    retriever = MagicMock(spec=BaseRetriever)
    retriever.invoke.return_value = docs
    return retriever


def _budget_callback(memory: RunMemory, tool_name: str, cap: int):
    """Mirror BaseReActAgent._consume_tool_budget's behavior for a synthetic test fixture.

    Bumps the per-turn counter on `memory` for `tool_name`. Returns None if the
    new count is at or below `cap`; otherwise returns a string starting with
    'Search budget exhausted:'.
    """

    def cb() -> str | None:
        new_count = memory.bump_tool_call_count(tool_name)
        if new_count <= cap:
            return None
        return (
            f"Search budget exhausted: you have already called {tool_name} "
            f"the maximum number of times for this turn (limit={cap})."
        )

    return cb


# --- 5.2 -------------------------------------------------------------------


def test_first_two_calls_pass_through_and_increment_counter():
    """First and second calls reach the retriever; counter ends at 2."""
    memory = RunMemory()
    retriever = _make_retriever()
    tool: Any = create_retriever_tool(
        retriever,
        name="search_vectorstore_hybrid",
        enforce_budget=_budget_callback(memory, "search_vectorstore_hybrid", cap=2),
    )

    out1 = tool.invoke({"query": "q1"})
    out2 = tool.invoke({"query": "q2"})

    assert retriever.invoke.call_count == 2, "calls 1 and 2 must reach the retriever"
    assert "Search budget exhausted" not in out1
    assert "Search budget exhausted" not in out2
    assert memory.tool_call_count("search_vectorstore_hybrid") == 2


# --- 5.3 -------------------------------------------------------------------


def test_third_call_short_circuits_with_synthetic_string():
    """Third call returns the budget-exhausted string without invoking the retriever."""
    memory = RunMemory()
    retriever = _make_retriever()
    tool: Any = create_retriever_tool(
        retriever,
        name="search_vectorstore_hybrid",
        enforce_budget=_budget_callback(memory, "search_vectorstore_hybrid", cap=2),
    )

    tool.invoke({"query": "q1"})
    tool.invoke({"query": "q2"})
    out3 = tool.invoke({"query": "q3"})

    assert retriever.invoke.call_count == 2, "retriever must NOT be invoked on call 3"
    assert isinstance(out3, str)
    assert out3.startswith("Search budget exhausted:")
    assert "search_vectorstore_hybrid" in out3
    # Counter still increments past the cap so subsequent calls also short-circuit deterministically.
    assert memory.tool_call_count("search_vectorstore_hybrid") == 3


# --- 5.4 -------------------------------------------------------------------


def test_fresh_run_memory_resets_counter():
    """A freshly-constructed RunMemory has zero counter and lets calls through again."""
    retriever = _make_retriever()

    # Turn 1: burn the budget.
    turn1 = RunMemory()
    tool1: Any = create_retriever_tool(
        retriever,
        name="search_vectorstore_hybrid",
        enforce_budget=_budget_callback(turn1, "search_vectorstore_hybrid", cap=2),
    )
    for q in ("q1", "q2", "q3"):
        tool1.invoke({"query": q})
    assert retriever.invoke.call_count == 2, "turn 1 should hit the retriever exactly twice"
    assert turn1.tool_call_count("search_vectorstore_hybrid") == 3

    # Turn 2: brand-new RunMemory ⇒ brand-new budget.
    turn2 = RunMemory()
    assert turn2.tool_call_count("search_vectorstore_hybrid") == 0
    tool2: Any = create_retriever_tool(
        retriever,
        name="search_vectorstore_hybrid",
        enforce_budget=_budget_callback(turn2, "search_vectorstore_hybrid", cap=2),
    )
    out = tool2.invoke({"query": "q4"})
    assert retriever.invoke.call_count == 3, "turn 2's first call must reach the retriever"
    assert "Search budget exhausted" not in out
    assert turn2.tool_call_count("search_vectorstore_hybrid") == 1


# --- 5.5 -------------------------------------------------------------------


def test_two_tools_share_run_memory_with_independent_counters():
    """Counters keyed by tool name are independent; sharing RunMemory does not collide them."""
    memory = RunMemory()
    retriever_a = _make_retriever()
    retriever_b = _make_retriever()

    tool_a: Any = create_retriever_tool(
        retriever_a,
        name="search_vectorstore_hybrid",
        enforce_budget=_budget_callback(memory, "search_vectorstore_hybrid", cap=2),
    )
    tool_b: Any = create_retriever_tool(
        retriever_b,
        name="search_local_files",
        enforce_budget=_budget_callback(memory, "search_local_files", cap=3),
    )

    # Burn tool A's budget; tool B should be untouched.
    for q in ("a1", "a2", "a3"):
        tool_a.invoke({"query": q})
    assert retriever_a.invoke.call_count == 2
    assert memory.tool_call_count("search_vectorstore_hybrid") == 3
    assert memory.tool_call_count("search_local_files") == 0

    # Tool B can still consume its full budget.
    for q in ("b1", "b2", "b3"):
        out = tool_b.invoke({"query": q})
        assert "Search budget exhausted" not in out
    assert retriever_b.invoke.call_count == 3
    assert memory.tool_call_count("search_local_files") == 3

    # Tool B's fourth call short-circuits, but tool A is unaffected.
    out_b4 = tool_b.invoke({"query": "b4"})
    assert out_b4.startswith("Search budget exhausted:")
    assert "search_local_files" in out_b4
    assert retriever_b.invoke.call_count == 3


# --- 5.6 -------------------------------------------------------------------


def test_no_enforce_budget_argument_is_unbounded():
    """create_retriever_tool without enforce_budget hits the retriever on every call (backward compat)."""
    retriever = _make_retriever()
    tool: Any = create_retriever_tool(retriever, name="search_vectorstore_hybrid")

    for q in ("q1", "q2", "q3", "q4", "q5"):
        out = tool.invoke({"query": q})
        assert "Search budget exhausted" not in out

    assert retriever.invoke.call_count == 5


def test_enforce_budget_callback_returning_none_lets_calls_through():
    """A callback that always returns None is structurally equivalent to omitting the kwarg."""
    retriever = _make_retriever()
    calls = {"n": 0}

    def always_none() -> str | None:
        calls["n"] += 1
        return None

    tool: Any = create_retriever_tool(
        retriever,
        name="search_vectorstore_hybrid",
        enforce_budget=always_none,
    )

    for q in ("q1", "q2", "q3"):
        tool.invoke({"query": q})

    assert calls["n"] == 3, "the callback runs on every call"
    assert retriever.invoke.call_count == 3, "every call must reach the retriever when callback returns None"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
