"""Redact secret-bearing keys from a resolved QA agent config before persisting.

Segment matching (splitting on word boundaries) rather than substring matching
is required here because the runtime executes from the redacted config. Substring
'token' would match 'max_tokens', which the Anthropic provider reads; substring
'key' would match 'keywords'. Segment matching avoids those collisions while
still catching every credential form the chat-app's substring rule masks.
"""

from __future__ import annotations

import re
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
    "passphrase",
    "passphrases",
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

_SECRET_PREFIXES = ("password", "passwd", "passphrase", "secret")

# Split plural forms (access_tokens, apiTokens): "tokens" is secret only after a
# credential qualifier, so count keys such as max_tokens stay plain.
_TOKENS_QUALIFIERS = {"access", "refresh", "api", "auth", "bearer"}

_CAMEL_LOWER_TO_UPPER = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_UPPER_RUN = re.compile(r"([A-Z]+)([A-Z][a-z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]")

# scheme:// then the userinfo up to the LAST "@" of the authority (the authority
# ends at the first "/", "?", "#" or whitespace). Each scheme occurrence is its own
# match, so a URL nested in another URL's query is redacted too.
_URL_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)([^\s/?#]*)@")


def _segments(name: str) -> list[str]:
    s = str(name)
    s = _CAMEL_LOWER_TO_UPPER.sub(r"\1_\2", s)
    s = _CAMEL_UPPER_RUN.sub(r"\1_\2", s)
    s = s.lower()
    s = _NON_ALNUM.sub("_", s)
    return [seg for seg in s.split("_") if seg]


def is_secret_key(name: Any) -> bool:
    segs = _segments(name)
    for index, seg in enumerate(segs):
        if seg == "tokens" and index and segs[index - 1] in _TOKENS_QUALIFIERS:
            return True
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
    """Replace the password in every ``scheme://user:password@`` occurrence.

    Only the password text changes. The host, port, path and query are kept
    byte for byte, so nothing is parsed: an IPv6 host or an out-of-range port
    cannot be reformatted or raise.
    """

    def _replace(m: re.Match) -> str:
        scheme, userinfo = m.group(1), m.group(2)
        if ":" not in userinfo:
            return m.group(0)
        username = userinfo.split(":", 1)[0]
        return f"{scheme}{username}:{URL_REDACTED}@"

    return _URL_USERINFO.sub(_replace, text)


# ---------------------------------------------------------------------------
# Redaction walker
# ---------------------------------------------------------------------------


def _redact_value_secret(value: Any) -> Any:
    """Redact every leaf except None, booleans and the empty string (secret mode).

    Any other scalar type (a YAML date, ``!!binary`` bytes) is redacted too: an
    unknown type must not pass the redaction boundary.
    """
    if isinstance(value, bool) or value is None or value == "":
        return value
    if isinstance(value, list):
        return [_redact_value_secret(v) for v in value]
    if isinstance(value, dict):
        return {k: _redact_value_secret(v) for k, v in value.items()}
    return REDACTED


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
                out[k] = _redact_value_secret(v)
            else:
                out[k] = redact_agent_config(v, _secret_mode=False)
        return out

    # Regular mapping
    out = {}
    for k, v in value.items():
        if is_secret_key(k):
            out[k] = _redact_value_secret(v)
        else:
            out[k] = redact_agent_config(v, _secret_mode=_secret_mode)
    return out
