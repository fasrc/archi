"""Classify a container engine endpoint as local or not provably local.

Rule: strip the value; an empty value is local; a value beginning ``unix:`` is local; a
value carrying any other URI scheme (``^[A-Za-z][A-Za-z0-9+.-]*:``) is not provably local;
a value with no scheme is local only when it is **path-shaped** (``/``, ``./``, ``../`` or
``~``), and is otherwise not provably local.

That last clause was wrong in an earlier draft, which called any scheme-less value a
filesystem path. Docker's host parser prepends ``tcp://`` to a scheme-less
``DOCKER_HOST`` instead — measured on Docker 29.7.2, ``127.0.0.1:19999`` dials
``tcp://127.0.0.1:19999`` and ``somehost.example.edu:2375`` gets a DNS lookup — so
``engine.example.edu:2376`` names a remote daemon, not a socket.

A presence check (``bool(DOCKER_HOST)``) is wrong because FASRC uses
``export DOCKER_HOST=unix:/$(podman info --format '{{.Host.RemoteSocket.Path}}')`` which
produces a non-empty, non-remote value.
"""

import json
import os
import pathlib
import re

_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

# Returned when the context store holds several entries claiming the selected context
# name and they disagree about the endpoint. Distinct from None, which means "no
# context configured" and is not evidence of a remote engine; this one is.
_AMBIGUOUS = object()


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
    # No URI scheme. Docker does NOT read such a value as a filesystem path: its
    # host parser prepends `tcp://`. Measured on Docker 29.7.2 —
    # DOCKER_HOST=127.0.0.1:19999 reports "Cannot connect to the Docker daemon at
    # tcp://127.0.0.1:19999", [::1]:19999 becomes tcp://[::1]:19999, and
    # somehost.example.edu:2375 gets a DNS lookup. So a scheme-less host-and-port is
    # a remote daemon address, and reading it as a socket path is a fail-open.
    #
    # A path-shaped value stays local. Docker prepends tcp:// to those too
    # (/var/run/docker.sock dials tcp://localhost:2375/var/run/docker.sock), but that
    # address is on this machine, so "local" is the right answer to the question this
    # module asks. Podman's CONTAINER_HOST does accept a socket path, and the FASRC
    # command produces `unix:/...`, which the scheme branch above already accepts.
    if value.startswith(("/", "./", "../", "~")):
        return True
    # Anything else — a bare hostname, host:port, or a relative name that is not
    # obviously a path — is not provably local. Fail closed: this helper decides
    # whether it is safe to stamp this machine's name onto a deployment.
    return False


def _docker_config_dir() -> pathlib.Path:
    """Return the Docker client configuration directory.

    ``DOCKER_CONFIG`` relocates both ``config.json`` and the context metadata store, so
    reading ``~/.docker`` unconditionally would miss a remote context.
    """
    override = os.environ.get("DOCKER_CONFIG", "").strip()
    if override:
        return pathlib.Path(override)
    return pathlib.Path.home() / ".docker"


def _resolve_context_endpoint() -> str | None | object:
    """Return the active Docker context's Host endpoint, or None if unresolvable.

    Any exception is swallowed — an unreadable configuration is not evidence of a remote
    engine, because Docker falls back to the local default context.
    """
    matches: list = []
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
            matches.append(docker_endpoint.get("Host"))

        if not matches:
            return None
        # More than one entry claiming the selected name, disagreeing about the
        # endpoint: taking the first is taking whichever the filesystem listed first,
        # and a stale duplicate naming a local socket would hide the real remote one.
        # Ambiguity is not evidence of a local engine, so refuse rather than pick.
        if len({host for host in matches}) > 1:
            return _AMBIGUOUS
        return matches[0]
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
    if context_host is _AMBIGUOUS:
        return False
    if context_host is not None:
        return endpoint_is_local(context_host)

    return True
