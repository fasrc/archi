"""Guard against the dead ``duckdb`` pin creeping back into the requirements files.

Upstream (``archi-physics/archi``) still carries ``duckdb==0.8.1``, so a future
merge from upstream would silently reintroduce it. This module has no
module-level skip and no git dependency, unlike ``test_repo_hygiene.py``: it
reads tracked files that are always present next to the test, so the guard
runs wherever the suite does, including environments with no ``.git`` at all.

The guard reads a file the way pip does. :func:`logical_lines` joins backslash
continuations first, because pip joins them before it parses anything, and a
directive split across two physical lines looks harmless on each one. Each
resulting logical line falls into one of three classes:

- **Named requirements** (``pkg==1.0``, ``pkg[extra]``, ``pkg @ URL``): readable,
  project name resolved via PEP 503 normalization.
- **Install directives** (``-e``/``--editable``, ``-r``/``--requirement``,
  ``-c``/``--constraint``): pull installable content without a readable project
  name, so they fail closed — reported as unreadable shapes. Every long-option
  abbreviation pip resolves counts, since ``--edit`` installs what ``--editable``
  installs. So does an option name holding a ``${VAR}`` the environment fills in,
  which the guard cannot prove is inert.
- **Inert option lines** (``--extra-index-url``, ``--index-url``, etc.) and
  comments/blanks: declare no requirement and are skipped. A ``${VAR}`` in an
  option's *value* — a token in an index URL — leaves the option inert.

Requirement and constraint includes (``-r``, ``-c``) are **reported rather than
followed** — none of the monitored files uses one today, and the failure message
tells a maintainer how to proceed.

Known boundary: the guard never expands environment variables and never opens an
include target, so it reports those shapes instead of resolving them. It reads
the five files in :data:`DUCKDB_PIN_PATHS` and nothing they point at.
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
# PEP 508 project-name grammar. A bare wheel URL, a ``git+https://`` reference or
# a local path fails this, which is how they are told apart from a named pin.
_PROJECT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")
# The three long directive names. pip's option parser accepts any prefix of a
# long option, so the guard matches every prefix rather than the full spelling:
# ``--edit git+...`` installs exactly what ``--editable git+...`` installs.
# Prefixes pip would reject as ambiguous (``--e``, ``--c``) are matched too —
# failing closed on a line pip refuses is the guard's job, not a false positive.
_INSTALL_DIRECTIVE_NAMES = ("editable", "requirement", "constraint")
_LONG_DIRECTIVE_ALTERNATION = "|".join(
    name[:length]
    for name in _INSTALL_DIRECTIVE_NAMES
    for length in range(len(name), 0, -1)
)
# pip install directives: -e/--editable, -r/--requirement, -c/--constraint.
# Space-separated, =-attached, and (for short forms) directly-attached values.
# Every other hyphen-led line is an inert option and stays invisible to the guard.
_INSTALL_DIRECTIVE_PATTERN = re.compile(
    r"^-[erc]([=\s]|$)"  # -e URL, -r file, -c file, -e=URL, bare -e
    r"|^-[erc]\S"  # attached short form: -e./vendor/duckdb
    rf"|^--({_LONG_DIRECTIVE_ALTERNATION})([=\s]|$)"
)
# Everything up to the first ``=`` or space: the option name, without its value.
_OPTION_NAME_PATTERN = re.compile(r"[=\s]")
# pip expands ``${VAR}`` from the environment before it parses a line, so an
# option name holding one cannot be proven inert — ``-${DIRECTIVE} extra.txt``
# becomes ``-r extra.txt`` when the build sets ``DIRECTIVE=r``. This is wider
# than pip's own ``[A-Z0-9_]+`` variable grammar on purpose: a guard that
# over-reads an option name fails closed, which is the safe direction.
_ENV_VAR_PATTERN = re.compile(r"\$\{[^}]*\}")
# pip joins a physical line ending in a backslash onto the next one, and a
# comment line ends the join. Mirrors pip's ``req_file.COMMENT_RE``.
_COMMENT_LINE_PATTERN = re.compile(r"(^|\s+)#.*$")


def _requirement_body(line):
    """Return the requirement text in ``line``, or ``None`` if it declares none.

    Comments and blank lines declare no requirement and return ``None``. Install
    directives (``-e``/``--editable``, ``-r``/``--requirement``,
    ``-c``/``--constraint``, including every long-option abbreviation pip
    accepts) pull installable content without a readable project name and return
    their text so the caller fails closed. An option name holding a ``${VAR}``
    the environment fills in also returns its text: the guard cannot prove such
    a name is inert. Inert pip option lines (``--extra-index-url``, etc.)
    declare no requirement and return ``None``. Requirement and constraint
    includes are reported, not followed recursively.

    This reads one logical line. Pass file content through :func:`logical_lines`
    first, or a directive split across a backslash continuation slips past.
    """
    text = line.split("#", 1)[0].strip()
    if not text:
        return None
    if _INSTALL_DIRECTIVE_PATTERN.match(text):
        return text
    if text.startswith("-"):
        option_name = _OPTION_NAME_PATTERN.split(text, 1)[0]
        if _ENV_VAR_PATTERN.search(option_name):
            return text
        return None
    return text


def declares_unreadable_requirement(line):
    """True when ``line`` installs something whose project name cannot be read.

    pip accepts bare archive, VCS and local-path requirements — a wheel URL, a
    ``git+https://host/duckdb.git#egg=duckdb``, a path into ``./vendor`` — which
    install a distribution without naming it in a form
    :func:`requirement_project_name` can resolve. Treating those as "not duckdb"
    would fail open, so the guard reports them and fails closed instead.

    Install directives (``-e``/``--editable``, ``-r``/``--requirement``,
    ``-c``/``--constraint``) also fail closed: they pull installable content
    without a readable project name. So does any abbreviation of those long
    names that pip resolves (``--edit``, ``--requirem``), and any option name
    built from an environment variable (``-${DIRECTIVE} extra.txt``). Inert
    option lines (``--extra-index-url``, etc.), comments and blanks are
    skipped — they declare no requirement. Requirement and constraint includes
    are reported, not followed recursively.
    """
    text = _requirement_body(line)
    if text is None:
        return False

    name = _NAME_TERMINATOR_PATTERN.split(text, 1)[0]
    return not _PROJECT_NAME_PATTERN.match(name)


def requirement_project_name(line):
    """Return the PEP 503-normalized project name ``line`` declares, else ``None``.

    Comments, blank lines and pip option lines (``-r``, ``--extra-index-url``)
    declare no project. Comparing normalized names — rather than peeking at the
    character after a literal prefix — is what makes ``DuckDB==1.0``,
    ``duckdb[httpfs]==1.0`` and ``duckdb; python_version >= "3.11"`` all resolve
    to the same distribution, while ``duckdb-engine`` stays distinct.

    Returns ``None`` for shapes that name no project — see
    :func:`declares_unreadable_requirement`, which is what stops those from
    passing as "not duckdb".
    """
    text = _requirement_body(line)
    if text is None:
        return None

    name = _NAME_TERMINATOR_PATTERN.split(text, 1)[0]
    if not _PROJECT_NAME_PATTERN.match(name):
        return None

    return _NAME_SEPARATOR_PATTERN.sub("-", name).lower()


def logical_lines(text):
    """Yield ``(lineno, line)`` for ``text`` with pip's continuations joined.

    pip joins a physical line that ends in a backslash onto the next one before
    it parses anything, so ``--edit\\`` followed by ``able git+...duckdb.git``
    is one ``--editable`` directive. A guard that reads physical lines sees an
    inert-looking option and a requirement apparently named ``able`` instead,
    and both of its checks pass while pip installs duckdb.

    The joined line keeps the number of its first physical line, and a comment
    line ends a join, both as pip's ``req_file.join_lines`` does.
    """
    primary_lineno = None
    parts = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        is_comment = _COMMENT_LINE_PATTERN.match(line)
        if not line.endswith("\\") or is_comment:
            if is_comment:
                line = " " + line
            if parts:
                parts.append(line)
                yield primary_lineno, "".join(parts)
                parts = []
            else:
                yield lineno, line
        else:
            if not parts:
                primary_lineno = lineno
            parts.append(line.strip("\\"))
    if parts:
        yield primary_lineno, "".join(parts)


def unreadable_requirement_lines(text):
    """Yield ``(lineno, line)`` for every line in ``text`` the guard cannot read.

    ``lineno`` is 1-based. A line joined from a backslash continuation reports
    the number of its first physical line, so a maintainer can find it.
    """
    for lineno, line in logical_lines(text):
        if declares_unreadable_requirement(line):
            yield lineno, line


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


@pytest.mark.parametrize(
    "line",
    [
        "https://example.invalid/duckdb-1.0.0-py3-none-any.whl",
        "git+https://example.invalid/duckdb.git#egg=duckdb",
        "./vendor/duckdb-1.0.0-py3-none-any.whl",
        "/opt/wheels/duckdb-1.0.0-py3-none-any.whl",
        "git+ssh://git@example.invalid/duckdb.git",
    ],
)
def test_unreadable_requirement_shapes_are_flagged(line):
    """A requirement pip would install but whose project name we cannot read.

    pip accepts bare archive, VCS and local-path requirements that install a
    distribution without naming it. ``requirement_project_name`` cannot resolve
    those to a project, so rather than let them pass as "not duckdb" the guard
    fails closed and demands a human look.
    """
    assert declares_unreadable_requirement(line)


@pytest.mark.parametrize(
    "line",
    [
        "-e git+https://host/duckdb.git#egg=duckdb",
        "--editable git+https://host/duckdb.git",
        "--editable=git+https://host/duckdb.git",
        "-e ./vendor/duckdb",
        "-e./vendor/duckdb",
        "-r extra-requirements.txt",
        "--requirement=extra-requirements.txt",
        "-c constraints.txt",
        "--constraint=constraints.txt",
    ],
)
def test_install_directives_are_flagged(line):
    """Install directives must fail closed — they pull content without a readable name."""
    assert declares_unreadable_requirement(line)
    assert requirement_project_name(line) is None


@pytest.mark.parametrize(
    "line",
    [
        "duckdb==1.0",
        "torch==2.6.0",
        "duckdb[httpfs]==1.0",
        'duckdb; python_version >= "3.11"',
        "duckdb @ https://example.invalid/duckdb-1.0-py3-none-any.whl",
        "--extra-index-url https://download.pytorch.org/whl/cpu",
        "# a comment",
        "",
    ],
)
def test_readable_requirement_shapes_are_not_flagged(line):
    """Named requirements, inert option lines, comments and blanks are all readable."""
    assert not declares_unreadable_requirement(line)


@pytest.mark.parametrize(
    "line",
    [
        # pip's optparse resolves an unambiguous long-option abbreviation, so
        # each of these installs or includes exactly as the full spelling does.
        "--edit git+https://host/duckdb.git#egg=duckdb",
        "--editabl=git+https://host/duckdb.git",
        "--e ./vendor/duckdb",
        "--requirem extra-requirements.txt",
        "--require extra-requirements.txt",
        "--r extra-requirements.txt",
        "--constrain constraints.txt",
        "--c constraints.txt",
    ],
)
def test_abbreviated_install_directives_are_flagged(line):
    """An abbreviated long directive installs the same content as the full name.

    pip accepts any prefix of a long option that resolves to one option, so
    matching only the full spellings leaves the abbreviations fail-open.
    """
    assert declares_unreadable_requirement(line)
    assert requirement_project_name(line) is None


@pytest.mark.parametrize(
    "line",
    [
        "-${DIRECTIVE} extra-requirements.txt",
        "--${DIRECTIVE} git+https://host/duckdb.git#egg=duckdb",
        "--${DIRECTIVE}=git+https://host/duckdb.git",
        "-${PREFIX}${SUFFIX} constraints.txt",
    ],
)
def test_variable_bearing_option_names_are_flagged(line):
    """An option name built from the environment cannot be proven inert.

    pip expands ``${VAR}`` from the environment before it parses the line, so
    ``-${DIRECTIVE} extra.txt`` becomes ``-r extra.txt`` when ``DIRECTIVE=r``.
    The guard cannot read the environment of every future build, so it fails
    closed on the shape instead of guessing the value.
    """
    assert declares_unreadable_requirement(line)
    assert requirement_project_name(line) is None


@pytest.mark.parametrize(
    "line",
    [
        # The variable sits in the value, not the option name, so the option
        # itself is still the inert one it says it is.
        "--extra-index-url https://${TOKEN}@pypi.example.invalid/simple",
        "--index-url https://${HOST}/simple",
        "--trusted-host ${HOST}",
    ],
)
def test_variables_in_inert_option_values_are_not_flagged(line):
    """Only the option name decides the class; a value may name anything."""
    assert not declares_unreadable_requirement(line)


@pytest.mark.parametrize(
    "line",
    [
        # Each of these shares a prefix with a directive or looks close to one,
        # and every one of them pulls in no installable content.
        "--require-hashes",
        "--extra-index-url https://download.pytorch.org/whl/cpu",
        "--index-url https://pypi.org/simple",
        "--find-links wheels/",
        "--trusted-host pypi.org",
        "--no-binary :all:",
        "--only-binary=:all:",
        "--prefer-binary",
        "--pre",
        "--hash=sha256:abc",
        "--cert /etc/ssl/ca.pem",
        "--client-cert /etc/ssl/client.pem",
        "--no-index",
    ],
)
def test_inert_options_stay_inert(line):
    """Widening the directive match must not start flagging inert option lines."""
    assert not declares_unreadable_requirement(line)


def test_logical_lines_joins_pip_continuations():
    """A physical line ending in a backslash is half of one logical line.

    pip's ``req_file.join_lines`` joins it onto the next line before it parses
    anything, and the joined line keeps the number of its first physical line.
    """
    text = "\n".join(
        [
            "torch==2.6.0",
            "--edit\\",
            "able git+https://host/duckdb.git#egg=duckdb",
            "duck\\",
            "db==1.0",
        ]
    )
    assert list(logical_lines(text)) == [
        (1, "torch==2.6.0"),
        (2, "--editable git+https://host/duckdb.git#egg=duckdb"),
        (4, "duckdb==1.0"),
    ]


def test_continued_install_directives_are_flagged_at_the_first_line():
    """A directive split across a continuation must fail closed, not read as a name.

    Read line by line, ``--edit\\`` looks like an inert option and ``able
    git+...`` looks like a requirement named ``able``: both guards pass while
    pip installs duckdb.
    """
    text = "\n".join(
        [
            "torch==2.6.0",
            "--edit\\",
            "able git+https://host/duckdb.git#egg=duckdb",
            "--require\\",
            "ment extra-requirements.txt",
        ]
    )
    assert list(unreadable_requirement_lines(text)) == [
        (2, "--editable git+https://host/duckdb.git#egg=duckdb"),
        (4, "--requirement extra-requirements.txt"),
    ]


def test_a_continuation_cannot_hide_a_duckdb_name():
    """Splitting the project name across lines must not hide the pin either."""
    text = "duck\\\ndb==1.0"
    names = [requirement_project_name(line) for _, line in logical_lines(text)]
    assert names == ["duckdb"]


def test_requirements_files_declare_only_readable_requirements():
    """Fail closed: every monitored line must be one the duckdb guard can read.

    Without this, a bare wheel URL or a ``git+https://...#egg=duckdb`` line
    would install duckdb while ``test_no_duckdb_pin_in_requirements_files``
    stayed green — the name check would simply find no name and move on. These
    files are plain pinned lists today; anything else is a shape change that a
    human should approve rather than something this guard should silently wave
    through.
    """
    unreadable = []
    for path in DUCKDB_PIN_PATHS:
        for lineno, line in unreadable_requirement_lines(path.read_text()):
            unreadable.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")

    assert not unreadable, (
        "Requirement line(s) whose project name the duckdb guard cannot read: "
        f"{unreadable}. Bare archive/VCS/local-path requirements can install "
        "duckdb without naming it, so the guard fails closed here. Either pin "
        "the dependency by name or extend the guard to understand this shape."
    )


def test_guard_monitors_every_requirements_file_the_generator_touches():
    """The guard must cover the generator's inputs, not just its tracked outputs.

    ``scripts/dev/build_docker_images.sh`` concatenates the header files with
    ``requirements-base.txt`` to produce the two image requirements files. A
    ``duckdb`` line added to a header would leave the tracked outputs — and so
    this guard — green until the next base-image build regenerated them, which
    is exactly when it would be installed.

    The comparison is **exact equality**, not "everything found is monitored".
    A subset check fails open: if the generator is rewritten to spell its paths
    some way this regex cannot see, the discovered set shrinks, the subset check
    still passes, and coverage silently evaporates. Requiring the sets to match
    turns any such restructuring into a loud failure that names the difference.
    It cannot see paths built from variables or command substitution, so it is a
    drift alarm rather than a proof of coverage.
    """
    script = REPO_ROOT / "scripts" / "dev" / "build_docker_images.sh"
    referenced = {
        REPO_ROOT / match
        for match in re.findall(
            r"\$\{?ROOT_DIR\}?/([A-Za-z0-9_./-]*requirements[A-Za-z0-9_./-]*\.txt)",
            script.read_text(),
        )
    }

    monitored = set(DUCKDB_PIN_PATHS)
    unmonitored = sorted(str(p.relative_to(REPO_ROOT)) for p in referenced - monitored)
    unreferenced = sorted(str(p.relative_to(REPO_ROOT)) for p in monitored - referenced)

    assert not unmonitored, (
        f"{script.relative_to(REPO_ROOT)} reads or writes requirements file(s) "
        f"the duckdb guard does not scan: {unmonitored}. Add them to "
        "DUCKDB_PIN_PATHS."
    )
    assert not unreferenced, (
        f"DUCKDB_PIN_PATHS lists file(s) {script.relative_to(REPO_ROOT)} no "
        f"longer appears to reference: {unreferenced}. Either the generator was "
        "restructured (so this guard's path discovery has gone blind and must be "
        "updated) or the file is genuinely gone (so drop it from the list). Do "
        "not silence this by deleting the path — that is how coverage is lost."
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
