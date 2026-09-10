import pytest

from src.utils.container_endpoint import (
    container_endpoint_is_provably_local,
    endpoint_is_local,
)


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
