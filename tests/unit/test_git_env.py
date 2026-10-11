import os

import pytest

from src.cli.managers.git_env import REPO_SELECTION_VARS, git_env

_ALL_EIGHT = [
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
]


def test_repo_selection_vars_contains_all_eight():
    assert set(_ALL_EIGHT) == set(REPO_SELECTION_VARS)


def test_git_env_omits_all_repo_selection_vars(monkeypatch):
    for name in _ALL_EIGHT:
        monkeypatch.setenv(name, "dummy_value")
    result = git_env()
    for name in _ALL_EIGHT:
        assert name not in result


def test_git_env_keeps_passthrough_vars(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/some/path")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    result = git_env()
    assert result["GIT_CONFIG_GLOBAL"] == "/some/path"
    assert result["GIT_CONFIG_NOSYSTEM"] == "1"
    assert result["PATH"] == "/usr/bin:/bin"


def test_git_env_reads_environ_at_call_time(monkeypatch):
    monkeypatch.setenv("ARCHI_TEST_LATE_SET", "present")
    result = git_env()
    assert result.get("ARCHI_TEST_LATE_SET") == "present"


def test_git_env_returns_new_dict():
    result = git_env()
    result["INJECTED_KEY"] = "should_not_leak"
    assert "INJECTED_KEY" not in os.environ
