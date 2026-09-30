"""Redact secret-bearing keys from a resolved QA agent config before persisting.

Segment matching (splitting on word boundaries) rather than substring matching
is required here because the runtime executes from the redacted config. Substring
'token' would match 'max_tokens', which the Anthropic provider reads; substring
'key' would match 'keywords'. Segment matching avoids those collisions while
still catching every credential form the chat-app's substring rule masks.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REDACTED = "[redacted]"
URL_REDACTED = "redacted"

SECRET_SEGMENTS = {
    "password",
    "passwd",
    "passphrase",
    "secret",
    "secrets",
    "token",
    "key",
    "keys",
    "apikey",
    "apikeys",
    "authorization",
    "authtoken",
    "bearer",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "session",
    "csrf",
    "xsrf",
    "dsn",
}

SECRET_SUFFIXES = (
    "password",
    "passwd",
    "secret",
    "token",
    "key",
    "keys",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "cookie",
    "authtokens",
    "accesstokens",
    "refreshtokens",
    "apitokens",
    "bearertokens",
)

_SECRET_PREFIXES = ("password", "passwd", "secret")

_CAMEL_LOWER_TO_UPPER = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_UPPER_RUN = re.compile(r"([A-Z]+)([A-Z][a-z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]")

_URL_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s]+")


def _segments(name: str) -> list[str]:
    s = str(name)
    s = _CAMEL_LOWER_TO_UPPER.sub(r"\1_\2", s)
    s = _CAMEL_UPPER_RUN.sub(r"\1_\2", s)
    s = s.lower()
    s = _NON_ALNUM.sub("_", s)
    return [seg for seg in s.split("_") if seg]


def is_secret_key(name: Any) -> bool:
    segs = _segments(name)
    for seg in segs:
        if seg in SECRET_SEGMENTS:
            return True
        if seg.endswith(SECRET_SUFFIXES):
            return True
        if seg.startswith(_SECRET_PREFIXES):
            return True
    return False


# ---------------------------------------------------------------------------
# URL password redaction
# ---------------------------------------------------------------------------


def _redact_url_passwords(text: str) -> str:
    def _replace(m: re.Match) -> str:
        raw = m.group(0)
        try:
            parsed = urllib.parse.urlsplit(raw)
        except ValueError:
            return REDACTED
        if parsed.password is None:
            return raw
        # Rebuild netloc with password replaced
        userinfo = parsed.username or ""
        userinfo += f":{URL_REDACTED}"
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        netloc = f"{userinfo}@{host}"
        rebuilt = urllib.parse.urlunsplit(
            (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
        )
        return rebuilt

    return _URL_PATTERN.sub(_replace, text)


# ---------------------------------------------------------------------------
# Redaction walker
# ---------------------------------------------------------------------------


def _redact_value_secret(value: Any) -> Any:
    """Redact every non-empty str/int/float leaf (secret mode)."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return REDACTED
    if isinstance(value, str):
        return REDACTED if value else value
    if isinstance(value, list):
        return [_redact_value_secret(v) for v in value]
    if isinstance(value, dict):
        return {k: _redact_value_secret(v) for k, v in value.items()}
    return value


def _redact_scalar(value: Any) -> Any:
    """Redact a scalar under a secret key (not secret mode)."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        return REDACTED if value else value
    if isinstance(value, (int, float)):
        return REDACTED
    return value


def _is_header_list_entry(mapping: dict) -> bool:
    """True when mapping has a 'name' or 'key' entry whose string value is secret."""
    for k in ("name", "key"):
        v = mapping.get(k)
        if isinstance(v, str) and is_secret_key(v):
            return True
    return False


def redact_agent_config(value: Any, *, _secret_mode: bool = False) -> Any:
    """Walk *value* and return a new structure with secret data replaced."""
    if isinstance(value, list):
        return [redact_agent_config(v, _secret_mode=_secret_mode) for v in value]

    if not isinstance(value, dict):
        if _secret_mode:
            return _redact_value_secret(value)
        # Non-secret mode scalar: redact URL passwords in strings
        if isinstance(value, str):
            return _redact_url_passwords(value)
        return value

    # Header-list form: {name: "X-Api-Key", value: "..."} — redact value entry
    if not _secret_mode and _is_header_list_entry(value):
        out = {}
        for k, v in value.items():
            if k == "value":
                out[k] = _redact_scalar(v)
            else:
                out[k] = redact_agent_config(v, _secret_mode=False)
        return out

    # Regular mapping
    out = {}
    for k, v in value.items():
        if is_secret_key(k):
            if isinstance(v, (dict, list)):
                out[k] = _redact_value_secret(v)
            else:
                out[k] = _redact_scalar(v)
        else:
            out[k] = redact_agent_config(v, _secret_mode=_secret_mode)
    return out
