"""Unit tests for the benchmark SUT local-provider bridge (issue #73).

The benchmark sets the system-under-test in services.benchmarking, but the agent
reads its provider mode/base_url from services.chat_app.providers.<provider>. For
a `provider: local` SUT pointed at an OpenAI-compatible endpoint (e.g. the FASRC
vLLM at .../v1), the agent defaulted to the Ollama client and 404'd. These helpers
resolve the right mode and inject the provider block the agent actually reads.
"""

from src.bin.benchmark_sut import (
    apply_sut_local_provider,
    inject_sut_provider,
    resolve_local_mode,
)


class _FakeStatic:
    def __init__(self, services_config):
        self.services_config = services_config


def test_v1_endpoint_resolves_openai_compat():
    assert (
        resolve_local_mode("http://archi.rc.fas.harvard.edu:8001/v1") == "openai_compat"
    )


def test_v1_endpoint_with_trailing_slash_resolves_openai_compat():
    assert resolve_local_mode("http://host:8001/v1/") == "openai_compat"


def test_non_v1_endpoint_resolves_ollama():
    assert resolve_local_mode("http://localhost:11434") == "ollama"


def test_explicit_mode_overrides_autodetect():
    assert resolve_local_mode("http://host:8001/v1", explicit="ollama") == "ollama"


def test_inject_sets_chat_app_provider_block():
    services = {}
    block = inject_sut_provider(
        services, "local", "qwen-x", "http://host:8001/v1", "openai_compat"
    )
    injected = services["chat_app"]["providers"]["local"]
    assert injected is block
    assert injected["base_url"] == "http://host:8001/v1"
    assert injected["mode"] == "openai_compat"
    assert injected["default_model"] == "qwen-x"
    assert injected["enabled"] is True


def test_inject_preserves_other_providers_and_keys():
    services = {"chat_app": {"providers": {"anthropic": {"enabled": True}}}}
    inject_sut_provider(services, "Local", "m", "http://h:8001/v1", "openai_compat")
    providers = services["chat_app"]["providers"]
    assert providers["anthropic"] == {"enabled": True}  # untouched
    assert providers["local"]["mode"] == "openai_compat"  # provider key lowercased


def test_inject_merges_into_existing_block_preserving_extra_kwargs():
    # A pre-existing provider block may carry vLLM extra_kwargs/timeouts that
    # BaseReActAgent._build_provider_config forwards. Overwriting the whole block
    # would silently drop them, so the SUT keys must be merged in, not replace it.
    services = {
        "chat_app": {
            "providers": {
                "local": {
                    "extra_kwargs": {"extra_body": {"enable_thinking": False}},
                    "timeout": 600,
                    "mode": "ollama",
                }
            }
        }
    }
    block = inject_sut_provider(
        services, "local", "qwen-x", "http://host:8001/v1", "openai_compat"
    )
    injected = services["chat_app"]["providers"]["local"]
    assert injected is block
    # SUT keys take the resolved values...
    assert injected["base_url"] == "http://host:8001/v1"
    assert injected["mode"] == "openai_compat"
    assert injected["default_model"] == "qwen-x"
    assert injected["enabled"] is True
    # ...but pre-existing options the agent still reads are preserved.
    assert injected["extra_kwargs"] == {"extra_body": {"enable_thinking": False}}
    assert injected["timeout"] == 600


def test_inject_tolerates_non_dict_services():
    # Must not raise on a missing/malformed static config.
    assert inject_sut_provider(None, "local", "m", "u", "ollama") is None


def test_apply_injects_for_local_provider_v1_endpoint():
    services = {}
    static = _FakeStatic(services)
    cfg = {
        "provider": "local",
        "model": "qwen-x",
        "ollama_url": "http://archi.rc.fas.harvard.edu:8001/v1",
    }
    block = apply_sut_local_provider(cfg, static)
    assert block["mode"] == "openai_compat"  # auto-detected from /v1
    assert services["chat_app"]["providers"]["local"]["base_url"].endswith("/v1")


def test_apply_respects_explicit_provider_mode():
    services = {}
    cfg = {
        "provider": "local",
        "model": "m",
        "ollama_url": "http://h:11434/v1",
        "provider_mode": "ollama",
    }
    apply_sut_local_provider(cfg, _FakeStatic(services))
    assert services["chat_app"]["providers"]["local"]["mode"] == "ollama"


def test_apply_is_noop_for_non_local_provider():
    services = {}
    cfg = {"provider": "anthropic", "model": "claude-x", "ollama_url": "http://h/v1"}
    assert apply_sut_local_provider(cfg, _FakeStatic(services)) is None
    assert services == {}  # untouched — anthropic builds its own default client


def test_apply_tolerates_missing_static_and_cfg():
    assert apply_sut_local_provider({"provider": "local"}, None) is None
    assert apply_sut_local_provider(None, _FakeStatic({})) is None
