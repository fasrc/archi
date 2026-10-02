"""OpenAI-compatible providers must stream token usage (issue #582).

`langchain_openai.ChatOpenAI` turns `stream_usage` on by default only when no custom
`base_url` is set. Every archi OpenAI-compatible provider points at a custom endpoint
(vLLM, LM Studio, the CERN gateway), so without an explicit opt-in the streamed
responses carry no usage chunk and the evaluation records 0 tokens for each call.
"""

import pytest

from src.archi.providers.base import ProviderConfig, ProviderType
from src.archi.providers.cern_litellm_provider import CERNLiteLLMProvider
from src.archi.providers.local_provider import LocalProvider


def _local(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    return LocalProvider(
        ProviderConfig(
            provider_type=ProviderType.LOCAL,
            base_url="http://gpu-vllm:8000/v1",
            extra_kwargs={"local_mode": "openai_compat"},
        )
    )


def _cern():
    return CERNLiteLLMProvider(
        ProviderConfig(
            provider_type=ProviderType.CERN_LITELLM,
            base_url="https://llm-gateway.example/v1",
            api_key="test-key",
        )
    )


@pytest.fixture(params=["local", "cern"])
def build(request, monkeypatch):
    if request.param == "local":
        provider = _local(monkeypatch)
    else:
        provider = _cern()
    return lambda **kwargs: provider.get_chat_model("some-model", **kwargs)


def test_streams_usage_with_custom_base_url(build):
    model = build()
    assert model._should_stream_usage() is True


def test_explicit_caller_stream_usage_wins(build):
    model = build(stream_usage=False)
    assert model.stream_usage is False
    assert model._should_stream_usage() is False
