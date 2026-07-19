"""Regression tests for the request-time provider-config override path.

`_build_provider_config_from_payload` builds the ProviderConfig used when a chat
request overrides the pipeline LLM (provider + model in the request, as the UI
dropdown always sends). It MUST preserve the config's ``extra_kwargs`` — dropping
them silently strips ``extra_body.chat_template_kwargs.enable_thinking`` from the
overridden LLM, so Qwen runs in thinking mode and chain-of-thought bleeds into
answers.
"""

from src.archi.providers.base import ProviderType
from src.interfaces.chat_app.app import (
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
