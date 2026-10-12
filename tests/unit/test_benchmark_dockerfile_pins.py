"""The benchmark image's own pip steps must install pinned versions only (#681).

``Dockerfile-benchmarks`` and ``Dockerfile-benchmarks-gpu`` install archi with
``pip install .`` and then add the benchmark stack -- ``pandas``, ``ragas``,
``langchain-openai``, ``argilla``. pip resolves only the packages a call names, so those
steps floated their whole transitive closure and never enforced the upper bounds of
packages already in the image. On 2026-10-11 that killed every fresh build at startup:

- ``datasets`` 5.1.0 requires ``huggingface-hub>=1.31``, so pip moved ``huggingface_hub``
  0.36.2 -> 2.2.0, past the ``<1.0`` that ``transformers==4.53.3`` declares, and
  ``sentence_transformers`` could no longer be imported.
- ``pyarrow`` 26.0.0 raised ``ImportError: pyarrow requires NumPy 2.0 or newer, found
  1.26.4``. pyarrow declares no NumPy requirement at all, so no resolver can see that.

The fix is ``benchmark-constraints.txt``, applied with ``-c`` to every benchmark-only step.
This module is the static net around it: the steps carry the flag, the file holds exact
pins only, the pins that broke are present and compatible with the base image's pins,
and every place the benchmark image overrides a project pin is written down with its
measured cause.

Known boundary, the same one ``test_base_image_dependency_compatibility.py`` states: a
static net does not prove the set resolves or imports. The ``pyarrow`` failure is an
import-time fact no resolver reaches. The gate is a no-cache build of the image followed
by the import smoke and the completeness diff described in the constraints file's
header. No CI job builds this image, so that check is manual.
"""

import shlex
import tomllib
from pathlib import Path

import pytest

from tests.unit.test_base_image_dependency_compatibility import (
    _COMMENT,
    _MISSING,
    _joined_lines,
    _measured,
    _opaque_requirements,
    _parse_pins,
    _parse_requirements,
    _satisfies,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = REPO_ROOT / "src" / "cli" / "templates" / "dockerfiles"
BENCHMARK_DOCKERFILES = (
    DOCKERFILES / "Dockerfile-benchmarks",
    DOCKERFILES / "Dockerfile-benchmarks-gpu",
)
CONSTRAINTS = DOCKERFILES / "benchmark-constraints.txt"
BASE_REQUIREMENTS = REPO_ROOT / "requirements" / "requirements-base.txt"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# How the Dockerfiles name the file. The deployment build context receives ``src/`` as
# ``archi_code/`` (``TemplateManager.copy_source_code``), and ``COPY archi_code src``
# under ``WORKDIR /root/archi`` puts it back at this relative path inside the image.
CONSTRAINTS_ARG = "src/cli/templates/dockerfiles/benchmark-constraints.txt"

# The packages that broke on 2026-10-11, the package that forces most overrides below
# (``instructor``, an unpinned dependency of ``ragas``), and the two named requirements.
REQUIRED_PINS = frozenset(
    {"argilla", "datasets", "huggingface-hub", "instructor", "pyarrow", "ragas"}
)

# requirements-base.txt's ``transformers`` pin -> the ``huggingface-hub`` range it
# declares. Measured from the installed metadata of transformers 4.53.3 on 2026-10-11:
# ``huggingface-hub<1.0,>=0.30.0``. tokenizers 0.21.4 (``<1.0``) and
# langchain-huggingface 1.0.0 (``<1.0.0``) agree.
HUB_SPEC_BY_TRANSFORMERS = {"4.53.3": ">=0.30.0,<1.0"}

# requirements-base.txt's ``numpy`` pin -> the ``pyarrow`` range that imports with it.
# Not from requires_dist: pyarrow declares no numpy requirement. Measured by importing
# pyarrow in built benchmark images on 2026-10-11: 25.0.1 imports against numpy 1.26.4
# (benchmarking-prompt-sweep-r0-atoms:2000), and 26.0.0 raises "pyarrow requires NumPy
# 2.0 or newer, found 1.26.4".
PYARROW_SPEC_BY_NUMPY = {"1.26.4": "<26"}

# Every constraint pinned at a version other than its exact pin in requirements-base.txt
# or pyproject.toml, with the measured cause. The benchmark image cannot honour these
# pins: the benchmark stack requires otherwise. Measured from installed metadata in
# benchmarking-prompt-sweep-r0-atoms:2000 and the stage after ``pip install .`` on
# 2026-10-11. An entry without a measured cause does not belong here.
BENCHMARK_OVERRIDES = {
    "aiohttp": "instructor 1.17.0 requires aiohttp<4.0.0,>=3.14.3",
    "click": "instructor 1.17.0 requires click>=8.3.3",
    "idna": "instructor 1.17.0 requires idna>=3.15",
    "jinja2": "instructor 1.17.0 requires jinja2<4.0.0,>=3.1.4",
    "langchain-openai": "the Dockerfiles install langchain-openai==1.0.1 explicitly",
    "requests": (
        "instructor 1.17.0 requires requests<3.0.0,>=2.33.0; datasets 5.0.1 and "
        "langchain-community 0.4 require >=2.32.2 and >=2.32.5"
    ),
    "typer": (
        "typer 0.15.4 requires click<8.2,>=8.0.0 while instructor 1.17.0 requires "
        "click>=8.3.3, and ragas and instructor both require typer"
    ),
    "urllib3": "instructor 1.17.0 requires urllib3<3.0.0,>=2.7.0",
}


def _dockerfile_instructions(text: str) -> list:
    """Logical Dockerfile instructions: comment lines dropped, continuations joined.

    Docker strips comment lines before it joins a ``\\`` continuation, so a comment in
    the middle of a continued ``RUN`` does not end it.
    """
    instructions, buffered = [], ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            buffered += line[:-1] + " "
            continue
        instructions.append((buffered + line).strip())
        buffered = ""
    if buffered.strip():
        instructions.append(buffered.strip())
    return instructions


def _shell_commands(body: str) -> list:
    """Split a shell-form ``RUN`` body into commands at ``&&``, ``||``, ``;`` and ``|``.

    ``shlex`` in punctuation mode keeps quoted text whole, so ``'argilla>=2.5,<3'``
    stays one token. Unbalanced quoting raises ``ValueError``.
    """
    lexer = shlex.shlex(body, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    commands, current = [], []
    for token in lexer:
        if token in {"&&", "||", ";", "|"}:
            commands.append(current)
            current = []
        else:
            current.append(token)
    commands.append(current)
    return [command for command in commands if command]


def _pip_install_args(tokens: list):
    """The arguments after ``pip install``, or ``None`` when this is not a pip install.

    ``python -m pip install`` matches too, since its tokens contain ``pip install``.
    """
    for index in range(len(tokens) - 1):
        if tokens[index] in {"pip", "pip3"} and tokens[index + 1] == "install":
            return tokens[index + 2 :]
    return None


def _constraint_files(args: list) -> list:
    """Every constraints file a pip install argument list names, in any spelling."""
    files = []
    for index, arg in enumerate(args):
        if arg in {"-c", "--constraint"} and index + 1 < len(args):
            files.append(args[index + 1])
        elif arg.startswith("--constraint="):
            files.append(arg.split("=", 1)[1])
        elif arg.startswith("-c") and len(arg) > 2:
            files.append(arg[2:])
    return files


def unconstrained_benchmark_steps(text: str) -> list:
    """Problems with the benchmark-only pip steps of one Dockerfile.

    A benchmark-only step is any ``pip install`` after the one that installs ``.``. Each
    must name ``CONSTRAINTS_ARG`` with ``-c``. Shapes this reader does not parse are
    reported rather than skipped: any heredoc ``RUN`` (its commands sit on the lines
    after ``RUN``), an exec-form ``RUN`` that mentions pip, and unbalanced quoting. So is
    a file with no ``pip install .`` at all.
    """
    problems, package_installed = [], False
    for instruction in _dockerfile_instructions(text):
        keyword, _, body = instruction.partition(" ")
        body = body.strip()
        if keyword.upper() != "RUN":
            continue
        if "<<" in body:
            problems.append(f"unreadable RUN (heredoc): {instruction}")
            continue
        if "pip" not in body:
            continue
        if body.startswith("["):
            problems.append(f"unreadable RUN (exec form): {instruction}")
            continue
        try:
            commands = _shell_commands(body)
        except ValueError:
            problems.append(f"unreadable RUN (shell quoting): {instruction}")
            continue
        for tokens in commands:
            args = _pip_install_args(tokens)
            if args is None:
                continue
            if not package_installed:
                package_installed = "." in args
                continue
            if CONSTRAINTS_ARG not in _constraint_files(args):
                problems.append(f"unconstrained benchmark step: {' '.join(tokens)}")
    if not package_installed:
        problems.append("no `pip install .` step found")
    return problems


def constraints_problems(text: str) -> list:
    """Lines of a constraints file that are not an exact ``name==version`` pin.

    Read with the base-image guard's pip-faithful reader. Any option line fails,
    including ``-r``/``-c``/``-e`` includes: they would pull in content this module
    cannot read.
    """
    problems = []
    for joined_line in _joined_lines(text):
        line = _COMMENT.sub("", joined_line).strip()
        if line.startswith("-"):
            problems.append(f"option or include line: {line}")
    problems += [
        f"unreadable requirement: {line}" for line in _opaque_requirements(text)
    ]
    pins = _parse_pins(text)
    for name, spec in _parse_requirements(text).items():
        if name not in pins:
            problems.append(f"not an exact pin: {name}{spec}")
    return problems


def compatibility_problems(constraint_pins: dict, base_pins: dict) -> list:
    """Constraint pins that conflict with a measured fact about a base-image pin.

    An unmeasured base pin fails closed with a request to re-measure, the way
    ``_measured`` is used in the base-image guard.
    """
    problems = []
    for subject, keyed_by, table in (
        ("huggingface-hub", "transformers", HUB_SPEC_BY_TRANSFORMERS),
        ("pyarrow", "numpy", PYARROW_SPEC_BY_NUMPY),
    ):
        base_version = base_pins.get(keyed_by)
        if base_version is None:
            problems.append(
                f"requirements-base.txt no longer pins {keyed_by} exactly; re-measure "
                f"which {subject} it works with"
            )
            continue
        spec = _measured(table, base_version)
        if spec is _MISSING:
            problems.append(
                f"{keyed_by}=={base_version} is unmeasured; re-measure which {subject} "
                "it works with and record it"
            )
            continue
        pinned = constraint_pins.get(subject)
        if pinned is not None and not _satisfies(pinned, spec):
            problems.append(
                f"{subject}=={pinned} breaks {keyed_by}=={base_version} "
                f"(measured: {subject}{spec})"
            )
    return problems


def override_problems(
    constraint_pins: dict, project_pins: dict, overrides: dict
) -> list:
    """Unrecorded overrides of project pins, and recorded ones that no longer apply.

    ``project_pins`` maps a name to the set of exact versions requirements-base.txt and
    pyproject.toml pin it at.
    """
    diverging = {
        name: versions
        for name, versions in project_pins.items()
        if name in constraint_pins and versions - {constraint_pins[name]}
    }
    problems = [
        f"unrecorded override: {name}=={constraint_pins[name]} vs project pin "
        f"{', '.join(sorted(versions))}"
        for name, versions in sorted(diverging.items())
        if name not in overrides
    ]
    problems += [
        f"stale override entry: {name} no longer overrides a project pin"
        for name in sorted(overrides)
        if name not in diverging
    ]
    return problems


def _project_pins() -> dict:
    """Exact pins of requirements-base.txt and pyproject.toml, by normalized name."""
    pyproject = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    found = {}
    for pins in (
        _parse_pins(BASE_REQUIREMENTS.read_text()),
        _parse_pins("\n".join(pyproject)),
    ):
        for name, version in pins.items():
            found.setdefault(name, set()).add(version)
    return found


def _constraint_pins() -> dict:
    return _parse_pins(CONSTRAINTS.read_text())


# --- the real files ----------------------------------------------------------


def test_constraints_file_lives_in_the_staged_build_context():
    assert CONSTRAINTS.is_file(), f"missing {CONSTRAINTS.relative_to(REPO_ROOT)}"
    assert CONSTRAINTS.is_relative_to(REPO_ROOT / "src")
    assert (REPO_ROOT / CONSTRAINTS_ARG) == CONSTRAINTS


@pytest.mark.parametrize("dockerfile", BENCHMARK_DOCKERFILES, ids=lambda p: p.name)
def test_benchmark_dockerfiles_constrain_every_benchmark_step(dockerfile):
    assert unconstrained_benchmark_steps(dockerfile.read_text()) == []


def test_constraints_file_holds_only_exact_pins():
    assert constraints_problems(CONSTRAINTS.read_text()) == []


def test_constraints_pin_the_known_breakage_points():
    assert sorted(REQUIRED_PINS - set(_constraint_pins())) == []


def test_constraints_stay_compatible_with_the_base_image_pins():
    base_pins = _parse_pins(BASE_REQUIREMENTS.read_text())
    assert compatibility_problems(_constraint_pins(), base_pins) == []


def test_every_override_of_a_project_pin_is_recorded():
    problems = override_problems(
        _constraint_pins(), _project_pins(), BENCHMARK_OVERRIDES
    )
    assert problems == []


# --- each rule fires ---------------------------------------------------------

_PREFIX = "FROM base\nRUN pip install --upgrade pip && pip install .\n"


@pytest.mark.parametrize(
    "step",
    [
        f"RUN pip install -c {CONSTRAINTS_ARG} ragas==0.3.5",
        f"RUN pip install -c{CONSTRAINTS_ARG} ragas==0.3.5",
        f"RUN pip install --constraint={CONSTRAINTS_ARG} ragas==0.3.5",
        f"RUN python -m pip install --constraint {CONSTRAINTS_ARG} \\\n    'argilla>=2.5,<3'",
        "RUN pip check",
    ],
)
def test_constrained_or_non_install_steps_pass(step):
    assert unconstrained_benchmark_steps(_PREFIX + step + "\n") == []


@pytest.mark.parametrize(
    "step, expected",
    [
        ("RUN pip install 'argilla>=2.5,<3'", "unconstrained benchmark step"),
        ("RUN pip install -c other.txt ragas==0.3.5", "unconstrained benchmark step"),
        ("RUN true && pip install pandas", "unconstrained benchmark step"),
        ('RUN ["pip", "install", "ragas"]', "unreadable RUN"),
        ("RUN <<EOF\npip install ragas\nEOF", "unreadable RUN"),
        ("RUN pip install 'ragas", "unreadable RUN"),
    ],
)
def test_unconstrained_or_unreadable_steps_are_reported(step, expected):
    problems = unconstrained_benchmark_steps(_PREFIX + step + "\n")
    assert problems and expected in problems[0]


def test_a_dockerfile_without_the_package_install_is_reported():
    assert unconstrained_benchmark_steps("FROM base\nRUN pip install pandas\n") == [
        "no `pip install .` step found"
    ]


def test_steps_before_the_package_install_are_not_benchmark_steps():
    text = "FROM base\nRUN pip install --upgrade pip\nRUN pip install .\n"
    assert unconstrained_benchmark_steps(text) == []


@pytest.mark.parametrize(
    "line, expected",
    [
        ("datasets>=5.0", "not an exact pin"),
        ("datasets", "not an exact pin"),
        ("-r other.txt", "option or include line"),
        ("-c other.txt", "option or include line"),
        ("--index-url https://example.invalid/simple", "option or include line"),
        ("https://example.invalid/datasets-5.0.1.whl", "unreadable requirement"),
        ("${DATASETS}", "unreadable requirement"),
    ],
)
def test_non_pin_constraint_lines_are_reported(line, expected):
    problems = constraints_problems(f"# header\npyarrow==25.0.1\n{line}\n")
    assert any(expected in problem for problem in problems), problems


def test_exact_pins_and_comments_pass():
    text = "# why\npyarrow==25.0.1  # trailing note\nhuggingface_hub==0.36.2\n"
    assert constraints_problems(text) == []


_BASE = {"transformers": "4.53.3", "numpy": "1.26.4"}


@pytest.mark.parametrize(
    "constraint_pins, base_pins, expected",
    [
        ({"huggingface-hub": "2.2.0"}, _BASE, "breaks transformers==4.53.3"),
        ({"pyarrow": "26.0.0"}, _BASE, "breaks numpy==1.26.4"),
        ({}, {**_BASE, "transformers": "4.54.0"}, "transformers==4.54.0 is unmeasured"),
        ({}, {**_BASE, "numpy": "2.0.0"}, "numpy==2.0.0 is unmeasured"),
        ({}, {"numpy": "1.26.4"}, "no longer pins transformers"),
    ],
)
def test_incompatible_or_unmeasured_pins_are_reported(
    constraint_pins, base_pins, expected
):
    problems = compatibility_problems(constraint_pins, base_pins)
    assert any(expected in problem for problem in problems), problems


def test_compatible_pins_pass():
    pins = {"huggingface-hub": "0.36.2", "pyarrow": "25.0.1"}
    assert compatibility_problems(pins, _BASE) == []


def test_an_unrecorded_override_is_reported():
    problems = override_problems({"aiohttp": "3.14.3"}, {"aiohttp": {"3.9.5"}}, {})
    assert problems == ["unrecorded override: aiohttp==3.14.3 vs project pin 3.9.5"]


@pytest.mark.parametrize(
    "constraint_pins, project_pins",
    [
        ({"aiohttp": "3.9.5"}, {"aiohttp": {"3.9.5"}}),
        ({}, {"aiohttp": {"3.9.5"}}),
    ],
    ids=["now-equal", "no-longer-constrained"],
)
def test_a_stale_override_entry_is_reported(constraint_pins, project_pins):
    problems = override_problems(constraint_pins, project_pins, {"aiohttp": "cause"})
    assert problems == [
        "stale override entry: aiohttp no longer overrides a project pin"
    ]


def test_a_name_pinned_twice_differently_counts_as_an_override():
    pins = {"requests": {"2.31.0", "2.32.5"}}
    assert override_problems({"requests": "2.32.5"}, pins, {}) == [
        "unrecorded override: requests==2.32.5 vs project pin 2.31.0, 2.32.5"
    ]
