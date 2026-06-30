"""Bridge the benchmark system-under-test (SUT) to its model provider.

The benchmark declares the SUT in ``services.benchmarking`` (provider, model,
ollama_url), but the ReAct agent reads its provider's ``mode``/``base_url`` from
``services.chat_app.providers.<provider>`` (resolved via ``get_full_config()``).
For a ``provider: local`` SUT pointed at an OpenAI-compatible endpoint such as the
FASRC vLLM (``.../v1``), that block defaulted to ``mode: ollama``, so the agent
built a LangChain ``ChatOllama`` and 404'd against the OpenAI-compatible API.

These helpers resolve the correct local mode and inject the provider block the
agent actually reads, so ``provider: local`` works against either a real Ollama
server or an OpenAI-compatible endpoint. See issue #73.
"""

from typing import Any, Dict, Optional


def resolve_local_mode(ollama_url: Any, explicit: Optional[str] = None) -> str:
    """Decide the local-provider mode for the SUT.

    An explicit value (from ``services.benchmarking.provider_mode``) always wins.
    Otherwise auto-detect: an endpoint ending in ``/v1`` is the OpenAI-compatible
    convention (vLLM, llama.cpp, etc.), so use ``openai_compat``; anything else is
    a native Ollama server.
    """
    if explicit:
        return str(explicit).lower()
    url = str(ollama_url or "").rstrip("/")
    return "openai_compat" if url.endswith("/v1") else "ollama"


def inject_sut_provider(
    services_config: Any,
    provider: Any,
    model: Any,
    ollama_url: Any,
    mode: str,
) -> Optional[Dict[str, Any]]:
    """Populate ``chat_app.providers.<provider>`` with the SUT base_url + mode.

    Mutates ``services_config`` in place (the agent reads this same cached static
    config). Returns the injected provider block, or ``None`` if ``services_config``
    is missing/malformed (a benchmark run should not crash on an unexpected shape).
    """
    if not isinstance(services_config, dict):
        return None
    chat = services_config.setdefault("chat_app", {})
    if not isinstance(chat, dict):
        return None
    providers = chat.setdefault("providers", {})
    if not isinstance(providers, dict):
        return None
    key = str(provider).lower()
    # Merge into any existing block: a pre-existing provider entry may carry
    # extra_kwargs (vLLM extra_body, timeouts) that BaseReActAgent forwards, and
    # overwriting the whole dict would silently drop them. The SUT keys win.
    existing = providers.get(key)
    block = existing if isinstance(existing, dict) else {}
    block.update(
        {
            "base_url": ollama_url,
            "mode": mode,
            "default_model": model,
            "enabled": True,
        }
    )
    providers[key] = block
    return block


def apply_sut_local_provider(
    benchmark_cfg: Any, static_config: Any
) -> Optional[Dict[str, Any]]:
    """Inject the SUT's local-provider block into the agent's static config.

    No-op (returns ``None``) unless ``services.benchmarking.provider`` is ``local``;
    other providers (anthropic, openai, …) build their default client and need no
    chat_app.providers entry. Tolerates a missing/malformed ``benchmark_cfg`` or
    ``static_config`` so a benchmark run never crashes on config shape.
    """
    if not isinstance(benchmark_cfg, dict):
        return None
    if str(benchmark_cfg.get("provider")).lower() != "local":
        return None
    ollama_url = benchmark_cfg.get("ollama_url")
    mode = resolve_local_mode(ollama_url, benchmark_cfg.get("provider_mode"))
    services = getattr(static_config, "services_config", None)
    return inject_sut_provider(
        services, "local", benchmark_cfg.get("model"), ollama_url, mode
    )
