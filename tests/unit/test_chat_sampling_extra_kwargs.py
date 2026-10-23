"""A sampling key in a provider's ``extra_kwargs`` must reach the chat client (#701).

Dev chat sets ``temperature: 0.3`` under
``services.chat_app.providers.<name>.extra_kwargs``, next to ``extra_body`` (the vLLM
``enable_thinking`` switch). Both providers that dev points at vLLM spread
``extra_kwargs`` into ``ChatOpenAI``: ``local`` in openai_compat mode and ``openai``
with a custom ``base_url``. If either spread is lost, the request carries no
temperature, and vLLM falls back to the model's own generation_config (temperature
1.0, top_p 0.95) -- the setting under which a live answer mistyped ``kempner_rtx``.
"""

import pytest
from langchain_openai import ChatOpenAI

from src.archi.providers import get_model

EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}}

# provider type -> (model name, provider_config) in the shape the chat agent hands to
# get_model (BaseReActAgent._build_provider_config moves dev.yaml's `mode` into
# extra_kwargs as local_mode).
DEV_PROVIDERS = {
    "local": (
        "palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4",
        {
            "base_url": "http://localhost:8001/v1",
            "extra_kwargs": {
                "local_mode": "openai_compat",
                "temperature": 0.3,
                "extra_body": EXTRA_BODY,
            },
        },
    ),
    "openai": (
        "palmfuture/Qwen3.8-27B-GPTQ-Int4",
        {
            "base_url": "http://localhost:8002/v1",
            "extra_kwargs": {"temperature": 0.3, "extra_body": EXTRA_BODY},
        },
    ),
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    # The OpenAI SDK refuses to build a client with no key. Supply it from the
    # environment, not extra_kwargs, so a lost spread fails on temperature rather
    # than on the key.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


@pytest.mark.parametrize("provider_type", sorted(DEV_PROVIDERS))
def test_extra_kwargs_temperature_reaches_chat_openai(provider_type):
    model_name, provider_config = DEV_PROVIDERS[provider_type]

    model = get_model(provider_type, model_name, provider_config)

    assert isinstance(model, ChatOpenAI)
    assert model.temperature == 0.3
    assert model.extra_body == EXTRA_BODY
