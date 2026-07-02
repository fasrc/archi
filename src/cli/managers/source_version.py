"""Resolve the archi source git commit for deployment provenance.

Best-effort and never fatal: :func:`resolve_source_commit` returns a short commit
identifier, with a ``-dirty`` suffix when the working tree has uncommitted changes,
or the sentinel ``unknown`` on any failure (a non-git checkout, a missing ``git``
binary, or any subprocess error). It MUST NOT raise under any circumstances so a
deploy from a non-git directory still succeeds.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"

SOURCE_COMMIT_FILENAME = "SOURCE_COMMIT"


def _file_derived_repo_root():
    # src/cli/managers/source_version.py -> parents[2] == the ``src`` package root,
    # which sits inside the archi working tree; ``git`` searches upward for ``.git``.
    return Path(__file__).resolve().parents[2]


def _recorded_repo_root():
    """Return the checkout path ``setup.py`` recorded at install time.

    This is the same path :func:`copy_source_code` ships from, so provenance matches
    the code that actually lands in the image. Under a non-editable ``pip install .``
    this module lives under ``site-packages`` (no ``.git``), but ``REPO_PATH`` still
    points at the original checkout.
    """
    from src.cli.utils import _repository_info

    return Path(_repository_info.REPO_PATH)


def _default_repo_root():
    # Prefer the recorded checkout path; fall back to the file-derived package root
    # (e.g. an editable install whose _repository_info is unavailable).
    try:
        return _recorded_repo_root()
    except Exception:
        return _file_derived_repo_root()


def resolve_source_commit(repo_root=None):
    """Return the short archi source commit, ``<sha>-dirty``, or ``unknown``.

    ``repo_root`` defaults to the checkout path recorded at install time (the same
    source ``copy_source_code`` ships), falling back to the ``__file__``-derived
    package root, so the resolved commit matches the code that ``pip install .``
    ships even for a non-editable install.
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


def write_source_commit(base_dir, repo_root=None):
    """Resolve and write ``base_dir/SOURCE_COMMIT``; return the resolved value.

    Tied to the source-copy/build path: callers pass the ``repo_root`` the source was
    just copied from so the recorded commit reflects the code that lands in the image.
    Best-effort — a resolution or write failure is logged and swallowed so it never
    breaks a deploy.
    """
    commit = resolve_source_commit(repo_root)
    logger.info(f"archi source commit: {commit}")
    try:
        (Path(base_dir) / SOURCE_COMMIT_FILENAME).write_text(f"{commit}\n")
    except Exception as exc:  # best-effort: never fail the deploy on IO error
        logger.warning(f"Could not write SOURCE_COMMIT: {exc}")
    return commit
