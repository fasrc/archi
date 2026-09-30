"""Tests for the local_mode canonicalization seam in BaseReActAgent._build_provider_config.

The static method resolves extra_kwargs["local_mode"] from the config's `mode` field.
After fix #463 it delegates to apply_local_mode, which validates and canonicalizes the
value, so mixed-case modes are accepted and unrecognized values raise at construction.
"""

import pytest

from src.archi.pipelines.agents.base_react import BaseReActAgent


def _providers(mode, extra_kwargs=None):
    return {
        "local": {
            "base_url": "http://localhost:8001/v1",
            "mode": mode,
            "extra_kwargs": extra_kwargs or {},
        }
    }


def test_base_react_mixed_case_mode_canonicalized():
    result = BaseReActAgent._build_provider_config("local", _providers("OpenAI_Compat"))
    assert result["extra_kwargs"]["local_mode"] == "openai_compat"


def test_base_react_invalid_mode_raises():
    with pytest.raises(ValueError):
        BaseReActAgent._build_provider_config("local", _providers("vllm"))


def test_base_react_empty_mode_raises():
    with pytest.raises(ValueError):
        BaseReActAgent._build_provider_config("local", _providers(""))


def test_base_react_no_mode_key_writes_no_local_mode():
    cfg = {"local": {"base_url": "http://localhost:8001/v1", "extra_kwargs": {}}}
    result = BaseReActAgent._build_provider_config("local", cfg)
    assert "local_mode" not in result["extra_kwargs"]


def test_base_react_unknown_provider_key_no_raise_no_local_mode():
    cfg = {
        "not_real": {
            "base_url": "http://localhost:8001/v1",
            "mode": "openai_compat",
            "extra_kwargs": {},
        }
    }
    result = BaseReActAgent._build_provider_config("not_real", cfg)
    assert "local_mode" not in result["extra_kwargs"]
