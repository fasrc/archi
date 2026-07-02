"""Unit tests for the best-effort archi source-commit resolver.

The git invocation is patched so these tests never depend on the runner's own git
state. They cover the spec scenarios: clean checkout -> short SHA, dirty checkout ->
``<sha>-dirty``, and non-git / git-unavailable -> the ``unknown`` sentinel without
raising.
"""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from src.cli.managers import source_version
from src.cli.managers.source_version import resolve_source_commit, write_source_commit


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


def test_default_repo_root_uses_recorded_checkout_path():
    # With no explicit repo_root, the resolver must run git against the same
    # checkout ``copy_source_code`` ships from — the path setup.py records in
    # ``_repository_info.REPO_PATH`` — so a non-editable ``pip install .`` (where
    # this module lives under site-packages with no ``.git``) still resolves the
    # real commit instead of ``unknown``.
    captured = {}

    def _fake_run(cmd, cwd, **kwargs):
        captured["cwd"] = cwd
        return _completed("936a52f8\n") if "rev-parse" in cmd else _completed("")

    with patch("src.cli.utils._repository_info.REPO_PATH", "/recorded/checkout"):
        with patch(
            "src.cli.managers.source_version.subprocess.run", side_effect=_fake_run
        ):
            assert resolve_source_commit() == "936a52f8"

    assert captured["cwd"] == "/recorded/checkout"


def test_default_repo_root_falls_back_when_recorded_path_unavailable():
    # If the recorded checkout path cannot be imported/read, fall back to the
    # file-derived package root rather than raising.
    def _fake_run(cmd, cwd, **kwargs):
        return _completed("936a52f8\n") if "rev-parse" in cmd else _completed("")

    with patch.object(
        source_version, "_recorded_repo_root", side_effect=RuntimeError("no info")
    ):
        with patch(
            "src.cli.managers.source_version.subprocess.run", side_effect=_fake_run
        ):
            assert resolve_source_commit() == "936a52f8"


def test_write_source_commit_writes_file_and_returns_value(tmp_path):
    with patch(
        "src.cli.managers.source_version.resolve_source_commit",
        return_value="936a52f8-dirty",
    ):
        result = write_source_commit(tmp_path, repo_root="/some/repo")

    assert result == "936a52f8-dirty"
    assert (tmp_path / "SOURCE_COMMIT").read_text().strip() == "936a52f8-dirty"


def test_write_source_commit_never_raises_on_io_error(tmp_path):
    # Best-effort: a write failure must not propagate (it must never break a deploy).
    def _boom(*args, **kwargs):
        raise OSError("disk full")

    with patch(
        "src.cli.managers.source_version.resolve_source_commit",
        return_value="936a52f8",
    ):
        with patch("src.cli.managers.source_version.Path.write_text", _boom):
            # Must not raise.
            result = write_source_commit(tmp_path, repo_root="/some/repo")

    assert result == "936a52f8"
    assert not (tmp_path / "SOURCE_COMMIT").exists()
