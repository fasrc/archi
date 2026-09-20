"""Capture and bound the git diff for benchmark artifact metadata."""

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

GIT_DIFF_MAX_BYTES = 256_000
GIT_DIFF_STAT_WIDTH = 120
GIT_DIFF_STAT_MAX_FILES = 200
GIT_DIFF_EXCLUDED_TOP_LEVEL_DIRS = ("bench_out",)


def git_diff_pathspec() -> List[str]:
    """Return ['--', ':(top,exclude)bench_out'] — one exclude per entry, all top-anchored."""
    return ["--"] + [f":(top,exclude){d}" for d in GIT_DIFF_EXCLUDED_TOP_LEVEL_DIRS]


def bound_text(text: str, max_bytes: int) -> Tuple[str, bool, int]:
    """Return (kept, truncated, original_bytes).

    Keeps the longest newline-aligned prefix of at most max_bytes bytes.
    Falls back to a character-safe prefix when no newline fits within the cap.
    """
    if text == "":
        return ("", False, 0)
    encoded = text.encode("utf-8")
    original_bytes = len(encoded)
    if original_bytes <= max_bytes:
        return (text, False, original_bytes)
    cut = encoded.rfind(b"\n", 0, max_bytes)
    if cut >= 0:
        kept = encoded[: cut + 1].decode("utf-8")
    else:
        kept = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return (kept, True, original_bytes)


def capture_git_diff(wd: Path) -> Dict[str, Any]:
    """Return the four diff keys for benchmark artifact metadata."""
    pathspec = git_diff_pathspec()
    diff_raw = subprocess.check_output(
        ["git", "diff", "--no-ext-diff", "--no-color"] + pathspec,
        cwd=wd,
        encoding="UTF-8",
    )
    stat_raw = subprocess.check_output(
        [
            "git",
            "diff",
            "--no-ext-diff",
            "--no-color",
            f"--stat={GIT_DIFF_STAT_WIDTH},,{GIT_DIFF_STAT_MAX_FILES}",
        ]
        + pathspec,
        cwd=wd,
        encoding="UTF-8",
    )
    git_diff, git_diff_truncated, git_diff_original_bytes = bound_text(
        diff_raw, GIT_DIFF_MAX_BYTES
    )
    return {
        "git_diff": git_diff,
        "git_diff_stat": stat_raw,
        "git_diff_truncated": git_diff_truncated,
        "git_diff_original_bytes": git_diff_original_bytes,
    }
