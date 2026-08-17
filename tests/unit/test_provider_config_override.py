"""Regression tests for the request-time provider-config override path.

`_build_provider_config_from_payload` builds the ProviderConfig used when a chat
request overrides the pipeline LLM (provider + model in the request, as the UI
dropdown always sends). It MUST preserve the config's ``extra_kwargs`` — dropping
them silently strips ``extra_body.chat_template_kwargs.enable_thinking`` from the
overridden LLM, so Qwen runs in thinking mode and chain-of-thought bleeds into
answers.
"""

from types import SimpleNamespace

from src.archi.providers.base import ProviderType
from src.interfaces.chat_app.app import (
    ChatWrapper,
    _build_provider_config_from_payload,
    _is_provider_enabled_in_config,
)


def _cfg(extra_kwargs):
    return {
        "services": {
            "chat_app": {
                "providers": {
                    "local": {
                        "base_url": "http://localhost:8001/v1",
                        "mode": "openai_compat",
                        "default_model": "m",
                        "models": ["m"],
                        "extra_kwargs": extra_kwargs,
                    }
                }
            }
        }
    }


def test_override_provider_config_preserves_extra_kwargs():
    ek = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    pc = _build_provider_config_from_payload(_cfg(ek), ProviderType.LOCAL)
    assert pc is not None
    # the thinking flag must survive the override path
    assert pc.extra_kwargs.get("extra_body") == {
        "chat_template_kwargs": {"enable_thinking": False}
    }
    # local_mode is still derived from `mode`
    assert pc.extra_kwargs.get("local_mode") == "openai_compat"


def test_override_provider_config_no_extra_kwargs_still_sets_local_mode():
    pc = _build_provider_config_from_payload(_cfg({}), ProviderType.LOCAL)
    assert pc is not None
    assert pc.extra_kwargs == {"local_mode": "openai_compat"}


def test_override_provider_config_does_not_mutate_source():
    ek = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    cfg = _cfg(ek)
    _build_provider_config_from_payload(cfg, ProviderType.LOCAL)
    # building the config must not inject local_mode back into the source dict
    src_extra = cfg["services"]["chat_app"]["providers"]["local"]["extra_kwargs"]
    assert "local_mode" not in src_extra


def test_provider_enabled_by_name_converts_and_checks_config():
    # provider_name is coerced to a ProviderType and, with no explicit
    # `enabled: false`, the override is allowed.
    ok, reason = _is_provider_enabled_in_config(_cfg({}), provider_name="local")
    assert ok is True
    assert reason is None


def test_provider_disabled_by_explicit_enabled_false():
    cfg = _cfg({})
    cfg["services"]["chat_app"]["providers"]["local"]["enabled"] = False
    ok, reason = _is_provider_enabled_in_config(cfg, ProviderType.LOCAL)
    assert ok is False
    assert "local" in reason


def test_unknown_provider_name_treated_as_enabled():
    # An unrecognized provider name can't be coerced to a ProviderType, so the
    # config check defers to other validation and reports enabled.
    ok, reason = _is_provider_enabled_in_config(
        _cfg({}), provider_name="not-a-real-provider"
    )
    assert ok is True
    assert reason is None


def test_no_provider_identifier_is_enabled():
    # Neither provider_type nor provider_name given -> nothing to disable.
    ok, reason = _is_provider_enabled_in_config(_cfg({}))
    assert ok is True
    assert reason is None


def test_non_dict_config_payload_is_enabled():
    # A malformed (non-dict) payload must not crash and defaults to enabled.
    ok, reason = _is_provider_enabled_in_config(None, ProviderType.LOCAL)
    assert ok is True
    assert reason is None


# --- The override's context window comes from the provider built here --------


class _StubProvider:
    """A provider built from this deployment's YAML, as the override path does."""

    def __init__(self, window=None, raises=False):
        self._window = window
        self._raises = raises
        self.api_key = None

    def set_api_key(self, key):
        self.api_key = key

    def get_chat_model(self, model):
        return f"chat-model:{model}"

    def get_model_info(self, model):
        if self._raises:
            raise RuntimeError("provider has no metadata for this model")
        if self._window is None:
            return None
        return SimpleNamespace(context_window=self._window)


def _wrapper(config):
    """A ChatWrapper with only the attribute _create_provider_llm reads."""
    wrapper = object.__new__(ChatWrapper)
    wrapper.config = config
    return wrapper


def _enabled_cfg():
    config = _cfg({})
    config["services"]["chat_app"]["providers"]["local"]["enabled"] = True
    return config


def test_a_model_named_in_the_config_reports_no_window(monkeypatch):
    """A configured model's "window" is ModelInfo's 128000 default, not a fact.

    `_build_provider_config_from_payload` builds each `models:` entry as
    `ModelInfo(id=m, name=m, display_name=m)`, so `get_model_info` answers with
    the dataclass default however the server was actually launched. Passing that
    on would size the budget from a number nothing measured — on this repo's own
    dev config, 128000 against a 32768-token server.
    """
    provider = _StubProvider(window=128000)
    monkeypatch.setattr(
        "src.archi.providers.get_provider", lambda *a, **kw: provider, raising=False
    )

    # "m" is the model named in _cfg()'s `models:` list.
    llm, window = _wrapper(_enabled_cfg())._create_provider_llm("local", "m", "key-1")

    assert llm == "chat-model:m"
    assert window is None, "a fabricated window must not reach the budget"
    assert provider.api_key == "key-1"


def test_a_model_the_config_does_not_name_keeps_the_providers_window(monkeypatch):
    """The provider's own compiled metadata is real and must still be used."""
    monkeypatch.setattr(
        "src.archi.providers.get_provider",
        lambda *a, **kw: _StubProvider(window=200000),
        raising=False,
    )

    llm, window = _wrapper(_enabled_cfg())._create_provider_llm(
        "local", "a-model-not-in-the-config"
    )

    assert llm == "chat-model:a-model-not-in-the-config"
    assert window == 200000


def test_create_provider_llm_reports_no_window_when_metadata_is_absent(monkeypatch):
    """No metadata is not an error: the model still builds, the window is None."""
    monkeypatch.setattr(
        "src.archi.providers.get_provider",
        lambda *a, **kw: _StubProvider(window=None),
        raising=False,
    )

    llm, window = _wrapper(_enabled_cfg())._create_provider_llm("local", "m")

    assert llm == "chat-model:m"
    assert window is None


def test_create_provider_llm_survives_a_provider_that_raises(monkeypatch):
    """A provider that throws on get_model_info must not fail the request."""
    monkeypatch.setattr(
        "src.archi.providers.get_provider",
        lambda *a, **kw: _StubProvider(raises=True),
        raising=False,
    )

    llm, window = _wrapper(_enabled_cfg())._create_provider_llm("local", "m")

    assert llm == "chat-model:m"
    assert window is None
