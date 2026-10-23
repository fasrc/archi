"""A sampling key in a provider's ``extra_kwargs`` must reach the chat client (#701).

Dev chat sets ``temperature: 0.3`` under
``services.chat_app.providers.<name>.extra_kwargs``, next to ``extra_body`` (the vLLM
``enable_thinking`` switch). Both providers that dev points at vLLM spread
``extra_kwargs`` into ``ChatOpenAI``: ``local`` in openai_compat mode and ``openai``
with a custom ``base_url``. If either spread is lost, the request carries no
temperature, and vLLM falls back to the model's own generation_config (temperature
1.0, top_p 0.95) -- the setting under which a live answer mistyped ``kempner_rtx``.

Two conversions sit between the YAML and the provider on the live paths, and each
copies ``extra_kwargs``: ``BaseReActAgent._build_provider_config`` (the agent's own
model) and ``app._build_provider_config_from_payload`` (the model a chat request
selects, which the UI dropdown always sends). The last two tests start from dev's
provider blocks and go through each one.
"""

import pytest
from langchain_openai import ChatOpenAI

from src.archi.pipelines.agents.base_react import BaseReActAgent
from src.archi.providers import get_model
from src.interfaces.chat_app.app import ChatWrapper

EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}}

# services.chat_app.providers in archi-config environments/dev.yaml at
# deploy-pin-2026-10g, without the comments and the anthropic standby.
DEV_PROVIDERS = {
    "local": {
        "enabled": True,
        "base_url": "http://localhost:8001/v1",
        "mode": "openai_compat",
        "default_model": "palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4",
        "models": ["palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4"],
        "extra_kwargs": {"extra_body": EXTRA_BODY, "temperature": 0.3},
    },
    "openai": {
        "enabled": True,
        "base_url": "http://localhost:8002/v1",
        "default_model": "palmfuture/Qwen3.8-27B-GPTQ-Int4",
        "models": ["palmfuture/Qwen3.8-27B-GPTQ-Int4"],
        "extra_kwargs": {
            "api_key": "sk-noauth",
            "extra_body": EXTRA_BODY,
            "temperature": 0.3,
        },
    },
}
PROVIDER_TYPES = sorted(DEV_PROVIDERS)


def _assert_dev_sampling(model):
    assert isinstance(model, ChatOpenAI)
    assert model.temperature == 0.3
    assert model.extra_body == EXTRA_BODY


@pytest.fixture(autouse=True)
def _no_ollama_host(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)


@pytest.mark.parametrize("provider_type", PROVIDER_TYPES)
def test_get_model_passes_extra_kwargs_temperature_to_chat_openai(
    provider_type, monkeypatch
):
    # The OpenAI SDK refuses to build a client with no key. Supply it from the
    # environment, not extra_kwargs, so a lost spread fails on temperature rather
    # than on the key.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    extra_kwargs = {"temperature": 0.3, "extra_body": EXTRA_BODY}
    if provider_type == "local":
        extra_kwargs["local_mode"] = "openai_compat"
    block = DEV_PROVIDERS[provider_type]

    model = get_model(
        provider_type,
        block["default_model"],
        {"base_url": block["base_url"], "extra_kwargs": extra_kwargs},
    )

    _assert_dev_sampling(model)


@pytest.mark.parametrize("provider_type", PROVIDER_TYPES)
def test_agent_model_built_from_the_dev_block_keeps_temperature(
    provider_type, monkeypatch
):
    # No OPENAI_API_KEY: dev's openai block builds on its own placeholder key.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider_config = BaseReActAgent._build_provider_config(
        provider_type, DEV_PROVIDERS
    )

    model = get_model(
        provider_type, DEV_PROVIDERS[provider_type]["default_model"], provider_config
    )

    _assert_dev_sampling(model)
    if provider_type == "openai":
        assert model.openai_api_key.get_secret_value() == "sk-noauth"


@pytest.mark.parametrize("provider_type", PROVIDER_TYPES)
def test_request_override_model_built_from_the_dev_block_keeps_temperature(
    provider_type, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    wrapper = object.__new__(ChatWrapper)
    wrapper.config = {"services": {"chat_app": {"providers": DEV_PROVIDERS}}}

    model, _window = wrapper._create_provider_llm(
        provider_type, DEV_PROVIDERS[provider_type]["default_model"]
    )

    _assert_dev_sampling(model)
    if provider_type == "openai":
        assert model.openai_api_key.get_secret_value() == "sk-noauth"
