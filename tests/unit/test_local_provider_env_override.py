"""OLLAMA_HOST must scope its override to `ollama` mode (issue #450).

`LocalProvider.__init__` used to apply `OLLAMA_HOST` — and, when no `base_url` was
configured, the Ollama default — regardless of local mode. An `openai_compat` provider
pointed at a vLLM/LM Studio endpoint would silently get redirected to an Ollama daemon
that does not serve the OpenAI route. These tests pin the mode-aware resolution from
`openspec/changes/fix-issue-450-ollama-host-scope/specs/local-provider-endpoint-resolution/spec.md`.
"""

import pytest

from src.archi.providers.base import ProviderConfig, ProviderType
from src.archi.providers.local_provider import LocalProvider


def _build(monkeypatch, ollama_host, base_url, mode):
    if ollama_host is None:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
    else:
        monkeypatch.setenv("OLLAMA_HOST", ollama_host)

    config = ProviderConfig(
        provider_type=ProviderType.LOCAL,
        base_url=base_url,
        extra_kwargs={"local_mode": mode},
    )
    return LocalProvider(config)


def test_openai_compat_configured_base_url_ignores_ollama_host(monkeypatch):
    """Fails today: OLLAMA_HOST overwrites a configured openai_compat base_url."""
    provider = _build(
        monkeypatch,
        "http://ollama-box:11434",
        "http://gpu-vllm:8000/v1",
        "openai_compat",
    )
    assert provider.config.base_url == "http://gpu-vllm:8000/v1"


def test_ollama_configured_base_url_yields_to_ollama_host(monkeypatch):
    provider = _build(
        monkeypatch, "http://ollama-box:11434", "http://gpu-vllm:8000/v1", "ollama"
    )
    assert provider.config.base_url == "http://ollama-box:11434"


def test_openai_compat_fallback_ignores_ollama_host(monkeypatch):
    """Fails today: with no configured base_url, OLLAMA_HOST leaks into openai_compat."""
    provider = _build(monkeypatch, "http://ollama-box:11434", None, "openai_compat")
    assert provider.config.base_url == LocalProvider.DEFAULT_OPENAI_COMPAT_BASE_URL


def test_ollama_fallback_prefers_ollama_host(monkeypatch):
    provider = _build(monkeypatch, "http://ollama-box:11434", None, "ollama")
    assert provider.config.base_url == "http://ollama-box:11434"


def test_openai_compat_configured_base_url_survives_unset_ollama_host(monkeypatch):
    provider = _build(monkeypatch, None, "http://gpu-vllm:8000/v1", "openai_compat")
    assert provider.config.base_url == "http://gpu-vllm:8000/v1"


def test_ollama_configured_base_url_survives_unset_ollama_host(monkeypatch):
    provider = _build(monkeypatch, None, "http://gpu-vllm:8000/v1", "ollama")
    assert provider.config.base_url == "http://gpu-vllm:8000/v1"


def test_openai_compat_fallback_with_unset_ollama_host(monkeypatch):
    """Fails today: the fallback is the Ollama default instead of the openai-compat one.

    #450 chose the openai-compat default over the Ollama host for this mode.
    """
    provider = _build(monkeypatch, None, None, "openai_compat")
    assert provider.config.base_url == LocalProvider.DEFAULT_OPENAI_COMPAT_BASE_URL


def test_ollama_fallback_with_unset_ollama_host(monkeypatch):
    provider = _build(monkeypatch, None, None, "ollama")
    assert provider.config.base_url == LocalProvider.DEFAULT_OLLAMA_BASE_URL


def test_openai_compat_empty_ollama_host_never_overrides(monkeypatch):
    provider = _build(monkeypatch, "", "http://gpu-vllm:8000/v1", "openai_compat")
    assert provider.config.base_url == "http://gpu-vllm:8000/v1"


def test_openai_compat_configured_base_url_is_normalized_and_ignores_ollama_host(
    monkeypatch,
):
    provider = _build(monkeypatch, None, "gpu-vllm:8000/v1", "openai_compat")
    assert provider.config.base_url == "http://gpu-vllm:8000/v1"
