"""Tests for the local_mode canonicalization seam in _resolve_provider_config.

After fix #463, the function delegates to apply_local_mode(overwrite=False), which
validates and canonicalizes the value before writing it. An existing extra_kwargs
["local_mode"] key wins; mixed-case modes are accepted; unrecognized values raise.
"""

import pytest

from src.data_manager.collectors.processing import _resolve_provider_config


def _providers(mode, extra_kwargs=None):
    return {
        "local": {
            "base_url": "http://localhost:11434",
            "mode": mode,
            "extra_kwargs": extra_kwargs or {},
        }
    }


def test_resolve_mixed_case_mode_canonicalized():
    result = _resolve_provider_config("local", _providers("OpenAI_Compat"))
    assert result["extra_kwargs"]["local_mode"] == "openai_compat"


def test_resolve_invalid_mode_raises():
    with pytest.raises(ValueError):
        _resolve_provider_config("local", _providers("vllm"))


def test_resolve_empty_mode_raises():
    with pytest.raises(ValueError):
        _resolve_provider_config("local", _providers(""))


def test_resolve_no_mode_key_writes_no_local_mode():
    cfg = {"local": {"base_url": "http://localhost:11434", "extra_kwargs": {}}}
    result = _resolve_provider_config("local", cfg)
    assert result is not None
    assert "local_mode" not in result["extra_kwargs"]


def test_resolve_existing_local_mode_wins():
    result = _resolve_provider_config(
        "local", _providers("openai_compat", {"local_mode": "ollama"})
    )
    assert result["extra_kwargs"]["local_mode"] == "ollama"
