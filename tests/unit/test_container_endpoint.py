import json
import pathlib

import pytest

from src.utils.container_endpoint import (
    container_endpoint_is_provably_local,
    endpoint_is_local,
)

_ENDPOINT_VARS = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "CONTAINER_HOST",
    "CONTAINER_CONNECTION",
    "XDG_CONFIG_HOME",
)


@pytest.fixture(autouse=True)
def _isolated_endpoint_env(monkeypatch, tmp_path):
    """Detach every test from the developer's own engine configuration.

    Without this the suite reads whatever DOCKER_CONFIG or CONTAINER_CONNECTION the
    machine running it happens to export, so a green run proves nothing.
    """
    for name in _ENDPOINT_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))


def test_unix_triple_slash_is_local():
    assert endpoint_is_local("unix:///var/run/docker.sock") is True


def test_unix_single_slash_podman_fasrc_form_is_local():
    assert endpoint_is_local("unix:/run/user/1000/podman/podman.sock") is True


def test_bare_path_no_scheme_is_local():
    assert endpoint_is_local("/var/run/docker.sock") is True


@pytest.mark.parametrize(
    "endpoint",
    [
        "192.0.2.10:2375",
        "127.0.0.1:19999",
        "[::1]:19999",
        "[2001:db8::1]:2376",
        "engine.example.edu:2376",
        "engine.example.edu",
    ],
)
def test_schemeless_host_and_port_is_not_local(endpoint):
    """Docker defaults a scheme-less DOCKER_HOST to TCP, not to a socket path.

    Measured on Docker 29.7.2 -- `DOCKER_HOST=127.0.0.1:19999` reports
    `Cannot connect to the Docker daemon at tcp://127.0.0.1:19999`, and
    `somehost.example.edu:2375` resolves DNS for it. So a scheme-less
    host-and-port is a remote daemon address, and reading it as a filesystem path
    stamps the CLI hostname onto a remote deployment.
    """
    assert endpoint_is_local(endpoint) is False


@pytest.mark.parametrize(
    "endpoint",
    ["/var/run/docker.sock", "/run/user/1000/podman/podman.sock", "./relative.sock"],
)
def test_schemeless_path_shaped_values_stay_local(endpoint):
    """The path-shaped fallback survives, narrowed to values that look like paths.

    Docker in fact prepends `tcp://` to these too --
    `DOCKER_HOST=/var/run/docker.sock` dials
    `tcp://localhost:2375/var/run/docker.sock` -- but that address is on this
    machine, so classifying it local is right for the question this module asks.
    Podman's `CONTAINER_HOST` does accept a socket path, and the FASRC command
    produces `unix:/...`, which the scheme branch already accepts.
    """
    assert endpoint_is_local(endpoint) is True


def test_empty_string_is_local():
    assert endpoint_is_local("") is True


def test_none_is_local():
    assert endpoint_is_local(None) is True


def test_whitespace_only_is_local():
    assert endpoint_is_local("  ") is True


def test_tcp_is_not_local():
    assert endpoint_is_local("tcp://engine.example.edu:2376") is False


def test_ssh_is_not_local():
    assert endpoint_is_local("ssh://user@box/run/podman.sock") is False


def test_http_is_not_local():
    assert endpoint_is_local("http://h:2375") is False


def test_https_is_not_local():
    assert endpoint_is_local("https://h:2376") is False


def test_npipe_is_not_local():
    assert endpoint_is_local("npipe:////./pipe/docker_engine") is False


def test_unknown_scheme_is_not_local():
    assert endpoint_is_local("weirdscheme://h") is False


def test_env_no_variable_set_is_local(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("CONTAINER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert container_endpoint_is_provably_local() is True


def test_env_docker_host_unix_is_local(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.delenv("CONTAINER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert container_endpoint_is_provably_local() is True


def test_env_docker_host_tcp_is_not_local(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_HOST", "tcp://h:2376")
    monkeypatch.delenv("CONTAINER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert container_endpoint_is_provably_local() is False


def test_env_container_host_ssh_is_not_local(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setenv("CONTAINER_HOST", "ssh://user@box/run/podman.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert container_endpoint_is_provably_local() is False


def test_env_container_host_unix_is_local(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setenv("CONTAINER_HOST", "unix:/run/user/1000/podman/podman.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert container_endpoint_is_provably_local() is True


def test_env_both_local_is_local(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.setenv("CONTAINER_HOST", "unix:/run/user/1000/podman/podman.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert container_endpoint_is_provably_local() is True


def test_env_docker_host_local_container_host_remote_is_not_local(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.setenv("CONTAINER_HOST", "ssh://user@box/run/podman.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert container_endpoint_is_provably_local() is False


def _pin_glob_order(monkeypatch, ordered):
    """Make the context-store scan visit ``ordered`` in exactly that order.

    `pathlib.Path.glob` yields `os.scandir` order and does not sort, so any test
    about "which entry wins" passes or fails on filesystem luck otherwise. Every
    such test here pins the order deliberately, worst case first.
    """
    real_glob = pathlib.Path.glob

    def _ordered(self, pattern, *args, **kwargs):
        if pattern == "contexts/meta/*/meta.json":
            return iter(ordered)
        return real_glob(self, pattern, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "glob", _ordered)


def _write_context_meta(tmp_path, dir_name, ctx_name, host):
    meta_dir = tmp_path / ".docker" / "contexts" / "meta" / dir_name
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "meta.json").write_text(
        f'{{"Name": "{ctx_name}", "Endpoints": {{"docker": {{"Host": "{host}"}}}}}}'
    )


def test_context_unix_host_is_local(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("CONTAINER_HOST", raising=False)
    monkeypatch.setenv("DOCKER_CONTEXT", "myctx")
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_context_meta(tmp_path, "abc123", "myctx", "unix:///var/run/docker.sock")
    assert container_endpoint_is_provably_local() is True


def test_context_tcp_host_is_not_local(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("CONTAINER_HOST", raising=False)
    monkeypatch.setenv("DOCKER_CONTEXT", "remotectx")
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_context_meta(
        tmp_path, "deadbeef", "remotectx", "tcp://engine.example.edu:2376"
    )
    assert container_endpoint_is_provably_local() is False


def test_context_default_name_is_local(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("CONTAINER_HOST", raising=False)
    monkeypatch.setenv("DOCKER_CONTEXT", "default")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert container_endpoint_is_provably_local() is True


def test_config_json_current_context_remote_is_not_local(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("CONTAINER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    docker_dir = tmp_path / ".docker"
    docker_dir.mkdir()
    (docker_dir / "config.json").write_text('{"currentContext": "remotectx"}')
    _write_context_meta(tmp_path, "abc", "remotectx", "tcp://engine.example.edu:2376")
    assert container_endpoint_is_provably_local() is False


def test_docker_host_wins_over_tcp_context(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.delenv("CONTAINER_HOST", raising=False)
    monkeypatch.setenv("DOCKER_CONTEXT", "remotectx")
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_context_meta(tmp_path, "abc", "remotectx", "tcp://engine.example.edu:2376")
    assert container_endpoint_is_provably_local() is True


def test_malformed_config_json_is_local(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("CONTAINER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    docker_dir = tmp_path / ".docker"
    docker_dir.mkdir()
    (docker_dir / "config.json").write_text("not valid json {{{{")
    assert container_endpoint_is_provably_local() is True


def test_missing_docker_dir_is_local(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("CONTAINER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert container_endpoint_is_provably_local() is True


def test_meta_json_no_matching_context_is_local(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("CONTAINER_HOST", raising=False)
    monkeypatch.setenv("DOCKER_CONTEXT", "myctx")
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_context_meta(
        tmp_path, "abc", "differentctx", "tcp://engine.example.edu:2376"
    )
    assert container_endpoint_is_provably_local() is True


def _write_connections(root, entries, default=None):
    """Write a Podman connection store under *root* and return its path."""
    store_dir = root / "containers"
    store_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "Connection": {
            "Default": default or next(iter(entries), ""),
            "Connections": {name: {"URI": uri} for name, uri in entries.items()},
        },
        "Farm": {},
    }
    path = store_dir / "podman-connections.json"
    path.write_text(json.dumps(payload))
    return path


# --- An empty DOCKER_HOST does not outrank a context (docker 29.7.2, measured) ---


def test_empty_docker_host_does_not_outrank_remote_context(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_HOST", "")
    monkeypatch.setenv("DOCKER_CONTEXT", "remotectx")
    _write_context_meta(tmp_path, "abc", "remotectx", "tcp://engine.example.edu:2376")
    assert container_endpoint_is_provably_local() is False


def test_whitespace_docker_host_does_not_outrank_remote_context(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_HOST", "   ")
    monkeypatch.setenv("DOCKER_CONTEXT", "remotectx")
    _write_context_meta(tmp_path, "abc", "remotectx", "tcp://engine.example.edu:2376")
    assert container_endpoint_is_provably_local() is False


def test_empty_docker_host_with_local_context_is_local(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_HOST", "")
    monkeypatch.setenv("DOCKER_CONTEXT", "localctx")
    _write_context_meta(tmp_path, "abc", "localctx", "unix:///var/run/docker.sock")
    assert container_endpoint_is_provably_local() is True


# --- DOCKER_CONFIG roots the context store ---


def test_docker_config_roots_the_context_store(monkeypatch, tmp_path):
    alt = tmp_path / "altdocker"
    monkeypatch.setenv("DOCKER_CONFIG", str(alt))
    monkeypatch.setenv("DOCKER_CONTEXT", "remotectx")
    meta_dir = alt / "contexts" / "meta" / "abc"
    meta_dir.mkdir(parents=True)
    (meta_dir / "meta.json").write_text(
        '{"Name": "remotectx", "Endpoints": {"docker": '
        '{"Host": "tcp://engine.example.edu:2376"}}}'
    )
    assert container_endpoint_is_provably_local() is False


def test_docker_config_roots_current_context(monkeypatch, tmp_path):
    alt = tmp_path / "altdocker"
    (alt / "contexts" / "meta" / "abc").mkdir(parents=True)
    (alt / "config.json").write_text('{"currentContext": "remotectx"}')
    (alt / "contexts" / "meta" / "abc" / "meta.json").write_text(
        '{"Name": "remotectx", "Endpoints": {"docker": '
        '{"Host": "tcp://engine.example.edu:2376"}}}'
    )
    monkeypatch.setenv("DOCKER_CONFIG", str(alt))
    assert container_endpoint_is_provably_local() is False


def test_docker_config_shadows_the_home_docker_directory(monkeypatch, tmp_path):
    """A remote context under ~/.docker is ignored once DOCKER_CONFIG points elsewhere."""
    docker_dir = tmp_path / ".docker"
    docker_dir.mkdir()
    (docker_dir / "config.json").write_text('{"currentContext": "remotectx"}')
    _write_context_meta(tmp_path, "abc", "remotectx", "tcp://engine.example.edu:2376")
    alt = tmp_path / "altdocker"
    alt.mkdir()
    (alt / "config.json").write_text("{}")
    monkeypatch.setenv("DOCKER_CONFIG", str(alt))
    assert container_endpoint_is_provably_local() is True


# --- CONTAINER_CONNECTION selects a Podman destination (podman 6.1.0, measured) ---


def test_container_connection_remote_uri_is_not_local(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _write_connections(
        tmp_path / "xdg", {"remotebox": "ssh://user@box.example.edu:22/run/podman.sock"}
    )
    monkeypatch.setenv("CONTAINER_CONNECTION", "remotebox")
    assert container_endpoint_is_provably_local() is False


def test_container_connection_local_uri_is_local(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _write_connections(
        tmp_path / "xdg", {"localsock": "unix:///run/user/1000/podman/podman.sock"}
    )
    monkeypatch.setenv("CONTAINER_CONNECTION", "localsock")
    assert container_endpoint_is_provably_local() is True


def test_container_connection_falls_back_to_home_config(monkeypatch, tmp_path):
    _write_connections(
        tmp_path / ".config",
        {"remotebox": "ssh://user@box.example.edu:22/run/podman.sock"},
    )
    monkeypatch.setenv("CONTAINER_CONNECTION", "remotebox")
    assert container_endpoint_is_provably_local() is False


def test_container_connection_with_no_store_is_not_local(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTAINER_CONNECTION", "remotebox")
    assert container_endpoint_is_provably_local() is False


def test_container_connection_unknown_name_is_not_local(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _write_connections(
        tmp_path / "xdg", {"othersock": "unix:///run/user/1000/podman/podman.sock"}
    )
    monkeypatch.setenv("CONTAINER_CONNECTION", "remotebox")
    assert container_endpoint_is_provably_local() is False


def test_container_connection_malformed_store_is_not_local(monkeypatch, tmp_path):
    store_dir = tmp_path / ".config" / "containers"
    store_dir.mkdir(parents=True)
    (store_dir / "podman-connections.json").write_text("not valid json {{{{")
    monkeypatch.setenv("CONTAINER_CONNECTION", "remotebox")
    assert container_endpoint_is_provably_local() is False


def test_container_connection_entry_without_uri_is_not_local(monkeypatch, tmp_path):
    store_dir = tmp_path / ".config" / "containers"
    store_dir.mkdir(parents=True)
    (store_dir / "podman-connections.json").write_text(
        '{"Connection": {"Connections": {"remotebox": "ssh://user@box/sock"}}}'
    )
    monkeypatch.setenv("CONTAINER_CONNECTION", "remotebox")
    assert container_endpoint_is_provably_local() is False


def test_empty_container_connection_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTAINER_CONNECTION", "   ")
    assert container_endpoint_is_provably_local() is True


def test_container_connection_outranks_a_local_docker_host(monkeypatch, tmp_path):
    """DOCKER_HOST cannot vouch for Podman: the named connection decides the endpoint."""
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _write_connections(
        tmp_path / "xdg", {"remotebox": "ssh://user@box.example.edu:22/run/podman.sock"}
    )
    monkeypatch.setenv("CONTAINER_CONNECTION", "remotebox")
    assert container_endpoint_is_provably_local() is False


# --- the never-raise contract holds for a malformed stored endpoint ---


def test_non_string_context_endpoint_is_not_local(monkeypatch, tmp_path):
    """A stored Host of the wrong type must not break the never-raise contract.

    The spec says the check never raises. A syntactically valid meta.json can still
    hold `"Host": 1`, and a type error there would abort an otherwise fine deploy.
    """
    monkeypatch.setenv("DOCKER_CONTEXT", "weirdctx")
    meta_dir = tmp_path / ".docker" / "contexts" / "meta" / "abc"
    meta_dir.mkdir(parents=True)
    (meta_dir / "meta.json").write_text(
        '{"Name": "weirdctx", "Endpoints": {"docker": {"Host": 1}}}'
    )
    assert container_endpoint_is_provably_local() is False


def test_non_string_endpoint_is_not_local():
    assert endpoint_is_local(1) is False
    assert endpoint_is_local(["unix:///var/run/docker.sock"]) is False


def test_unrelated_non_object_meta_json_does_not_hide_a_remote_context(
    monkeypatch, tmp_path
):
    """A stale sibling holding valid non-object JSON must not abort the scan.

    `json.loads("[]")` succeeds, so the per-file guard passes it through, and the
    `.get("Name")` that follows raises AttributeError on a list. That escapes to the
    function's outer handler, which returns None -- and a None endpoint is read by the
    caller as "no context configured", so the selected remote context is never seen
    and the CLI machine gets stamped as the deployment host.

    Docker addresses the selected context independently of an unrelated stale entry,
    so this must refuse. `pathlib.Path.glob` yields os.scandir order and does not sort,
    which makes the visit order filesystem luck; the order is pinned here so the test
    cannot pass by finding the good entry first.
    """
    monkeypatch.setenv("DOCKER_CONTEXT", "remotebox")
    meta_root = tmp_path / ".docker" / "contexts" / "meta"

    stale = meta_root / "stale"
    stale.mkdir(parents=True)
    (stale / "meta.json").write_text("[]")

    selected = meta_root / "selected"
    selected.mkdir(parents=True)
    (selected / "meta.json").write_text(
        json.dumps(
            {
                "Name": "remotebox",
                "Endpoints": {"docker": {"Host": "ssh://user@remote.example.com"}},
            }
        )
    )

    ordered = [stale / "meta.json", selected / "meta.json"]
    real_glob = pathlib.Path.glob

    def _stale_first(self, pattern, *args, **kwargs):
        if pattern == "contexts/meta/*/meta.json":
            return iter(ordered)
        return real_glob(self, pattern, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "glob", _stale_first)
    assert container_endpoint_is_provably_local() is False


def test_non_object_endpoint_metadata_does_not_raise(monkeypatch, tmp_path):
    """The nested lookups have the same shape problem as the top-level one.

    `Endpoints` and `Endpoints.docker` are read with `.get` too, so valid JSON that
    puts a list at either level would raise inside the loop and fail open the same way.
    """
    monkeypatch.setenv("DOCKER_CONTEXT", "remotebox")
    meta_dir = tmp_path / ".docker" / "contexts" / "meta" / "abc"
    meta_dir.mkdir(parents=True)
    (meta_dir / "meta.json").write_text(
        json.dumps({"Name": "remotebox", "Endpoints": ["not", "a", "mapping"]})
    )
    # No endpoint can be read, and no exception escapes; with nothing else configured
    # the absent endpoint is not evidence of a remote engine.
    assert container_endpoint_is_provably_local() is True


def test_non_object_docker_endpoint_does_not_raise(monkeypatch, tmp_path):
    """`Endpoints.docker` is read with `.get` too and needs the same guard."""
    monkeypatch.setenv("DOCKER_CONTEXT", "remotebox")
    meta_dir = tmp_path / ".docker" / "contexts" / "meta" / "abc"
    meta_dir.mkdir(parents=True)
    (meta_dir / "meta.json").write_text(
        json.dumps({"Name": "remotebox", "Endpoints": {"docker": ["nope"]}})
    )
    assert container_endpoint_is_provably_local() is True


def test_unparseable_sibling_meta_json_does_not_hide_a_remote_context(
    monkeypatch, tmp_path
):
    """A sibling that is not JSON at all is skipped, not fatal to the scan.

    Same failure shape as the non-object case, but caught one branch earlier. Pinned
    separately because the two are guarded by different lines.
    """
    monkeypatch.setenv("DOCKER_CONTEXT", "remotebox")
    meta_root = tmp_path / ".docker" / "contexts" / "meta"

    stale = meta_root / "stale"
    stale.mkdir(parents=True)
    (stale / "meta.json").write_text("not valid json {{{{")

    selected = meta_root / "selected"
    selected.mkdir(parents=True)
    (selected / "meta.json").write_text(
        json.dumps(
            {
                "Name": "remotebox",
                "Endpoints": {"docker": {"Host": "ssh://user@remote.example.com"}},
            }
        )
    )

    ordered = [stale / "meta.json", selected / "meta.json"]
    real_glob = pathlib.Path.glob

    def _stale_first(self, pattern, *args, **kwargs):
        if pattern == "contexts/meta/*/meta.json":
            return iter(ordered)
        return real_glob(self, pattern, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "glob", _stale_first)
    assert container_endpoint_is_provably_local() is False


def test_conflicting_duplicate_context_entries_refuse(monkeypatch, tmp_path):
    """Two entries claiming the selected name, disagreeing, must not resolve to either.

    The scan matches on the `Name` a file states, and takes the first hit in
    `os.scandir` order. A stale duplicate naming a local socket would therefore hide
    the real remote entry and stamp the CLI machine -- the same fail-open the
    malformed-sibling fix closed, reached by a different route.
    """
    monkeypatch.setenv("DOCKER_CONTEXT", "remotebox")
    _write_context_meta(tmp_path, "stale", "remotebox", "unix:///var/run/docker.sock")
    _write_context_meta(tmp_path, "real", "remotebox", "ssh://user@remote.example.com")
    # Order pinned local-first: glob() does not sort, so without this the test
    # passes whenever the filesystem happens to yield the remote entry first.
    _pin_glob_order(
        monkeypatch,
        [
            tmp_path / ".docker" / "contexts" / "meta" / "stale" / "meta.json",
            tmp_path / ".docker" / "contexts" / "meta" / "real" / "meta.json",
        ],
    )
    assert container_endpoint_is_provably_local() is False


@pytest.mark.parametrize(
    "stale_host", ["[]", "{}", '["unix:///var/run/docker.sock"]', '{"a": 1}']
)
def test_an_unhashable_duplicate_host_does_not_abort_the_scan(
    monkeypatch, tmp_path, stale_host
):
    """A same-name entry whose `Host` is a list or object must not fail open.

    The duplicate check deduplicates the matches, and a `set` of them raises
    `TypeError` on a list or dict. That escapes to the outer handler, which returns
    None, and the caller reads a None endpoint as "no context configured" -- so the
    valid remote entry alongside it goes unseen and the CLI machine gets stamped.

    Introduced by the duplicate-refusal fix itself: the earlier non-string guard
    covered a hashable `1`, and conflicting *strings* were the only conflict shape
    tested, so both new guards had a hole between them.
    """
    monkeypatch.setenv("DOCKER_CONTEXT", "remotebox")
    meta_root = tmp_path / ".docker" / "contexts" / "meta"

    stale = meta_root / "stale"
    stale.mkdir(parents=True)
    (stale / "meta.json").write_text(
        '{"Name": "remotebox", "Endpoints": {"docker": {"Host": ' + stale_host + "}}}"
    )
    _write_context_meta(tmp_path, "real", "remotebox", "ssh://user@remote.example.com")

    _pin_glob_order(
        monkeypatch,
        [stale / "meta.json", meta_root / "real" / "meta.json"],
    )
    assert container_endpoint_is_provably_local() is False


def test_a_lone_unhashable_context_host_is_not_local(monkeypatch, tmp_path):
    """The same shape with no sibling: unclassifiable is not evidence of local."""
    monkeypatch.setenv("DOCKER_CONTEXT", "remotebox")
    meta_dir = tmp_path / ".docker" / "contexts" / "meta" / "abc"
    meta_dir.mkdir(parents=True)
    (meta_dir / "meta.json").write_text(
        '{"Name": "remotebox", "Endpoints": {"docker": {"Host": ["nope"]}}}'
    )
    assert container_endpoint_is_provably_local() is False


def test_agreeing_duplicate_context_entries_still_classify(monkeypatch, tmp_path):
    """Duplicates that agree are not ambiguous, so they resolve normally."""
    monkeypatch.setenv("DOCKER_CONTEXT", "myctx")
    _write_context_meta(tmp_path, "a", "myctx", "unix:///var/run/docker.sock")
    _write_context_meta(tmp_path, "b", "myctx", "unix:///var/run/docker.sock")
    assert container_endpoint_is_provably_local() is True


def test_a_raising_context_store_is_no_evidence_of_a_remote_engine(monkeypatch):
    """The outer handler is the never-raise backstop and must stay reachable.

    Every inner path is guarded now, so nothing in the loop is expected to raise. An
    unreadable store still must not abort a deploy: the spec says an unresolvable
    configuration is not evidence of a remote engine, because Docker falls back to the
    local default context.
    """
    monkeypatch.setenv("DOCKER_CONTEXT", "remotebox")

    def _explode(self, pattern, *args, **kwargs):
        raise OSError("context store unreadable")

    monkeypatch.setattr(pathlib.Path, "glob", _explode)
    assert container_endpoint_is_provably_local() is True


def test_non_string_podman_connection_uri_is_not_local(monkeypatch, tmp_path):
    store_dir = tmp_path / ".config" / "containers"
    store_dir.mkdir(parents=True)
    (store_dir / "podman-connections.json").write_text(
        '{"Connection": {"Connections": {"remotebox": {"URI": 7}}}}'
    )
    monkeypatch.setenv("CONTAINER_CONNECTION", "remotebox")
    assert container_endpoint_is_provably_local() is False


# --- a named connection outranks CONTAINER_HOST (podman 6.1.0, measured) ---


def test_local_connection_outranks_a_stale_remote_container_host(monkeypatch, tmp_path):
    """CONTAINER_CONNECTION decides the podman endpoint, so CONTAINER_HOST cannot refuse for it."""
    monkeypatch.setenv("CONTAINER_HOST", "ssh://user@stale/run/podman.sock")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _write_connections(
        tmp_path / "xdg", {"localsock": "unix:///run/user/1000/podman/podman.sock"}
    )
    monkeypatch.setenv("CONTAINER_CONNECTION", "localsock")
    assert container_endpoint_is_provably_local() is True


def test_remote_container_host_still_refuses_without_a_connection(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CONTAINER_HOST", "ssh://user@box/run/podman.sock")
    assert container_endpoint_is_provably_local() is False
