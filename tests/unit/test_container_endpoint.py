import json

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
