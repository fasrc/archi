import pytest

from src.utils.container_endpoint import endpoint_is_local


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
