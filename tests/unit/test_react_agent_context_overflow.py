"""Unit tests for BaseReActAgent's context-window overflow resilience.

Covers the openspec change `harden-benchmark-and-agent-resilience` (#2):
- `_is_context_overflow_error` recognises OpenAI-compatible (vLLM) phrasing, not just
  the OpenAI-hosted phrasing (the exact message that crashed ragas-bench Q7).
- `invoke()` degrades gracefully on a context-length overflow instead of crashing,
  mirroring the guard `stream()`/`astream()` already have.
- Only genuine context-length overflows are degraded; other errors re-raise.
- A recovered trimmed-context retry stays marked (`context_overflow_retry`) so it is
  distinguishable from a clean, full-context success (Codex F3 / design D6).

End-to-end LangGraph (real LLM, real tool routing) is out of scope for unit tests.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.archi.pipelines.agents.base_react import BaseReActAgent

# The exact provider error message that crashed ragas-bench question 7/9: a vLLM
# (OpenAI-compatible) context-length overflow. None of the pre-change detector
# substrings match this phrasing.
VLLM_OVERFLOW_MSG = (
    "You passed 102420 input tokens and requested 0 output tokens. However, the "
    "model's context length is only 32768, resulting in a maximum input length of "
    "32768. Please reduce the length of the input messages. "
    "(parameter=input_tokens, value=102420)"
)


class _TestableAgent(BaseReActAgent):
    """Subclass that skips LLM/prompt/LangGraph init so we can test invoke() alone."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
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
        self.agent_llm = MagicMock()
        self.agent_prompt = "test prompt"
        self.mcp_client = None

    def _prepare_agent_inputs(self, **kwargs: Any) -> Dict[str, Any]:
        # Bypass RunMemory / retriever wiring; invoke() only needs a messages dict.
        return {"messages": [HumanMessage(content="how do I do X and Y?")]}


# --- detector: recognises vLLM phrasing (1.1) and no regression (1.3) -------


def test_detects_vllm_context_length_message():
    assert (
        BaseReActAgent._is_context_overflow_error(Exception(VLLM_OVERFLOW_MSG)) is True
    )


def test_detects_existing_openai_hosted_phrasings():
    assert (
        BaseReActAgent._is_context_overflow_error(
            Exception("Error code: 400 - context_length_exceeded")
        )
        is True
    )
    assert (
        BaseReActAgent._is_context_overflow_error(
            Exception("This model's maximum context length is 8192 tokens")
        )
        is True
    )


def test_non_overflow_error_not_detected():
    assert (
        BaseReActAgent._is_context_overflow_error(
            Exception("Error code: 400 - invalid value for 'temperature'")
        )
        is False
    )


# --- invoke() degrades on overflow (1.4) / re-raises otherwise (1.6) --------


def test_invoke_degrades_on_overflow_without_recovery():
    agent = _TestableAgent()
    # Every model call (initial + trimmed retry) overflows -> fallback message path.
    agent.agent.invoke = MagicMock(side_effect=Exception(VLLM_OVERFLOW_MSG))

    out = agent.invoke()

    assert out is not None
    assert out.metadata.get("error_type") == "context_overflow"


def test_invoke_reraises_non_overflow_error():
    agent = _TestableAgent()
    agent.agent.invoke = MagicMock(
        side_effect=Exception("Error code: 400 - invalid value for 'temperature'")
    )

    with pytest.raises(Exception, match="temperature"):
        agent.invoke()


# --- recovered retry stays marked degraded (1.7 / F3 / D6) ------------------


def test_invoke_marks_recovered_retry_as_degraded():
    agent = _TestableAgent()
    # Initial call overflows; the trimmed-context retry succeeds with an answer.
    agent.agent.invoke = MagicMock(
        side_effect=[
            Exception(VLLM_OVERFLOW_MSG),
            {"messages": [AIMessage(content="recovered answer")]},
        ]
    )

    out = agent.invoke()

    assert out.answer == "recovered answer"
    assert out.metadata.get("context_overflow_retry") is True
