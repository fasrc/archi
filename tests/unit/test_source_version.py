"""Unit tests for the best-effort archi source-commit resolver.

The git invocation is patched so these tests never depend on the runner's own git
state. They cover the spec scenarios: clean checkout -> short SHA, dirty checkout ->
``<sha>-dirty``, and non-git / git-unavailable -> the ``unknown`` sentinel without
raising.
"""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from src.cli.managers.source_version import resolve_source_commit


def _completed(stdout):
    return SimpleNamespace(stdout=stdout, returncode=0)


def test_clean_checkout_returns_short_sha():
    with patch(
        "src.cli.managers.source_version.subprocess.run",
        side_effect=[_completed("936a52f8\n"), _completed("")],
    ):
        assert resolve_source_commit("/some/repo") == "936a52f8"


def test_dirty_checkout_returns_dirty_suffix():
    with patch(
        "src.cli.managers.source_version.subprocess.run",
        side_effect=[_completed("936a52f8\n"), _completed(" M src/foo.py\n")],
    ):
        assert resolve_source_commit("/some/repo") == "936a52f8-dirty"


def test_non_git_path_returns_unknown():
    err = subprocess.CalledProcessError(128, ["git", "rev-parse"])
    with patch("src.cli.managers.source_version.subprocess.run", side_effect=err):
        assert resolve_source_commit("/not/a/repo") == "unknown"


def test_git_unavailable_returns_unknown():
    with patch(
        "src.cli.managers.source_version.subprocess.run",
        side_effect=FileNotFoundError("git"),
    ):
        assert resolve_source_commit("/some/repo") == "unknown"


def test_empty_sha_returns_unknown():
    with patch(
        "src.cli.managers.source_version.subprocess.run",
        side_effect=[_completed("\n")],
    ):
        assert resolve_source_commit("/some/repo") == "unknown"


def test_helper_never_raises_on_unexpected_error():
    with patch(
        "src.cli.managers.source_version.subprocess.run",
        side_effect=RuntimeError("boom"),
    ):
        assert resolve_source_commit("/some/repo") == "unknown"


def test_default_repo_root_does_not_raise():
    # Called with no argument the helper derives its own repo root and must still
    # return a string without raising, regardless of the runner's git state.
    result = resolve_source_commit()
    assert isinstance(result, str)
    assert result
