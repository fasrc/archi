"""Guard against the dead ``duckdb`` pin creeping back into the requirements files.

Upstream (``archi-physics/archi``) still carries ``duckdb==0.8.1``, so a future
merge from upstream would silently reintroduce it. This module has no
module-level skip and no git dependency, unlike ``test_repo_hygiene.py``: it
reads tracked files that are always present next to the test, so the guard
runs wherever the suite does, including environments with no ``.git`` at all.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

DUCKDB_PIN_PATHS = [
    REPO_ROOT / "requirements" / "requirements-base.txt",
    REPO_ROOT / "requirements" / "cpu-requirementsHEADER.txt",
    REPO_ROOT / "requirements" / "gpu-requirementsHEADER.txt",
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

# Everything that can terminate a project name in a requirement line: an extras
# bracket, an environment marker, a version specifier, a direct-reference ``@``,
# or whitespace.
_NAME_TERMINATOR_PATTERN = re.compile(r"[\[;=<>!~@\s]")
# PEP 503: runs of ``-``, ``_`` and ``.`` are equivalent and fold to a single ``-``.
_NAME_SEPARATOR_PATTERN = re.compile(r"[-_.]+")


def requirement_project_name(line):
    """Return the PEP 503-normalized project name ``line`` declares, else ``None``.

    Comments, blank lines and pip option lines (``-r``, ``--extra-index-url``)
    declare no project. Comparing normalized names — rather than peeking at the
    character after a literal prefix — is what makes ``DuckDB==1.0``,
    ``duckdb[httpfs]==1.0`` and ``duckdb; python_version >= "3.11"`` all resolve
    to the same distribution, while ``duckdb-engine`` stays distinct.
    """
    text = line.split("#", 1)[0].strip()
    if not text or text.startswith("-"):
        return None

    name = _NAME_TERMINATOR_PATTERN.split(text, 1)[0]
    if not name:
        return None

    return _NAME_SEPARATOR_PATTERN.sub("-", name).lower()


@pytest.mark.parametrize(
    "line",
    [
        "duckdb==0.8.1",
        "DuckDB==1.0",
        "DUCKDB>=1.0",
        "duckdb[httpfs]==1.0",
        'duckdb; python_version >= "3.11"',
        "duckdb @ https://example.invalid/duckdb-1.0-py3-none-any.whl",
        "  duckdb==1.0  ",
        "duckdb",
        "duckdb==1.0  # pinned for the analytics spike",
    ],
)
def test_duckdb_declarations_are_detected(line):
    """Every shape that installs the ``duckdb`` distribution must be caught."""
    assert requirement_project_name(line) == "duckdb"


@pytest.mark.parametrize(
    "line",
    [
        "duckdb-engine==0.13.0",
        "duckdb_engine==0.13.0",
        "pyduckdb==1.0",
        # PEP 503 folds separators to ``-``; it does not delete them, so
        # ``duck_db`` normalizes to ``duck-db`` — a different distribution.
        "duck_db==1.0",
        "# duckdb==0.8.1 (removed, see #246)",
        "--extra-index-url https://download.pytorch.org/whl/cpu",
        "-r requirements-base.txt",
        "",
        "   ",
    ],
)
def test_non_duckdb_declarations_are_not_detected(line):
    """A different distribution, a comment, or an option line is not a duckdb pin."""
    assert requirement_project_name(line) != "duckdb"


def test_guard_monitors_every_requirements_file_the_generator_touches():
    """The guard must cover the generator's inputs, not just its tracked outputs.

    ``scripts/dev/build_docker_images.sh`` concatenates the header files with
    ``requirements-base.txt`` to produce the two image requirements files. A
    ``duckdb`` line added to a header would leave the tracked outputs — and so
    this guard — green until the next base-image build regenerated them, which
    is exactly when it would be installed. Deriving the monitored set from the
    script keeps the guard honest if the generator is restructured.
    """
    script = REPO_ROOT / "scripts" / "dev" / "build_docker_images.sh"
    referenced = {
        REPO_ROOT / match
        for match in re.findall(
            r"\$ROOT_DIR/([A-Za-z0-9_./-]*requirements[A-Za-z0-9_./-]*\.txt)",
            script.read_text(),
        )
    }

    assert referenced, (
        f"No requirements paths found in {script.relative_to(REPO_ROOT)}. The "
        "generator was restructured; update this guard to match."
    )

    unmonitored = sorted(
        str(path.relative_to(REPO_ROOT)) for path in referenced - set(DUCKDB_PIN_PATHS)
    )
    assert not unmonitored, (
        f"{script.relative_to(REPO_ROOT)} reads or writes requirements file(s) "
        f"the duckdb guard does not scan: {unmonitored}. Add them to "
        "DUCKDB_PIN_PATHS."
    )


def test_no_duckdb_pin_in_requirements_files():
    """No requirements file may pin any duckdb version, at any distribution shape.

    Matches on the normalized project name, not a specific version string, so
    ``duckdb==0.10.0``, ``DuckDB>=1.0``, ``duckdb[httpfs]`` and a marker-guarded
    ``duckdb; python_version >= "3.11"`` are all caught. ``duckdb-engine`` is a
    different distribution and normalizes to a different name, so it is allowed.
    """
    offenders = []
    for path in DUCKDB_PIN_PATHS:
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if requirement_project_name(line) == "duckdb":
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")

    assert not offenders, (
        "duckdb pin found in requirements file(s): "
        f"{offenders}. Nothing in this project imports duckdb; upstream still "
        "carries this pin, so a merge from upstream reintroduced it. Delete the "
        "offending line(s)."
    )
