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
    """Return True when *endpoint* is provably local (or absent).

    A non-string endpoint is not provably local. A stored configuration can hold one —
    ``{"Host": 1}`` is valid JSON — and the check must classify it rather than raise.
    """
    if endpoint is None:
        return True
    if not isinstance(endpoint, str):
        return False
    value = endpoint.strip()
    if not value:
        return True
    if value.startswith("unix:"):
        return True
    if _URI_SCHEME_RE.match(value):
        return False
    # No URI scheme — treat as a bare filesystem path, which is local.
    return True


def _docker_config_dir() -> pathlib.Path:
    """Return the Docker client configuration directory.

    ``DOCKER_CONFIG`` relocates both ``config.json`` and the context metadata store, so
    reading ``~/.docker`` unconditionally would miss a remote context.
    """
    override = os.environ.get("DOCKER_CONFIG", "").strip()
    if override:
        return pathlib.Path(override)
    return pathlib.Path.home() / ".docker"


def _resolve_context_endpoint() -> str | None:
    """Return the active Docker context's Host endpoint, or None if unresolvable.

    Any exception is swallowed — an unreadable configuration is not evidence of a remote
    engine, because Docker falls back to the local default context.
    """
    try:
        config_dir = _docker_config_dir()
        context_name = os.environ.get("DOCKER_CONTEXT", "").strip()
        if not context_name:
            try:
                raw = (config_dir / "config.json").read_text()
                context_name = json.loads(raw).get("currentContext", "").strip()
            except Exception:
                return None
        if not context_name or context_name == "default":
            return None
        for meta_path in config_dir.glob("contexts/meta/*/meta.json"):
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:
                continue
            # Valid JSON is not necessarily an object: a stale `[]` parses fine and
            # then raises on .get, which would escape to the outer handler and report
            # "no context" for the whole store — hiding the selected remote context
            # behind an unrelated entry. Docker addresses the selected context
            # independently of stale siblings, so skip what cannot be read and keep
            # scanning. glob() yields os.scandir order, so which entry comes first is
            # filesystem luck; without this, the bug appears and disappears by machine.
            if not isinstance(meta, dict) or meta.get("Name") != context_name:
                continue
            endpoints = meta.get("Endpoints")
            if not isinstance(endpoints, dict):
                return None
            docker_endpoint = endpoints.get("docker")
            if not isinstance(docker_endpoint, dict):
                return None
            return docker_endpoint.get("Host")
        return None
    except Exception:
        return None


def _resolve_podman_connection_uri(name: str) -> str | None:
    """Return the URI of the named Podman connection, or None if unresolvable."""
    try:
        base = os.environ.get("XDG_CONFIG_HOME", "").strip()
        root = pathlib.Path(base) if base else pathlib.Path.home() / ".config"
        raw = (root / "containers" / "podman-connections.json").read_text()
        entry = json.loads(raw).get("Connection", {}).get("Connections", {}).get(name)
        if isinstance(entry, dict):
            return entry.get("URI")
        return None
    except Exception:
        return None


def container_endpoint_is_provably_local() -> bool:
    """Return True when every container endpoint variable points to a local engine.

    Checks DOCKER_HOST, CONTAINER_HOST, CONTAINER_CONNECTION, and the active Docker
    context; any refusing means not provably local. Two precedence rules apply, and they
    run opposite ways because the two engines do: DOCKER_HOST, when set and non-empty,
    takes priority over the Docker context, while CONTAINER_CONNECTION takes priority
    over CONTAINER_HOST.
    """
    docker_host = os.environ.get("DOCKER_HOST")
    container_host = os.environ.get("CONTAINER_HOST")

    if not endpoint_is_local(docker_host):
        return False

    # A named Podman connection decides the Podman endpoint on its own and outranks
    # CONTAINER_HOST — measured on Podman 6.1.0, where CONTAINER_CONNECTION dials its
    # own destination even when CONTAINER_HOST names a local socket. So it is
    # classified INSTEAD of CONTAINER_HOST, not in addition to it: a stale remote
    # CONTAINER_HOST must not refuse a deployment that podman routes locally.
    connection = os.environ.get("CONTAINER_CONNECTION", "").strip()
    if connection:
        connection_uri = _resolve_podman_connection_uri(connection)
        if connection_uri is None or not endpoint_is_local(connection_uri):
            return False
    elif not endpoint_is_local(container_host):
        return False

    # DOCKER_HOST set and non-empty and local — wins over context
    if docker_host is not None and docker_host.strip():
        return True

    context_host = _resolve_context_endpoint()
    if context_host is not None:
        return endpoint_is_local(context_host)

    return True
