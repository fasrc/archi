"""Unit tests for enforced (mandatory) vector retrieval in the ReAct agents.

The chat model can ignore a "always search first" system prompt and answer
from its own weights, leaving ``source_documents`` empty (and the chat UI
showing "Link unavailable"). To *enforce* retrieval, the agent prefills a
completed tool round — an ``AIMessage`` carrying a ``search_vectorstore_hybrid``
tool call plus the matching ``ToolMessage`` result — before the model's first
turn, so the retrieval happens regardless of the model's choice.

The forced round runs outside the per-turn search budget: the budget counts the
model's own searches, so a forced round must leave the model all of them.
"""

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import src.archi.pipelines.agents.cms_comp_ops_agent as cms_comp_ops_agent
import src.archi.pipelines.agents.fasrc_docs_agent as fasrc_docs_agent
from src.archi.pipelines.agents.cms_comp_ops_agent import CMSCompOpsAgent
from src.archi.pipelines.agents.fasrc_docs_agent import FASRCDocsAgent

TOOL_NAME = "search_vectorstore_hybrid"
QUESTION = "What is the best way to move large files onto FASSE?"


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
    # The fake has no budget, so it can serve as the forced round's own instance.
    agent._forced_vector_tool = tools[0] if tools else None
    agent.config = {"services": {"chat_app": {"force_initial_retrieval": force}}}
    return agent


class _RecordingRetriever:
    """Stands in for the vector store, not for any agent code.

    ``_update_vector_retrievers`` — the real method — still builds the tools.
    """

    def __init__(self):
        self.queries = []

    def invoke(self, query):
        self.queries.append(query)
        return [
            Document(
                page_content="FASSE Large Data Transfer with Globus",
                metadata={
                    "title": "FASSE protected data transfers",
                    "url": "https://docs.example.org/kb/fasse-protected-data-transfers/",
                },
            )
        ]


def _shell(agent, *, call_budget):
    agent.config = {
        "services": {"chat_app": {"tool_budgets": {TOOL_NAME: call_budget}}}
    }
    agent.pipeline_config = {}
    agent.dm_config = {}
    agent.enable_vector_tools = True
    agent.catalog_service = object()
    agent._active_memory = None
    agent._tool_budgets_cache = None
    return agent


def _fasrc_agent(monkeypatch, retriever, *, call_budget=2):
    """A real FASRCDocsAgent wired just far enough to build its retriever tools."""
    agent = _shell(FASRCDocsAgent.__new__(FASRCDocsAgent), call_budget=call_budget)
    monkeypatch.setattr(
        fasrc_docs_agent, "build_vector_retriever", lambda _vs, _cfg: retriever
    )
    agent._update_vector_retrievers(object())
    return agent


def _cms_agent(monkeypatch, retriever, *, call_budget=2):
    """A real CMSCompOpsAgent wired just far enough to build its retriever tools."""
    agent = _shell(CMSCompOpsAgent.__new__(CMSCompOpsAgent), call_budget=call_budget)
    monkeypatch.setattr(
        cms_comp_ops_agent, "HybridRetriever", lambda **_kwargs: retriever
    )
    agent._update_vector_retrievers(object())
    return agent


AGENT_BUILDERS = [
    pytest.param(_fasrc_agent, id="fasrc-docs"),
    pytest.param(_cms_agent, id="cms-comp-ops"),
]


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


# --- The forced round runs outside the model's per-turn search budget


@pytest.mark.parametrize("build", AGENT_BUILDERS)
def test_forced_round_leaves_the_model_its_full_search_budget(monkeypatch, build):
    retriever = _RecordingRetriever()
    agent = build(monkeypatch, retriever, call_budget=2)
    memory = agent.start_run_memory()

    out = agent._inject_forced_retrieval([HumanMessage(QUESTION)])

    assert isinstance(out[-1], ToolMessage)
    assert "Large Data Transfer with Globus" in out[-1].content
    assert retriever.queries == [QUESTION]
    # The forced round still grounds the turn: its documents reach the sources.
    assert [d.page_content for d in memory.unique_documents()] == [
        "FASSE Large Data Transfer with Globus"
    ]
    assert memory.tool_call_count(TOOL_NAME) == 0

    tool = agent._vector_tools[0]
    outcomes = [
        tool.invoke({"query": q})
        for q in ("Globus FASSE", "transfer large data to FASSE", "rsync FASSE")
    ]

    exhausted = [o.startswith("Search budget exhausted:") for o in outcomes]
    assert exhausted == [False, False, True]
    assert retriever.queries == [
        QUESTION,
        "Globus FASSE",
        "transfer large data to FASSE",
    ]


@pytest.mark.parametrize("build", AGENT_BUILDERS)
def test_forced_round_is_recorded_under_its_own_call_id(monkeypatch, build):
    agent = build(monkeypatch, _RecordingRetriever())
    memory = agent.start_run_memory()

    out = agent._inject_forced_retrieval([HumanMessage(QUESTION)])

    forced_id = out[-2].tool_calls[0]["id"]
    recorded = memory.tool_inputs_by_id()
    assert recorded[forced_id]["tool_name"] == TOOL_NAME
    assert recorded[forced_id]["tool_input"] == {"query": QUESTION}
    # Nothing is left queued for a later call with empty streamed arguments.
    assert memory.resolve_tool_input("probe", TOOL_NAME, {}) == {}


@pytest.mark.parametrize("build", AGENT_BUILDERS)
def test_a_later_model_search_keeps_its_own_arguments(monkeypatch, build):
    agent = build(monkeypatch, _RecordingRetriever())
    memory = agent.start_run_memory()
    agent._inject_forced_retrieval([HumanMessage(QUESTION)])

    # The model's first search streams its call id with empty arguments...
    memory.record_tool_calls_from_message(
        AIMessage(
            content="",
            tool_calls=[{"name": TOOL_NAME, "args": {}, "id": "model_call_1"}],
        )
    )
    # ...and only then executes with its own query.
    agent._vector_tools[0].invoke({"query": "Globus FASSE"})

    recorded = memory.tool_inputs_by_id()
    assert recorded["model_call_1"]["tool_input"] == {"query": "Globus FASSE"}


@pytest.mark.parametrize("build", AGENT_BUILDERS)
def test_missing_unbudgeted_tool_falls_back_with_a_warning(monkeypatch, caplog, build):
    retriever = _RecordingRetriever()
    agent = build(monkeypatch, retriever)
    agent._forced_vector_tool = None
    memory = agent.start_run_memory()

    with caplog.at_level("WARNING"):
        out = agent._inject_forced_retrieval([HumanMessage(QUESTION)])

    # Grounding is never silently lost: the round runs through the budgeted tool...
    assert isinstance(out[-1], ToolMessage)
    assert retriever.queries == [QUESTION]
    assert memory.tool_call_count(TOOL_NAME) == 1
    # ...and the cost is named.
    assert "consumes one of the model's" in caplog.text
    # The budgeted tool queued the question as a runtime input; the forced call
    # claims it, so the model's first call does not inherit it.
    forced_id = out[-2].tool_calls[0]["id"]
    assert memory.tool_inputs_by_id()[forced_id]["tool_input"] == {"query": QUESTION}
    assert memory.resolve_tool_input("probe", TOOL_NAME, {}) == {}


@pytest.mark.parametrize("build", AGENT_BUILDERS)
def test_disabling_vector_tools_drops_the_forced_tool(monkeypatch, build):
    agent = build(monkeypatch, _RecordingRetriever())
    assert agent._forced_vector_tool is not None
    assert agent._forced_vector_tool is not agent._vector_tools[0]

    agent.enable_vector_tools = False
    agent._update_vector_retrievers(object())

    assert agent._forced_vector_tool is None
