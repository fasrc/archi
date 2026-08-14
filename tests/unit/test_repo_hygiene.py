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


class TestArgillaRostersStayUntracked:
    """Argilla rosters are operator data, not repository content.

    ``create_argilla_users.py`` takes a CSV of real people — name, institutional
    email, and an optional credential. A filled-in ``argilla/argilla_users.csv`` was
    committed to this repo once already, which is why the invariant is enforced here
    rather than left to reviewer attention. A roster belongs outside the checkout
    (``argilla/argilla_users.csv.example`` documents where), and only the template is
    tracked.
    """

    def test_no_tracked_roster_files_under_argilla(self):
        """The general form of the defect, not just the one file that hit it.

        Asserting on ``argilla_users.csv`` by name would pass again the moment
        someone commits ``evaluators.csv`` — and the script's own docstring suggests
        exactly that spelling. The invariant is that NO roster-shaped file is
        tracked, so the assertion is over the extensions the script reads.
        """
        rosters = [
            line
            for line in _git("ls-files", "argilla").stdout.splitlines()
            if line.endswith((".csv", ".txt"))
        ]
        assert not rosters, (
            f"roster file(s) tracked under argilla/: {rosters}. These hold real "
            "names, emails, and sometimes plaintext passwords in a public repo. "
            "Drop them with `git rm --cached <path>`; keep the working-tree copy."
        )

    def test_roster_csvs_are_ignored(self):
        assert _rule_ignores("argilla/argilla_users.csv"), (
            "argilla/*.csv must stay gitignored so a filled-in roster cannot be "
            "committed. Untracking without the ignore rule only defers the leak to "
            "the next `git add -A`."
        )

    def test_the_example_template_stays_tracked(self):
        """The mirror-image mistake: a rule broad enough to swallow the template.

        ``argilla/*`` or ``argilla/argilla_users*`` would untrack the very file that
        tells an operator where a roster belongs, along with the README and the
        script — turning a leak fix into silent documentation loss. The negation
        ``!argilla/*.example`` exists for this, and is worth a test rather than an
        eyeball.
        """
        template = "argilla/argilla_users.csv.example"
        assert _git("ls-files", "--error-unmatch", template).returncode == 0, (
            f"{template} must stay tracked — it is the only in-repo record of the "
            "roster format and of where a real roster belongs."
        )
        assert not _rule_ignores(template), (
            f"{template} is matched by an ignore rule, so the roster pattern is too "
            "broad. Keep the `!argilla/*.example` negation."
        )
