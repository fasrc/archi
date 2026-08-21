"""The two ReAct-agent seams the QA-evaluation runtime drives.

``ArchiAgentRuntime`` (``src/evaluation/qa/runtime.py``) collects the tool trace of
one attempt by handing ``pipeline.invoke`` a callback handler, and refuses to run an
attempt whose spec selected ``mcp`` while no MCP tool loaded. The agent must expose
both: ``invoke`` forwards supplied callbacks into the compiled agent's invoke
configuration, and ``loaded_mcp_tools`` reports the loaded MCP tool set. Without the
forward the trace comes back empty and every attempt looks tool-free.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence
from unittest.mock import patch

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from src.archi.pipelines.agents import base_react
from src.archi.pipelines.agents.base_react import BaseReActAgent


class _RecordingModel(BaseChatModel):
    """Fake chat model: records each request instead of calling a provider."""

    sink: Any = None

    @property
    def _llm_type(self) -> str:
        return "recording-fake"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.sink.append(list(messages))
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="fake answer"))]
        )


class _RecordingGraph:
    """Stand-in for the compiled LangGraph agent that captures its invoke config."""

    def __init__(self) -> None:
        self.configs: List[Optional[Dict[str, Any]]] = []

    def invoke(
        self, inputs: Dict[str, Any], config: Optional[Dict[str, Any]] = None, **kwargs
    ) -> Dict[str, Any]:
        self.configs.append(config)
        return {"messages": [AIMessage(content="fake answer")]}


def _agent(tools: Sequence[str] = ()) -> BaseReActAgent:
    """Build a real agent whose only fake is the provider-facing chat model."""
    spec = SimpleNamespace(tools=list(tools), prompt="Answer directly.")
    model = _RecordingModel(sink=[])
    with patch.object(base_react, "get_model", lambda *args, **kwargs: model):
        return BaseReActAgent(
            config={"services": {"chat_app": {"providers": {}}}},
            agent_spec=spec,
            default_provider="fake",
            default_model="fake-model",
        )


def test_invoke_forwards_supplied_callbacks_to_the_compiled_agent():
    agent = _agent()
    graph = _RecordingGraph()
    # Assigned before invoke: the tool set is unchanged, so refresh_agent keeps it.
    agent.agent = graph
    handler = BaseCallbackHandler()

    agent.invoke(
        history=[("User", "how much capacity is free?")],
        vectorstore=None,
        callbacks=[handler],
    )

    [config] = graph.configs
    assert config is not None
    assert handler in (config.get("callbacks") or [])


def test_invoke_without_callbacks_leaves_the_invoke_config_alone():
    agent = _agent()
    graph = _RecordingGraph()
    agent.agent = graph

    agent.invoke(history=[("User", "how much capacity is free?")], vectorstore=None)

    [config] = graph.configs
    assert config is not None
    assert "callbacks" not in config


def test_loaded_mcp_tools_is_empty_before_any_mcp_tool_loads():
    assert _agent(tools=["mcp"]).loaded_mcp_tools == []


def test_loaded_mcp_tools_reports_the_loaded_tools_without_sharing_state():
    agent = _agent(tools=["mcp"])
    loaded = SimpleNamespace(name="search")
    agent._mcp_tools = [loaded]

    reported = agent.loaded_mcp_tools

    assert reported == [loaded]
    reported.clear()
    assert agent.loaded_mcp_tools == [loaded]
