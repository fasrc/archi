"""Guard against the dead ``duckdb`` pin creeping back into the requirements files.

Upstream (``archi-physics/archi``) still carries ``duckdb==0.8.1``, so a future
merge from upstream would silently reintroduce it. This module has no
module-level skip and no git dependency, unlike ``test_repo_hygiene.py``: it
reads tracked files that are always present next to the test, so the guard
runs wherever the suite does, including environments with no ``.git`` at all.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DUCKDB_PIN_PATHS = [
    REPO_ROOT / "requirements" / "requirements-base.txt",
    REPO_ROOT
    / "src"
    / "cli"
    / "templates"
    / "dockerfiles"
    / "base-python-image"
    / "requirements.txt",
    REPO_ROOT
    / "src"
    / "cli"
    / "templates"
    / "dockerfiles"
    / "base-pytorch-image"
    / "requirements.txt",
]

DUCKDB_PIN_PATTERN = re.compile(r"^\s*duckdb([=<>!~ ]|$)")


def test_no_duckdb_pin_in_requirements_files():
    """No requirements file may pin any duckdb version, at any distribution shape.

    Matches on the package name, not a specific version string, so
    ``duckdb==0.10.0`` or ``duckdb>=1.0`` are caught too. ``duckdb-engine`` is not
    a match: the character after ``duckdb`` is ``-``, which is neither in the
    character class nor end-of-line.
    """
    offenders = []
    for path in DUCKDB_PIN_PATHS:
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if DUCKDB_PIN_PATTERN.match(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")

    assert not offenders, (
        "duckdb pin found in requirements file(s): "
        f"{offenders}. Nothing in this project imports duckdb; upstream still "
        "carries this pin, so a merge from upstream reintroduced it. Delete the "
        "offending line(s)."
    )
