"""Resolve the archi source git commit for deployment provenance.

Best-effort and never fatal: :func:`resolve_source_commit` returns a short commit
identifier, with a ``-dirty`` suffix when the working tree has uncommitted changes,
or the sentinel ``unknown`` on any failure (a non-git checkout, a missing ``git``
binary, or any subprocess error). It MUST NOT raise under any circumstances so a
deploy from a non-git directory still succeeds.
"""

import subprocess
from pathlib import Path

UNKNOWN = "unknown"


def _default_repo_root():
    # src/cli/managers/source_version.py -> parents[2] == the ``src`` package root,
    # which sits inside the archi working tree; ``git`` searches upward for ``.git``.
    return Path(__file__).resolve().parents[2]


def resolve_source_commit(repo_root=None):
    """Return the short archi source commit, ``<sha>-dirty``, or ``unknown``.

    ``repo_root`` defaults to the archi package root derived from ``__file__`` so the
    resolved commit matches the code that ``pip install .`` ships.
    """
    if repo_root is None:
        repo_root = _default_repo_root()
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if not sha:
            return UNKNOWN
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return f"{sha}-dirty" if status.strip() else sha
    except Exception:
        return UNKNOWN
