"""Repo invariants: properties of this checkout that must stay true.

Unlike the rest of ``tests/unit``, these deliberately inspect the REAL git
repository instead of a patched or temporary one. The invariant here is "this repo
does not track that path", which no fixture can express — a temporary repo would
only prove something about the fixture.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*args):
    """Run git in the repo root and hand back the CompletedProcess."""
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _rule_ignores(path):
    """Does an ignore RULE match ``path``, independent of tracking status?

    ``--no-index`` is required and not optional politeness. Plain
    ``git check-ignore`` consults the index first and reports a TRACKED path as
    "not ignored" no matter which patterns match it. Every
    ``openspec/changes/**/tasks.md`` is tracked, so without this flag the
    anchoring test below passes even when the pattern is wrong — which is exactly
    how it was first written, and the mutation check caught it.
    """
    return _git("check-ignore", "--no-index", "-q", path).returncode == 0


pytestmark = pytest.mark.skipif(
    _git("rev-parse", "--git-dir").returncode != 0,
    reason="not a git checkout, so there are no git invariants to assert",
)


class TestRootTasksMdStaysUntracked:
    """Root ``tasks.md`` is the Ralph harness's live pointer at whichever OpenSpec
    change is active (``ralph.conf``: ``RALPH_TASKS="tasks.md"``), so it is a
    symlink whose target differs on every branch. Tracking it therefore made every
    branch's version conflict with every other's: ``ca31e80d`` (#164) dropped it
    from ``dev``, which turned it into a modify/delete conflict that blocked five
    open PRs at once (#159-#163, cleared under #168). It is legitimate working-tree
    state and must never be tracked again.
    """

    def test_root_tasks_md_is_ignored(self):
        assert _rule_ignores("tasks.md"), (
            "root tasks.md must stay gitignored. It is a per-branch symlink, so "
            "tracking it makes every branch conflict with every other one — see "
            "issue #168, which cleared exactly that off five PRs."
        )

    def test_root_tasks_md_is_not_tracked(self):
        assert _git("ls-files", "--error-unmatch", "tasks.md").returncode != 0, (
            "root tasks.md is tracked again, which recreates the modify/delete "
            "conflict that blocked PRs #159-#163. Drop it with `git rm --cached "
            "tasks.md`; the working-tree symlink itself should stay."
        )

    def test_the_ignore_rule_is_root_anchored(self):
        """The rule must be ``/tasks.md``, not ``tasks.md``.

        Without the leading slash the pattern matches at every depth and silently
        untracks all 29 ``openspec/changes/**/tasks.md`` files, which ARE the
        source of truth the root symlink merely points at. This is the one-character
        mistake that would turn the fix into a much worse regression, so it gets its
        own test rather than trusting the pattern by eye.
        """
        tracked = [
            line
            for line in _git("ls-files").stdout.splitlines()
            if line.endswith("/tasks.md")
        ]
        assert tracked, "expected OpenSpec change task lists to be tracked"
        for path in tracked:
            assert not _rule_ignores(path), (
                f"{path} is matched by an ignore rule, so the tasks.md rule is not "
                "root-anchored. Use `/tasks.md`, not `tasks.md`."
            )
