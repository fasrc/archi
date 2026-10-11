"""Tests that get_git_information and capture_git_diff pass env=git_env().

Design D3: every git call strips the eight REPO_SELECTION_VARS so that an
inherited GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE does not shadow wd=.
"""

import os
import subprocess
from pathlib import Path

import pytest

from src.cli.managers.git_diff_capture import capture_git_diff
from src.cli.managers.templates_manager import get_git_information


def _git(args, *, cwd):
    """Run git without inheriting any GIT_* variables (design D3)."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.check_call(["git"] + args, cwd=str(cwd), env=clean)


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

    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
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
