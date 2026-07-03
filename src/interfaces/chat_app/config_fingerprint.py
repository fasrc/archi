"""Effective-config observability for the chat app.

Surfaces what configuration the running process actually loaded — the default
provider, the effective ``extra_kwargs`` it will pass to the LLM (notably
``chat_template_kwargs.enable_thinking``), and a fingerprint of the whole
providers block — so drift between the stored config and the live process is
greppable from container logs and assertable via ``/api/health``.

Motivating incident (OpenSpec change ``harden-config-propagation``): a chat
process served ``enable_thinking``-enabled output for two days because it kept
config it had cached at boot, and there was no way to see what it had loaded
without reconstructing it by hand. These helpers make the effective config a
one-line log and a read-only health field.
"""

import hashlib
import json
from typing import Any, Dict, Optional, Tuple

# Substrings that mark a config key as secret-bearing; their values are masked
# before the effective config is written to logs. The public health payload
# never includes raw ``extra_kwargs`` at all.
_SENSITIVE_HINTS = ("key", "token", "secret", "password", "authorization")


def _chat_app(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return the ``services.chat_app`` sub-config, tolerating missing keys."""
    return ((config or {}).get("services", {}) or {}).get("chat_app", {}) or {}


def _default_provider_block(
    config: Dict[str, Any]
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Return ``(default_provider_name, that_provider's_config_block)``."""
    chat_app = _chat_app(config)
    provider = chat_app.get("default_provider")
    block = (chat_app.get("providers", {}) or {}).get(provider, {}) or {}
    return provider, block


def providers_sha256(config: Dict[str, Any]) -> str:
    """Stable short fingerprint of the whole providers block, for drift checks."""
    providers = _chat_app(config).get("providers", {}) or {}
    blob = json.dumps(providers, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def resolved_enable_thinking(config: Dict[str, Any]) -> Optional[bool]:
    """The ``enable_thinking`` the default provider will send, or ``None``.

    Guards each level: a config that sets ``extra_body`` (or
    ``chat_template_kwargs``) to ``null``/non-mapping resolves to ``None`` rather
    than raising — this runs at boot and on ``/api/health``, so it must not crash.
    """
    _, block = _default_provider_block(config)
    node: Any = block
    for key in (
        "extra_kwargs",
        "extra_body",
        "chat_template_kwargs",
        "enable_thinking",
    ):
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _redact(value: Any) -> Any:
    """Recursively mask values whose key looks secret-bearing.

    Recurses into both dicts and list/tuple items, so a secret nested inside a
    list-valued kwarg (e.g. per-endpoint entries with ``api_key``) is masked
    before the boot summary is logged.
    """
    if isinstance(value, dict):
        return {
            k: (
                "***"
                if any(h in str(k).lower() for h in _SENSITIVE_HINTS)
                else _redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def resolve_provider_boot_summary(config: Dict[str, Any]) -> str:
    """One-line server-side log of the effective loaded provider config.

    Includes the default provider's ``extra_kwargs`` (with secret-bearing keys
    redacted) so an operator can confirm ``enable_thinking`` and friends from
    ``docker logs`` without reconstructing them by hand.
    """
    provider, block = _default_provider_block(config)
    model = block.get("default_model") or _chat_app(config).get("default_model")
    return (
        "effective chat config: "
        f"config_version={config.get('config_version')} "
        f"default_provider={provider} "
        f"default_model={model} "
        f"enable_thinking={resolved_enable_thinking(config)} "
        f"extra_kwargs={_redact(block.get('extra_kwargs', {}) or {})} "
        f"providers_sha256={providers_sha256(config)}"
    )


def build_health_payload(config: Dict[str, Any]) -> Dict[str, Any]:
    """Public, secret-free ``/api/health`` body: booleans + hash, no raw kwargs."""
    provider, block = _default_provider_block(config)
    model = block.get("default_model") or _chat_app(config).get("default_model")
    return {
        "status": "OK",
        "config_version": config.get("config_version"),
        "provider": provider,
        "model": model,
        "enable_thinking": resolved_enable_thinking(config),
        "providers_sha256": providers_sha256(config),
    }
