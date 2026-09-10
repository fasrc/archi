"""Classify a container engine endpoint as local or not provably local.

Rule: strip the value; an empty value is local; a value beginning ``unix:`` is local; a
value carrying any other URI scheme (``^[A-Za-z][A-Za-z0-9+.-]*:``) is not provably local;
a value with no scheme is a filesystem path and is local.

A presence check (``bool(DOCKER_HOST)``) is wrong because FASRC uses
``export DOCKER_HOST=unix:/$(podman info --format '{{.Host.RemoteSocket.Path}}')`` which
produces a non-empty, non-remote value.
"""

import json
import os
import pathlib
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


def _resolve_context_endpoint() -> str | None:
    """Return the active Docker context's Host endpoint, or None if unresolvable.

    Any exception is swallowed — an unreadable configuration is not evidence of a remote
    engine.
    """
    try:
        context_name = os.environ.get("DOCKER_CONTEXT", "").strip()
        if not context_name:
            home = pathlib.Path.home()
            try:
                raw = (home / ".docker" / "config.json").read_text()
                context_name = json.loads(raw).get("currentContext", "").strip()
            except Exception:
                return None
        if not context_name or context_name == "default":
            return None
        home = pathlib.Path.home()
        for meta_path in home.glob(".docker/contexts/meta/*/meta.json"):
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:
                continue
            if meta.get("Name") == context_name:
                return meta.get("Endpoints", {}).get("docker", {}).get("Host")
        return None
    except Exception:
        return None


def container_endpoint_is_provably_local() -> bool:
    """Return True when every container endpoint variable points to a local engine.

    Checks DOCKER_HOST, CONTAINER_HOST, and the active Docker context; any refusing means
    not provably local. DOCKER_HOST, when explicitly set, takes priority over context
    resolution.
    """
    docker_host = os.environ.get("DOCKER_HOST")
    container_host = os.environ.get("CONTAINER_HOST")

    if not endpoint_is_local(docker_host) or not endpoint_is_local(container_host):
        return False

    # DOCKER_HOST explicitly set and local — wins over context
    if docker_host is not None:
        return True

    context_host = _resolve_context_endpoint()
    if context_host is not None:
        return endpoint_is_local(context_host)

    return True
