"""Unit tests for chat-app effective-config observability helpers."""

import json

from src.interfaces.chat_app.config_fingerprint import (
    build_health_payload,
    providers_sha256,
    resolve_provider_boot_summary,
    resolved_enable_thinking,
)


def _cfg(enable_thinking=False, extra=None):
    """A minimal get_full_config()-shaped dict for the local provider."""
    extra_kwargs = {
        "extra_body": {"chat_template_kwargs": {"enable_thinking": enable_thinking}}
    }
    if extra:
        extra_kwargs.update(extra)
    return {
        "config_version": "2.0.0",
        "services": {
            "chat_app": {
                "default_provider": "local",
                "providers": {
                    "local": {"default_model": "qwen", "extra_kwargs": extra_kwargs}
                },
            }
        },
    }


def test_resolved_enable_thinking_reads_default_provider():
    assert resolved_enable_thinking(_cfg(enable_thinking=False)) is False
    assert resolved_enable_thinking(_cfg(enable_thinking=True)) is True


def test_resolved_enable_thinking_missing_returns_none():
    assert resolved_enable_thinking({}) is None
    assert (
        resolved_enable_thinking(
            {
                "services": {
                    "chat_app": {
                        "default_provider": "local",
                        "providers": {"local": {}},
                    }
                }
            }
        )
        is None
    )


def test_providers_sha256_stable_and_content_sensitive():
    a = providers_sha256(_cfg(enable_thinking=False))
    assert a == providers_sha256(_cfg(enable_thinking=False))  # stable
    assert a != providers_sha256(_cfg(enable_thinking=True))  # tracks content
    assert len(a) == 12


def test_boot_summary_has_key_fields_and_redacts_secrets():
    summary = resolve_provider_boot_summary(
        _cfg(enable_thinking=False, extra={"api_key": "sk-secret", "timeout": 30})
    )
    assert "config_version=2.0.0" in summary
    assert "default_provider=local" in summary
    assert "default_model=qwen" in summary
    assert "enable_thinking=False" in summary
    assert "providers_sha256=" in summary
    assert "sk-secret" not in summary  # secret-bearing key masked
    assert "***" in summary
    assert "timeout" in summary  # non-sensitive kwargs preserved


def test_health_payload_is_secret_free():
    payload = build_health_payload(
        _cfg(enable_thinking=False, extra={"api_key": "sk-secret"})
    )
    assert payload["status"] == "OK"
    assert payload["config_version"] == "2.0.0"
    assert payload["provider"] == "local"
    assert payload["model"] == "qwen"
    assert payload["enable_thinking"] is False
    assert len(payload["providers_sha256"]) == 12
    # raw extra_kwargs / secrets must never appear in the public payload
    assert "sk-secret" not in json.dumps(payload)
    assert "extra_kwargs" not in payload


def test_empty_config_does_not_crash():
    assert build_health_payload({})["status"] == "OK"
    assert len(providers_sha256({})) == 12
    assert "effective chat config" in resolve_provider_boot_summary({})
