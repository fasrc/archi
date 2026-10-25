"""Unit tests for BaseReActAgent callback forwarding on the invoke path (#597 D1).

Every LLM call on invoke() -- history summarizer, context-overflow retry, and
recursion-limit wrap-up -- must reach the caller's callbacks.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError

from src.archi.pipelines.agents.base_react import BaseReActAgent
from src.utils.llm_usage import UsageRecorder

# Same overflow message used by test_react_agent_context_overflow.py.
VLLM_OVERFLOW_MSG = (
    "You passed 102420 input tokens and requested 0 output tokens. However, the "
    "model's context length is only 32768, resulting in a maximum input length of "
    "32768. Please reduce the length of the input messages. "
    "(parameter=input_tokens, value=102420)"
)


def _usage_msg(
    content: str = "wrap-up answer",
    input_tokens: int = 12,
    output_tokens: int = 4,
) -> AIMessage:
    return AIMessage(
        content=content,
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )


class _TestableAgent(BaseReActAgent):
    """Subclass that skips LLM/prompt/LangGraph init so we can test invoke() alone."""

    def __init__(
        self,
        agent_llm: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.config = config or {}
        self.archi_config = {}
        self.dm_config = {}
        self.pipeline_config = {}
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
        self.agent = MagicMock()  # non-None so invoke() skips refresh_agent
        self.agent_llm = agent_llm if agent_llm is not None else MagicMock()
        self.agent_prompt = "test prompt"
        self.mcp_client = None
        self.prepare_kwargs_log: List[Dict[str, Any]] = []

    def _prepare_agent_inputs(self, **kwargs: Any) -> Dict[str, Any]:
        # Record kwargs for inspection, then bypass RunMemory / retriever wiring.
        self.prepare_kwargs_log.append(dict(kwargs))
        return {"messages": [HumanMessage(content="how do I do X and Y?")]}


# --- Scenario: The recursion-limit wrap-up is counted -----------------------


def test_recursion_limit_wrap_up_counted():
    llm = GenericFakeChatModel(
        messages=iter([_usage_msg(input_tokens=12, output_tokens=4)])
    )
    agent = _TestableAgent(agent_llm=llm)
    agent.agent.invoke = MagicMock(side_effect=GraphRecursionError())
    recorder = UsageRecorder("local", "default-m")

    agent.invoke(callbacks=[recorder])

    snap = recorder.snapshot()
    assert snap is not None
    assert snap["calls"] == 1
    assert snap["input_tokens"] == 12
    assert snap["output_tokens"] == 4


# --- Scenario: A run without callbacks is unchanged -------------------------


def test_no_callbacks_wrap_up_unchanged():
    llm = GenericFakeChatModel(messages=iter([_usage_msg()]))
    agent = _TestableAgent(agent_llm=llm)
    agent.agent.invoke = MagicMock(side_effect=GraphRecursionError())

    out = agent.invoke()

    assert out is not None
    assert out.answer


# --- Scenario: The context-overflow retry receives the callbacks -------------


def test_overflow_retry_config_has_callbacks():
    agent = _TestableAgent()
    recorder = UsageRecorder("local", "default-m")
    agent.agent.invoke = MagicMock(
        side_effect=[
            Exception(VLLM_OVERFLOW_MSG),
            {"messages": [AIMessage(content="ok")]},
        ]
    )

    agent.invoke(callbacks=[recorder])

    retry_config = agent.agent.invoke.call_args_list[1].args[1]
    assert retry_config["recursion_limit"] == 10
    assert retry_config["callbacks"] == [recorder]


# --- Scenario: The history summarizer is counted ----------------------------


def test_compress_history_summarizer_counted():
    llm = GenericFakeChatModel(messages=iter([_usage_msg()]))
    agent = _TestableAgent(agent_llm=llm)
    recorder = UsageRecorder("local", "default-m")
    # More than 4 messages triggers the summarizer.
    history = [HumanMessage(content=f"message {i}") for i in range(6)]

    agent._compress_history(history, callbacks=[recorder])

    assert recorder.snapshot()["calls"] == 1


def test_invoke_passes_callbacks_to_prepare_agent_inputs():
    agent = _TestableAgent()
    agent.agent.invoke = MagicMock(
        return_value={"messages": [AIMessage(content="done")]}
    )
    recorder = UsageRecorder("local", "default-m")

    agent.invoke(callbacks=[recorder])

    assert len(agent.prepare_kwargs_log) == 1
    assert agent.prepare_kwargs_log[0].get("callbacks") == [recorder]


# --- Scenario: The async wrap-up is counted ---------------------------------


def test_async_wrap_up_counted():
    llm = GenericFakeChatModel(
        messages=iter([_usage_msg(input_tokens=5, output_tokens=3)])
    )
    agent = _TestableAgent(agent_llm=llm)
    recorder = UsageRecorder("local", "default-m")

    asyncio.run(
        agent._generate_wrap_up_message_async(
            recursion_limit=10,
            error=GraphRecursionError(),
            latest_messages=[],
            agent_inputs={},
            callbacks=[recorder],
        )
    )

    snap = recorder.snapshot()
    assert snap is not None
    assert snap["calls"] == 1
