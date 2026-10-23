"""Tests for the local_mode canonicalization seam in _resolve_provider_config.

After fix #463, the function delegates to apply_local_mode(overwrite=False), which
validates and canonicalizes the value before writing it. An existing extra_kwargs
["local_mode"] key wins; mixed-case modes are accepted; unrecognized values raise.
"""

import pytest

from src.data_manager.collectors.processing import _resolve_provider_config
from src.utils.local_mode import LOCAL_PROVIDER_KEY


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


def test_resolve_non_local_provider_mode_is_left_alone():
    """A non-local provider's ``mode`` is not a local_mode and must not be judged.

    The other three seams (``base_react._build_provider_config``,
    ``app._build_provider_config_from_payload``, ``get_model``) all gate the
    canonicalizer on ``ProviderType.LOCAL``. Without the same gate here, an
    ``anthropic`` block carrying its own ``mode`` value raises at ingest-pipeline
    build time while the identical config still works in chat.
    """
    cfg = {"anthropic": {"models": ["claude"], "mode": "batch", "extra_kwargs": {}}}
    result = _resolve_provider_config("anthropic", cfg)
    assert result is not None
    assert "local_mode" not in result["extra_kwargs"]
    assert result["mode"] == "batch"


def test_local_provider_key_matches_the_provider_enum():
    """The dependency-free key must stay pinned to the enum it stands in for.

    ``processing.py`` deliberately keeps ``src.archi.providers`` out of its
    module-level imports (that package pulls ``langchain_core``, which the
    conversion-only ingest path does not require), so the seam compares a string
    instead of ``ProviderType.LOCAL``. This test is the join between the two.
    """
    from src.archi.providers.base import ProviderType

    assert LOCAL_PROVIDER_KEY == ProviderType.LOCAL.value
