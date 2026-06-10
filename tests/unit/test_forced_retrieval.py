"""Unit tests for enforced (mandatory) vector retrieval in CMSCompOpsAgent.

The chat model can ignore a "always search first" system prompt and answer
from its own weights, leaving ``source_documents`` empty (and the chat UI
showing "Link unavailable"). To *enforce* retrieval, the agent prefills a
completed tool round — an ``AIMessage`` carrying a ``search_vectorstore_hybrid``
tool call plus the matching ``ToolMessage`` result — before the model's first
turn, so the retrieval happens regardless of the model's choice.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.archi.pipelines.agents.cms_comp_ops_agent import CMSCompOpsAgent


class _FakeTool:
    """Stand-in for the real retriever StructuredTool."""

    def __init__(self, result: str = "[1] GLOBUS doc snippet"):
        self.result = result
        self.calls = []

    def invoke(self, payload):
        self.calls.append(payload)
        return self.result


def _make_agent(*, enabled=True, force=True, tools=None):
    """Build a CMSCompOpsAgent shell without running its heavy __init__."""
    agent = object.__new__(CMSCompOpsAgent)
    agent.enable_vector_tools = enabled
    agent._vector_tools = tools
    agent.config = {"services": {"chat_app": {"force_initial_retrieval": force}}}
    return agent


def test_injects_forced_tool_round():
    fake = _FakeTool()
    agent = _make_agent(tools=[fake])

    out = agent._inject_forced_retrieval([HumanMessage("What is GLOBUS for?")])

    # original human message + prefilled (AIMessage tool_call, ToolMessage)
    assert len(out) == 3
    assert isinstance(out[0], HumanMessage)

    ai = out[1]
    assert isinstance(ai, AIMessage)
    assert len(ai.tool_calls) == 1
    assert ai.tool_calls[0]["name"] == "search_vectorstore_hybrid"
    assert ai.tool_calls[0]["args"] == {"query": "What is GLOBUS for?"}

    tool_msg = out[2]
    assert isinstance(tool_msg, ToolMessage)
    assert tool_msg.content == "[1] GLOBUS doc snippet"
    assert tool_msg.tool_call_id == ai.tool_calls[0]["id"]

    # the real retriever tool was actually invoked with the user's query
    assert fake.calls == [{"query": "What is GLOBUS for?"}]


def test_disabled_by_flag_is_noop():
    fake = _FakeTool()
    agent = _make_agent(tools=[fake], force=False)

    msgs = [HumanMessage("What is GLOBUS for?")]
    out = agent._inject_forced_retrieval(msgs)

    assert out == msgs
    assert fake.calls == []


def test_no_vector_tools_is_noop():
    agent = _make_agent(enabled=False, tools=None)
    msgs = [HumanMessage("What is GLOBUS for?")]
    assert agent._inject_forced_retrieval(msgs) == msgs


def test_only_injects_on_fresh_human_turn():
    """If the turn does not end with a human message, do not inject."""
    fake = _FakeTool()
    agent = _make_agent(tools=[fake])

    msgs = [HumanMessage("hi"), AIMessage("hello")]
    out = agent._inject_forced_retrieval(msgs)

    assert out == msgs
    assert fake.calls == []


def test_empty_query_is_noop():
    fake = _FakeTool()
    agent = _make_agent(tools=[fake])

    msgs = [HumanMessage("   ")]
    out = agent._inject_forced_retrieval(msgs)

    assert out == msgs
    assert fake.calls == []


def test_retriever_failure_fails_open():
    """A retrieval error must not break the chat turn."""

    class _BoomTool:
        def invoke(self, payload):
            raise RuntimeError("vectorstore down")

    agent = _make_agent(tools=[_BoomTool()])
    msgs = [HumanMessage("What is GLOBUS for?")]
    out = agent._inject_forced_retrieval(msgs)

    assert out == msgs
