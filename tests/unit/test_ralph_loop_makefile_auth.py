"""The Ralph loop Makefile forwards a long-lived Claude token and can probe auth.

The loop container used to authenticate only through an interactive ``claude login``
whose credential lived in ``.ralph/claude-home`` and stopped refreshing after a few
weeks (2026-08-24 and 2026-09-27: every nightly turn died on ``401 OAuth access token
has expired``). The durable path is a one-year token from ``claude setup-token``,
supplied to the container as ``CLAUDE_CODE_OAUTH_TOKEN``. These tests pin the two
Makefile facts that path depends on, by reading the Makefile as text (the recipes
invoke podman, so they are not runnable in the unit suite):

* ``RUN_FLAGS`` forwards ``CLAUDE_CODE_OAUTH_TOKEN`` the way it forwards ``GH_TOKEN``:
  a bare ``-e NAME`` with no inline value, so the secret never appears in ``ps`` or
  in the Makefile, and an unset host variable is simply not passed;
* an ``auth-check`` target exists for the pre-run probe, and it runs the container
  WITHOUT ``-it`` (systemd ``ExecStartPre`` has no TTY).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"


def _makefile() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _run_flags_block(text: str) -> str:
    match = re.search(r"^RUN_FLAGS\s*:=\s*\\\n((?:.*\\\n)*.*)$", text, re.MULTILINE)
    assert match, "RUN_FLAGS block not found in Makefile"
    return match.group(1)


def _target_recipe(text: str, target: str) -> str:
    match = re.search(
        rf"^{re.escape(target)}:[^\n]*\n((?:\t.*\n)+)", text, re.MULTILINE
    )
    assert match, f"target {target!r} not found in Makefile"
    return match.group(1)


def test_run_flags_forward_the_oauth_token_without_an_inline_value():
    flags = _run_flags_block(_makefile())
    assert re.search(r"-e CLAUDE_CODE_OAUTH_TOKEN(\s|\\|$)", flags), flags
    assert (
        "CLAUDE_CODE_OAUTH_TOKEN=" not in flags
    ), "token must be forwarded, never inlined"


def test_run_flags_still_forward_the_github_token_the_same_way():
    flags = _run_flags_block(_makefile())
    assert re.search(r"-e GH_TOKEN(\s|\\|$)", flags), flags
    assert "GH_TOKEN=" not in flags


def test_auth_check_target_runs_the_container_without_a_tty():
    recipe = _target_recipe(_makefile(), "auth-check")
    assert "$(RUN_FLAGS)" in recipe
    assert (
        "-it" not in recipe
    ), "ExecStartPre has no TTY; the probe must not ask for one"
    assert "claude" in recipe


def test_auth_check_target_is_bounded_by_a_timeout():
    """A hung pull or API call must not hold the systemd unit for its whole
    TimeoutStartSec (5 h); the recipe wraps the container run in `timeout` and
    removes the named container if it lingers."""
    text = _makefile()
    recipe = _target_recipe(text, "auth-check")
    assert re.search(r"^AUTH_CHECK_TIMEOUT\s*\?=\s*\d+\s*$", text, re.MULTILINE), text
    assert re.search(r"\btimeout\b.*\$\(AUTH_CHECK_TIMEOUT\)", recipe), recipe
    assert "rm -f $(IMAGE)-auth-check" in recipe


def test_login_target_is_kept_as_the_fallback():
    recipe = _target_recipe(_makefile(), "login")
    assert "claude login" in recipe
