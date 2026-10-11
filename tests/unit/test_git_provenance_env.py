"""Tests that get_git_information and capture_git_diff pass env=git_env().

Design D3: every git call strips the eight REPO_SELECTION_VARS so that an
inherited GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE does not shadow wd=.
"""

import os
import subprocess
from pathlib import Path

import pytest

import src.cli.managers.templates_manager as _tm
from src.cli.managers.git_diff_capture import capture_git_diff
from src.cli.managers.source_version import resolve_source_commit
from src.cli.managers.templates_manager import get_git_information, get_git_version


def _clean_env():
    """Drop inherited GIT_* variables and ignore global and system git config."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    clean["GIT_CONFIG_GLOBAL"] = os.devnull
    clean["GIT_CONFIG_NOSYSTEM"] = "1"
    return clean


def _git(args, *, cwd):
    """Run git without inheriting any GIT_* variables (design D3)."""
    subprocess.check_call(["git"] + args, cwd=str(cwd), env=_clean_env())


@pytest.fixture
def poisoned_repos(tmp_path, monkeypatch):
    """Return (repo_a, a_head) with GIT_DIR/WORK_TREE/INDEX_FILE pointing at repo B.

    Repo A has one commit plus an uncommitted modification to readme.txt.
    Repo B has a different commit and no uncommitted changes.
    After building both, monkeypatches the three REPO_SELECTION_VARS to B so
    the test can verify that wd= governs the result, not the inherited env.
    """
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    repo_a.mkdir()
    repo_b.mkdir()

    for cmd in [
        ["init", "-b", "main"],
        ["config", "user.email", "a@test.com"],
        ["config", "user.name", "A"],
    ]:
        _git(cmd, cwd=repo_a)
    (repo_a / "readme.txt").write_text("initial\n")
    _git(["add", "readme.txt"], cwd=repo_a)
    _git(["commit", "-m", "init A"], cwd=repo_a)
    (repo_a / "readme.txt").write_text("modified by A\n")

    clean = _clean_env()
    a_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_a), encoding="UTF-8", env=clean
    ).strip()

    for cmd in [
        ["init", "-b", "main"],
        ["config", "user.email", "b@test.com"],
        ["config", "user.name", "B"],
    ]:
        _git(cmd, cwd=repo_b)
    (repo_b / "other.txt").write_text("repo B content\n")
    _git(["add", "other.txt"], cwd=repo_b)
    _git(["commit", "-m", "init B"], cwd=repo_b)

    monkeypatch.setenv("GIT_DIR", str(repo_b / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(repo_b))
    monkeypatch.setenv("GIT_INDEX_FILE", str(repo_b / ".git" / "index"))

    return repo_a, a_head


def test_get_git_information_reads_wd_not_env(poisoned_repos):
    repo_a, a_head = poisoned_repos
    info = get_git_information(wd=repo_a)
    assert info["last_commit"].strip() == a_head
    assert "modified by A" in info["git_diff"]
    assert "repo B content" not in info["git_diff"]


def test_capture_git_diff_reads_wd_not_env(poisoned_repos):
    repo_a, a_head = poisoned_repos
    result = capture_git_diff(repo_a)
    assert "modified by A" in result["git_diff"]
    assert "repo B content" not in result["git_diff"]


def test_resolve_source_commit_reads_repo_root_not_env(poisoned_repos):
    repo_a, _a_head = poisoned_repos
    clean = _clean_env()
    a_short = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(repo_a),
        encoding="UTF-8",
        env=clean,
    ).strip()

    # repo_a has an uncommitted modification, so result must be <sha>-dirty
    result = resolve_source_commit(repo_a)
    assert result == f"{a_short}-dirty", f"expected {a_short}-dirty, got {result!r}"

    # commit the pending change; result must then be the bare short sha
    _git(["add", "readme.txt"], cwd=repo_a)
    _git(["commit", "-m", "second A"], cwd=repo_a)
    clean2 = _clean_env()
    a_short2 = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(repo_a),
        encoding="UTF-8",
        env=clean2,
    ).strip()
    result2 = resolve_source_commit(repo_a)
    assert result2 == a_short2, f"expected {a_short2}, got {result2!r}"


def test_git_helper_ignores_global_config(tmp_path, monkeypatch):
    """A global commit.gpgSign=true must not break the fixture commits."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        "[commit]\n\tgpgSign = true\n[gpg]\n\tprogram = false\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in [
        ["init", "-b", "main"],
        ["config", "user.email", "c@test.com"],
        ["config", "user.name", "C"],
    ]:
        _git(cmd, cwd=repo)
    (repo / "f.txt").write_text("x\n")
    _git(["add", "f.txt"], cwd=repo)
    _git(["commit", "-m", "init"], cwd=repo)


def test_get_git_version_passes_clean_env(monkeypatch):
    captured = {}

    def fake_check_output(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return b"v1.2.3"

    monkeypatch.setenv("GIT_DIR", "/fake/poisoned/path")
    monkeypatch.setattr(_tm.subprocess, "check_output", fake_check_output)

    get_git_version()

    assert (
        "env" in captured
    ), "get_git_version did not pass env= to subprocess.check_output"
    assert captured["env"] is not None, "env= must not be None"
    assert (
        "GIT_DIR" not in captured["env"]
    ), "GIT_DIR must be stripped from the env passed to git"
