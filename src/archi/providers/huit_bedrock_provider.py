"""Harvard HUIT Bedrock provider implementation.

HUIT operates an Anthropic-API-compatible proxy in front of AWS Bedrock at
`https://go.apis.huit.harvard.edu/ais-bedrock-llm/v2/model/{id}/invoke`. The
proxy accepts Bedrock-native Anthropic JSON bodies (`anthropic_version` field,
top-level `system`/`messages`/`max_tokens`) and is authenticated with an
`x-api-key` header — not Anthropic's standard `x-api-key` /
`anthropic-version` pair, but a Harvard-issued API gateway token.

The proxy is in scope for Harvard-affiliated traffic only, so all data stays
inside HUIT's compliance boundary. We use it as the RAGAS judge LLM (Phase 3
of adopt-argilla-benchmark-platform) and may use it for SUT comparisons too.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from src.archi.providers.base import (
    BaseProvider,
    ModelInfo,
    ProviderConfig,
    ProviderType,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


DEFAULT_HUIT_BEDROCK_BASE_URL = "https://go.apis.huit.harvard.edu/ais-bedrock-llm/v2"
DEFAULT_HUIT_BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
DEFAULT_ANTHROPIC_VERSION = "bedrock-2023-05-31"


DEFAULT_HUIT_BEDROCK_MODELS = [
    ModelInfo(
        id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        name="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        display_name="Claude Sonnet 4.5 (HUIT Bedrock, pinned)",
        context_window=200000,
        supports_tools=False,
        supports_streaming=False,
        supports_vision=False,
        max_output_tokens=8192,
    ),
    ModelInfo(
        id="us.anthropic.claude-opus-4-20250514-v1:0",
        name="us.anthropic.claude-opus-4-20250514-v1:0",
        display_name="Claude Opus 4 (HUIT Bedrock)",
        context_window=200000,
        supports_tools=False,
        supports_streaming=False,
        supports_vision=False,
        max_output_tokens=8192,
    ),
    ModelInfo(
        id="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
        name="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
        display_name="Claude 3.5 Sonnet (HUIT Bedrock)",
        context_window=200000,
        supports_tools=False,
        supports_streaming=False,
        supports_vision=False,
        max_output_tokens=8192,
    ),
]


def _convert_messages(messages: List[BaseMessage]) -> tuple[str, List[Dict[str, Any]]]:
    """Convert a LangChain message sequence into the Bedrock-native Anthropic
    body shape: a single ``system`` string and a list of ``{role, content}``
    dicts.

    Tool messages are coerced into ``user`` turns with a ``tool_result`` block;
    only text content is supported (no vision, no streaming).
    """
    system_parts: List[str] = []
    converted: List[Dict[str, Any]] = []

    for msg in messages:
        if isinstance(msg, SystemMessage):
            system_parts.append(str(msg.content))
        elif isinstance(msg, HumanMessage):
            converted.append({"role": "user", "content": str(msg.content)})
        elif isinstance(msg, AIMessage):
            converted.append({"role": "assistant", "content": str(msg.content)})
        elif isinstance(msg, ToolMessage):
            converted.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": getattr(msg, "tool_call_id", "unknown"),
                    "content": str(msg.content),
                }],
            })
        else:
            converted.append({"role": "user", "content": str(msg.content)})

    return "\n\n".join(system_parts), converted


class HuitBedrockChat(BaseChatModel):
    """LangChain chat model that talks to HUIT's Bedrock-Anthropic proxy.

    The proxy is synchronous-only and does not stream; ``streaming=True``
    in ``extra_kwargs`` is silently ignored.
    """

    model_id: str = Field(default=DEFAULT_HUIT_BEDROCK_MODEL)
    base_url: str = Field(default=DEFAULT_HUIT_BEDROCK_BASE_URL)
    api_key: str = Field(default="")
    max_tokens: int = Field(default=4096)
    temperature: float = Field(default=0.0)
    anthropic_version: str = Field(default=DEFAULT_ANTHROPIC_VERSION)
    request_timeout: int = Field(default=120)

    @property
    def _llm_type(self) -> str:
        return "huit_bedrock"

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "base_url": self.base_url,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        if not self.api_key:
            raise ValueError(
                "HUIT_API_KEY is not set; cannot call HUIT Bedrock. "
                "Add it to ~/.archi/.env.benchmark and redeploy."
            )

        system, converted_messages = _convert_messages(messages)

        body: Dict[str, Any] = {
            "anthropic_version": self.anthropic_version,
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "messages": converted_messages,
        }
        if system:
            body["system"] = system
        if stop:
            body["stop_sequences"] = stop

        url = f"{self.base_url.rstrip('/')}/model/{self.model_id}/invoke"
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        response = requests.post(
            url,
            headers=headers,
            data=json.dumps(body),
            timeout=self.request_timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"HUIT Bedrock request failed: HTTP {response.status_code} — {response.text[:500]}"
            )

        payload = response.json()
        content_blocks = payload.get("content", [])
        text = "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )

        usage = payload.get("usage", {}) or {}
        usage_metadata = {
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "total_tokens": int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0),
        }

        ai_message = AIMessage(content=text, usage_metadata=usage_metadata)  # type: ignore[arg-type]
        return ChatResult(generations=[ChatGeneration(message=ai_message)])


class HuitBedrockProvider(BaseProvider):
    """Provider for Harvard HUIT's Bedrock-Anthropic proxy."""

    provider_type = ProviderType.HUIT_BEDROCK
    display_name = "Harvard HUIT Bedrock"

    def __init__(self, config: Optional[ProviderConfig] = None):
        if config is None:
            config = ProviderConfig(
                provider_type=ProviderType.HUIT_BEDROCK,
                api_key_env="HUIT_API_KEY",
                base_url=DEFAULT_HUIT_BEDROCK_BASE_URL,
                models=DEFAULT_HUIT_BEDROCK_MODELS,
                default_model=DEFAULT_HUIT_BEDROCK_MODEL,
            )
        if not config.api_key_env:
            config.api_key_env = "HUIT_API_KEY"
        super().__init__(config)

    def get_chat_model(self, model_name: str, **kwargs) -> HuitBedrockChat:
        base_url = self.config.base_url or DEFAULT_HUIT_BEDROCK_BASE_URL
        chat_kwargs: Dict[str, Any] = {
            "model_id": model_name,
            "base_url": base_url,
            "api_key": self._api_key or "",
        }
        for key in ("max_tokens", "temperature", "anthropic_version", "request_timeout"):
            if key in self.config.extra_kwargs:
                chat_kwargs[key] = self.config.extra_kwargs[key]
            if key in kwargs:
                chat_kwargs[key] = kwargs[key]
        return HuitBedrockChat(**chat_kwargs)

    def list_models(self) -> List[ModelInfo]:
        if self.config.models:
            return self.config.models
        return DEFAULT_HUIT_BEDROCK_MODELS
