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


def test_null_mode_resolves_ollama_default_endpoint(monkeypatch):
    """A null local_mode gets the Ollama client, so it gets the Ollama endpoint."""
    provider = _build(monkeypatch, None, None, None)
    assert provider.config.base_url == LocalProvider.DEFAULT_OLLAMA_BASE_URL


def test_null_mode_honors_ollama_host(monkeypatch):
    provider = _build(monkeypatch, "http://ollama-box:11434", None, None)
    assert provider.config.base_url == "http://ollama-box:11434"


def test_null_mode_configured_base_url_yields_to_ollama_host(monkeypatch):
    provider = _build(
        monkeypatch, "http://ollama-box:11434", "http://gpu-vllm:8000/v1", None
    )
    assert provider.config.base_url == "http://ollama-box:11434"


def test_null_mode_falls_back_to_the_ollama_default(monkeypatch):
    """``extra_kwargs: {local_mode: null}`` shadows the default, so the key holds None."""
    provider = _build(monkeypatch, None, None, None)
    assert provider.config.base_url == LocalProvider.DEFAULT_OLLAMA_BASE_URL


def test_null_mode_endpoint_and_client_agree(monkeypatch):
    """The endpoint rule and the client dispatch must read the mode the same way.

    A null local_mode resolves the Ollama endpoint and dispatches the Ollama client;
    the two must agree so the client and the endpoint speak the same protocol.
    """
    provider = _build(monkeypatch, None, None, None)
    calls = []
    monkeypatch.setattr(
        LocalProvider,
        "_get_ollama_model",
        lambda self, name, **kw: calls.append("ollama"),
    )
    monkeypatch.setattr(
        LocalProvider,
        "_get_openai_compat_model",
        lambda self, name, **kw: calls.append("openai_compat"),
    )
    provider.get_chat_model("some-model")
    assert calls == ["ollama"]
    assert provider.config.base_url == LocalProvider.DEFAULT_OLLAMA_BASE_URL


def test_openai_compat_endpoint_and_client_agree(monkeypatch):
    """The counterpart: the canonical compat mode keeps its endpoint and its client."""
    provider = _build(monkeypatch, None, None, "openai_compat")
    calls = []
    monkeypatch.setattr(
        LocalProvider,
        "_get_ollama_model",
        lambda self, name, **kw: calls.append("ollama"),
    )
    monkeypatch.setattr(
        LocalProvider,
        "_get_openai_compat_model",
        lambda self, name, **kw: calls.append("openai_compat"),
    )
    provider.get_chat_model("some-model")
    assert calls == ["openai_compat"]
    assert provider.config.base_url == LocalProvider.DEFAULT_OPENAI_COMPAT_BASE_URL


def _record_dispatch(monkeypatch):
    """Capture which client ``get_chat_model`` builds, and with which kwargs."""
    calls = []
    monkeypatch.setattr(
        LocalProvider,
        "_get_ollama_model",
        lambda self, name, **kw: calls.append(("ollama", kw)),
    )
    monkeypatch.setattr(
        LocalProvider,
        "_get_openai_compat_model",
        lambda self, name, **kw: calls.append(("openai_compat", kw)),
    )
    return calls


def test_a_per_call_mode_that_agrees_is_accepted(monkeypatch):
    provider = _build(monkeypatch, None, None, "openai_compat")
    calls = _record_dispatch(monkeypatch)
    provider.get_chat_model("some-model", local_mode="openai_compat")
    assert [mode for mode, _ in calls] == ["openai_compat"]


def test_a_per_call_mode_never_reaches_the_client_kwargs(monkeypatch):
    provider = _build(monkeypatch, None, None, "ollama")
    calls = _record_dispatch(monkeypatch)
    provider.get_chat_model("some-model", local_mode="ollama")
    assert "local_mode" not in calls[0][1]


def test_a_per_call_mode_cannot_move_the_dialect_off_the_compat_endpoint(monkeypatch):
    """The endpoint is resolved once, at construction, from the stored mode.

    A per-call mode that disagreed used to switch the dialect while leaving that
    endpoint alone — an Ollama client against `http://localhost:8000/v1`. No caller in
    the repository passes this keyword, so the override is closed rather than taught to
    re-resolve the endpoint.
    """
    provider = _build(monkeypatch, None, None, "openai_compat")
    assert provider.config.base_url == LocalProvider.DEFAULT_OPENAI_COMPAT_BASE_URL
    with pytest.raises(ValueError) as excinfo:
        provider.get_chat_model("some-model", local_mode="ollama")
    assert "openai_compat" in str(excinfo.value)
    assert "ollama" in str(excinfo.value)


def test_a_per_call_mode_cannot_move_the_dialect_onto_the_ollama_endpoint(monkeypatch):
    provider = _build(monkeypatch, None, None, "ollama")
    assert provider.config.base_url == LocalProvider.DEFAULT_OLLAMA_BASE_URL
    with pytest.raises(ValueError):
        provider.get_chat_model("some-model", local_mode="openai_compat")


def test_a_per_call_mode_cannot_promote_a_null_mode(monkeypatch):
    """A per-call local_mode cannot override a provider built for the null (Ollama) mode."""
    provider = _build(monkeypatch, None, None, None)
    with pytest.raises(ValueError):
        provider.get_chat_model("some-model", local_mode="openai_compat")


def test_an_absent_per_call_mode_still_uses_the_stored_mode(monkeypatch):
    provider = _build(monkeypatch, None, None, "openai_compat")
    calls = _record_dispatch(monkeypatch)
    provider.get_chat_model("some-model")
    assert [mode for mode, _ in calls] == ["openai_compat"]


# === Probe matrix from issue #463 ===


def test_mixed_case_openai_compat_builds_openai_client(monkeypatch):
    """OpenAI_Compat is canonicalized to openai_compat — fails today."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    config = ProviderConfig(
        provider_type=ProviderType.LOCAL,
        base_url="http://gpu-vllm:8000/v1",
        extra_kwargs={"local_mode": "OpenAI_Compat"},
    )
    provider = LocalProvider(config)
    calls = _record_dispatch(monkeypatch)
    provider.get_chat_model("m")
    assert [mode for mode, _ in calls] == ["openai_compat"]


def test_all_caps_openai_compat_builds_openai_client(monkeypatch):
    """OPENAI_COMPAT is canonicalized to openai_compat — fails today."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    config = ProviderConfig(
        provider_type=ProviderType.LOCAL,
        base_url="http://gpu-vllm:8000/v1",
        extra_kwargs={"local_mode": "OPENAI_COMPAT"},
    )
    provider = LocalProvider(config)
    calls = _record_dispatch(monkeypatch)
    provider.get_chat_model("m")
    assert [mode for mode, _ in calls] == ["openai_compat"]


def test_whitespace_trimmed_openai_compat_builds_openai_client(monkeypatch):
    """Leading/trailing whitespace is stripped before client dispatch — fails today."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    config = ProviderConfig(
        provider_type=ProviderType.LOCAL,
        base_url="http://gpu-vllm:8000/v1",
        extra_kwargs={"local_mode": " openai_compat "},
    )
    provider = LocalProvider(config)
    calls = _record_dispatch(monkeypatch)
    provider.get_chat_model("m")
    assert [mode for mode, _ in calls] == ["openai_compat"]


def test_mixed_case_ollama_builds_ollama_client(monkeypatch):
    """Ollama (mixed case) is canonicalized to ollama before client dispatch."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    config = ProviderConfig(
        provider_type=ProviderType.LOCAL,
        base_url=None,
        extra_kwargs={"local_mode": "Ollama"},
    )
    provider = LocalProvider(config)
    calls = _record_dispatch(monkeypatch)
    provider.get_chat_model("m")
    assert [mode for mode, _ in calls] == ["ollama"]


def test_hyphenated_openai_compat_raises_at_construction(monkeypatch):
    """openai-compat is not a recognized mode; raises ValueError at construction — fails today."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    config = ProviderConfig(
        provider_type=ProviderType.LOCAL,
        base_url=None,
        extra_kwargs={"local_mode": "openai-compat"},
    )
    with pytest.raises(ValueError):
        LocalProvider(config)


def test_vllm_mode_raises_at_construction(monkeypatch):
    """vllm is not a recognized mode; raises ValueError at construction — fails today."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    config = ProviderConfig(
        provider_type=ProviderType.LOCAL,
        base_url=None,
        extra_kwargs={"local_mode": "vllm"},
    )
    with pytest.raises(ValueError):
        LocalProvider(config)


def test_empty_string_mode_raises_at_construction(monkeypatch):
    """An empty string is not a recognized mode; raises ValueError at construction — fails today."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    config = ProviderConfig(
        provider_type=ProviderType.LOCAL,
        base_url=None,
        extra_kwargs={"local_mode": ""},
    )
    with pytest.raises(ValueError):
        LocalProvider(config)


def test_null_local_mode_key_builds_ollama_client(monkeypatch):
    """A present-but-None local_mode key must build the Ollama client."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    config = ProviderConfig(
        provider_type=ProviderType.LOCAL,
        base_url=None,
        extra_kwargs={"local_mode": None},
    )
    provider = LocalProvider(config)
    calls = _record_dispatch(monkeypatch)
    provider.get_chat_model("m")
    assert [mode for mode, _ in calls] == ["ollama"]


def test_absent_local_mode_key_builds_ollama_client(monkeypatch):
    """When local_mode is absent from extra_kwargs, the Ollama client is used."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    config = ProviderConfig(
        provider_type=ProviderType.LOCAL,
        base_url=None,
        extra_kwargs={},
    )
    provider = LocalProvider(config)
    calls = _record_dispatch(monkeypatch)
    provider.get_chat_model("m")
    assert [mode for mode, _ in calls] == ["ollama"]


def test_compat_no_base_url_uses_default_and_builds_openai_client(monkeypatch):
    """An openai_compat provider with no base_url resolves the default and uses the OpenAI client."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    config = ProviderConfig(
        provider_type=ProviderType.LOCAL,
        base_url=None,
        extra_kwargs={"local_mode": "openai_compat"},
    )
    provider = LocalProvider(config)
    assert provider.config.base_url == LocalProvider.DEFAULT_OPENAI_COMPAT_BASE_URL
    calls = _record_dispatch(monkeypatch)
    provider.get_chat_model("m")
    assert [mode for mode, _ in calls] == ["openai_compat"]


def test_null_local_mode_list_models_attempts_ollama_discovery(monkeypatch):
    """A present-but-None local_mode key must send list_models down the Ollama path — fails today."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    config = ProviderConfig(
        provider_type=ProviderType.LOCAL,
        base_url=None,
        extra_kwargs={"local_mode": None},
    )
    provider = LocalProvider(config)
    fetch_calls = []
    monkeypatch.setattr(
        LocalProvider,
        "_fetch_ollama_models",
        lambda self: fetch_calls.append(True) or [],
    )
    provider.list_models()
    assert fetch_calls, "list_models should have attempted Ollama discovery"


def test_null_local_mode_validate_connection_probes_ollama_tags(monkeypatch):
    """A present-but-None local_mode key must send validate_connection to /api/tags — fails today."""
    import urllib.request

    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    config = ProviderConfig(
        provider_type=ProviderType.LOCAL,
        base_url="http://ollama-host:11434",
        extra_kwargs={"local_mode": None},
    )
    provider = LocalProvider(config)
    probed_urls = []

    class _FakeResponse:
        status = 200

        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def fake_urlopen(req, timeout=None):
        probed_urls.append(req.full_url)
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider.validate_connection()
    assert probed_urls, "validate_connection should have made a request"
    assert probed_urls[0].endswith(
        "/api/tags"
    ), f"expected /api/tags endpoint, got {probed_urls[0]}"
