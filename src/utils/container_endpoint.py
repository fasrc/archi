"""Classify a container engine endpoint as local or not provably local.

Rule: strip the value; an empty value is local; a value beginning ``unix:`` is local; a
value carrying any other URI scheme (``^[A-Za-z][A-Za-z0-9+.-]*:``) is not provably local;
a value with no scheme is a filesystem path and is local.

A presence check (``bool(DOCKER_HOST)``) is wrong because FASRC uses
``export DOCKER_HOST=unix:/$(podman info --format '{{.Host.RemoteSocket.Path}}')`` which
produces a non-empty, non-remote value.
"""

import re

_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def endpoint_is_local(endpoint: str | None) -> bool:
    """Return True when *endpoint* is provably local (or absent)."""
    if endpoint is None:
        return True
    value = endpoint.strip()
    if not value:
        return True
    if value.startswith("unix:"):
        return True
    if _URI_SCHEME_RE.match(value):
        return False
    # No URI scheme — treat as a bare filesystem path, which is local.
    return True
