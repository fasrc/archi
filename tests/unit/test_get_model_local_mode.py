"""Regression: get_model must not inject local_mode when provider_config has no mode.

The OpenAI provider's categorization path passes provider_config with
``mode: None`` (no explicit mode configured). Before the fix, ``get_model``
checked ``"mode" in provider_config`` (key presence), which is True for
``{"mode": None}``, so it injected ``local_mode=None`` into extra_kwargs.
Langchain transferred the unrecognised kwarg to model_kwargs, and the OpenAI
Completions client rejected it with::

    Completions.create() got an unexpected keyword argument 'local_mode'

Every categorization call silently fell back to "uncategorized".
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_cache():
    from src.archi.providers import clear_provider_cache

    clear_provider_cache()
    yield
    clear_provider_cache()


def _extra_kwargs_from_get_model(provider_config):
    """Call get_model and capture the ProviderConfig.extra_kwargs it constructs."""
    from src.archi.providers import get_model

    captured = {}

    with patch("src.archi.providers.get_provider") as mock_gp:
        mock_provider = MagicMock()
        mock_provider.get_chat_model.return_value = MagicMock()
        mock_gp.return_value = mock_provider

        get_model("openai", "some-model", provider_config)

        config_arg = mock_gp.call_args[0][1]
        captured["extra_kwargs"] = dict(config_arg.extra_kwargs)

    return captured["extra_kwargs"]


def test_mode_none_does_not_inject_local_mode():
    cfg = {
        "base_url": "http://localhost:8001/v1",
        "default_model": "m",
        "extra_kwargs": {"temperature": 0.3},
        "mode": None,
    }
    ek = _extra_kwargs_from_get_model(cfg)
    assert "local_mode" not in ek
    assert ek == {"temperature": 0.3}


def test_mode_absent_does_not_inject_local_mode():
    cfg = {
        "base_url": "http://localhost:8001/v1",
        "default_model": "m",
        "extra_kwargs": {},
    }
    ek = _extra_kwargs_from_get_model(cfg)
    assert "local_mode" not in ek


def test_mode_present_injects_local_mode():
    cfg = {
        "base_url": "http://localhost:8001/v1",
        "default_model": "m",
        "mode": "openai_compat",
        "extra_kwargs": {},
    }
    ek = _extra_kwargs_from_get_model(cfg)
    assert ek["local_mode"] == "openai_compat"


def test_explicit_local_mode_in_extra_kwargs_not_overwritten():
    cfg = {
        "base_url": "http://localhost:8001/v1",
        "default_model": "m",
        "mode": "openai_compat",
        "extra_kwargs": {"local_mode": "ollama"},
    }
    ek = _extra_kwargs_from_get_model(cfg)
    assert ek["local_mode"] == "ollama"
